"""
PP-OCR Engine Implementation — parameter choices vs RapidOCR defaults.

Models
  Detection:  PP-OCRv6 TINY  (1.8 MB) — language-agnostic, only locates text
              regions; does not recognise characters.
  Recognition: PP-OCRv6 SMALL (21 MB) — 50-language dictionary including
              Japanese, Traditional Chinese, and extended Unicode symbols.
  Classifier:  PP-OCRv4 (disabled — saves ~10 % latency, only needed for
              180° rotated images).

  Upgraded from PP-OCRv5 in Jul 2026 (RapidOCR ≥ 3.9.1).

Why TINY det + SMALL rec
  ───────────────────────
  Det and rec are independent ONNX models — any det size can pair with any
  rec size.  The three PP-OCRv6 sizes (tiny/small/medium) differ only in
  channel width, not architecture; they share the same PPLCNetV4 backbone.

  Det choice — TINY over SMALL:
    End-to-end benchmarks against SMALL det (both with SMALL rec) show TINY
    det is faster in most cases and matches or exceeds end-to-end accuracy on
    English, Chinese, and Japanese text.  PaddleOCR's official detection-only
    Hmean favours SMALL det, but that metric does not account for the downstream
    recogniser's behaviour on the resulting crops.

  Rec choice — SMALL over TINY:
    TINY rec (1.1M parameters) lacks Japanese entirely and shows significant
    errors on Traditional Chinese characters outside the simplified set.  It
    also confuses visually similar symbols (e.g. oxygen atom O vs digit 0)
    and degrades on superscripts/subscripts and uncommon Unicode.  On modern
    Simplified Chinese and English, TINY rec and SMALL rec are comparable.
    SMALL rec is chosen for full script coverage, not for speed.

Pipeline: det+rec → fallback rec-only.
  Detection provides reading-order layout (overlap-based clustering) for
  horizontal left-to-right and vertical right-to-left text.  When the
  detector finds no boxes, the engine falls back to recognition-
  only on the raw image (via _recognize_without_detection, which crops back to
  the original content area before recognising).

  Known limitation: PP-OCR is trained for horizontal LTR text.  Vertical text
  and multi-column layouts produce unreliable results — this is a model-level
  constraint, not a parameter choice.

  Pad-to-960 pre-processing was removed in Jul 2026.  The padding was originally
  introduced to work around RapidOCR v5's detector behaviour: when an image's
  short side is < limit_side_len (736), the detector upscales it to 736 px via
  cv2.resize.  For tiny images (e.g. 32 px short side) this ~23× upscale causes
  catastrophic interpolation blur that destroys character features, so padding
  to 960 px forced a 1∶1 scale.  With v6's PPLCNetV4 backbone the native tiny-
  image handling improved, but the fundamental issue remains: for wide-flat
  small images (short side < 48 px, aspect ratio ≥ 3∶1) the detector's forced
  upscale still degrades features.  However, the rec-only fallback on the raw
  pixels consistently outperforms detection in these cases down to ~15 px short
  side, so the simpler pipeline without padding is more reliable overall.

Parameter choices vs RapidOCR defaults
  ────────────────────────────────────
  Global.max_side_len = 1280 (default 2000)
      Verified against v6 small models (Jul 2026); remains the knee of the
      accuracy-vs-latency curve.  Values above 1600 degrade both.

  Rec.rec_batch_num = 1 (default 6)
      Recognition runs sequentially on CPU — batching only adds threading
      overhead without parallelism.

  intra_op_num_threads = 8 (default -1)
      U-curve bottoms at 8 threads on consumer CPUs.  Beyond that, scheduling
      overhead and cache contention overtake remaining throughput.

  inter_op_num_threads = 1 (default -1)
      Det → Rec pipeline is strictly sequential; inter-op parallelism has
      nothing to schedule.

  enable_cpu_mem_arena = False (ONNX default: True)
      ONNX's arena allocator pools large blocks without releasing them,
      keeping the working set at peak after OCR completes.  Disabling it
      lets the OS reclaim pages immediately — important for a long-running
      tray app.
"""

import logging
import re
import threading
import time

import cv2

# Import ppocr library at startup to ensure thread-safe loading of C/C++ extensions.
# Importing at module level (not inside functions / background threads) prevents
# Python 3.13 JIT + C-extension loader race conditions on the first OCR call.
from rapidocr import RapidOCR, OCRVersion, ModelType

# Alias kept for backward compatibility (tests monkeypatch ppocr_module.PPOCR).
PPOCR = RapidOCR

from PyQt6 import QtCore, QtGui

from .models import OcrBox, OcrLine, OcrRecognition, OcrWord
from .preprocess import OcrPreprocessResult
from ..system.memory_utils import get_working_set_mb, fmt_memory, trim_working_set

logger = logging.getLogger(__name__)

# ── Fallback / tuning constants ──────────────────────────────────────────────
# Overlap threshold for greedy line/column clustering.  Two boxes whose
# vertical (or horizontal) overlap exceeds 50 % of the shorter box's
# height (or width) are considered to belong to the same line (or column).
# This is a normalised, font-agnostic metric — the only layout threshold.
_OVERLAP_THRESHOLD = 0.5
# Minimum contrast range for recognition-without-detection fallback.
_MIN_CONTRAST_RANGE = 80

# ── Default PP-OCR engine parameters (documented in the module header) ───────
_DEFAULT_ENGINE_PARAMS: dict = {
    "Global.max_side_len": 1280,
    "Rec.rec_batch_num": 1,
    "EngineConfig.onnxruntime.intra_op_num_threads": 8,
    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    "EngineConfig.onnxruntime.enable_cpu_mem_arena": False,
}


# -- pure functions ----------------------------------------------------

def ppocr_box_to_bbox(box) -> tuple[float, float, float, float]:
    if not isinstance(box, list) or not box:
        return 0.0, 0.0, 0.0, 0.0

    points = []
    for point in box:
        if isinstance(point, list | tuple) and len(point) >= 2:
            points.append((float(point[0]), float(point[1])))
    if not points:
        return 0.0, 0.0, 0.0, 0.0

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_to_ocr_box(left: float, top: float, right: float, bottom: float) -> OcrBox:
    return OcrBox(x=left, y=top, width=max(0.0, right - left), height=max(0.0, bottom - top))


def is_cjk_or_fullwidth(character: str) -> bool:
    if not character:
        return False
    codepoint = ord(character)
    return (
        0x3000 <= codepoint <= 0x303F
        or 0x3040 <= codepoint <= 0x30FF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xFF00 <= codepoint <= 0xFFEF
    )


def word_separator(left: str, right: str) -> str:
    if not left or not right:
        return ""
    if is_cjk_or_fullwidth(left[-1]) and is_cjk_or_fullwidth(right[0]):
        return ""
    if left[-1] in "-—–…」』":
        return ""
    if right[0] in (
        ",.;:!?)]}"
        "，。、；：！？"
        "）】》〉〕｝"
        "…—–·"
        "\"'”’"
        "」』"
    ):
        return ""
    return " "


# -- Overlap-based line clustering --------------------------------------
# Greedy overlap-based clustering replaces the old recursive XY-Cut
# pipeline.  The single _OVERLAP_THRESHOLD (0.5) governs all grouping
# decisions — no DPI-, font-size-, or line-spacing-dependent constants.
#
# Horizontal text: sort by y_top, greedily group into lines by vertical
#   overlap → sort lines top→bottom, within-line left→right.
# Vertical CJK text: sort by x_right descending, greedily group into
#   columns by horizontal overlap → sort columns right→left, within-
#   column top→bottom.

# ---------------------------------------------------------------------------


def _normalize_blocks(blocks: list[dict]) -> list[dict]:
    """Convert raw PP-OCR detection blocks to internal representation; filter junk.

    Blocks without a valid bounding box get a minimal placeholder so their
    text is preserved in the output (the detector may occasionally return
    text without proper box coordinates in edge cases).
    """
    normalized: list[dict] = []
    for block in (blocks or []):
        raw_text = str(block.get("text", "") or "")
        # Filter out truly empty or whitespace-only blocks
        if not raw_text.strip():
            continue
            
        left, top, right, bottom = ppocr_box_to_bbox(block.get("box"))
        w = right - left
        h = bottom - top
        if w <= 0 or h <= 0:
            # Block has text but no valid bounding box — skip it.
            # Without real coordinates we cannot place it in reading order,
            # and a fabricated box at (0,0) would distort line clustering.
            logger.debug("PP-OCR: skipping block with invalid bbox: %r", raw_text[:80])
            continue
        normalized.append({
            "text": raw_text,
            "left": left, "top": top,
            "right": right, "bottom": bottom,
            "width": w, "height": h,
            "center_x": (left + right) / 2,
            "center_y": (top + bottom) / 2,
        })
    return normalized


def _greedy_line_cluster(
    blocks: list[dict], threshold: float = _OVERLAP_THRESHOLD,
) -> list[list[dict]]:
    """Greedy line clustering for horizontal LTR text.

    1. Sort boxes by y_top (top → bottom)
    2. Greedy: if box overlaps current line's y-range by > *threshold*,
       add to current line and update y-range to union.
    3. Within each line: sort by x_left (left → right)
    4. Between lines: sort by average y_center (top → bottom)

    The overlap ratio is *intersection over min-height*::

        overlap = max(0, min(bottom_a, bottom_b) − max(top_a, top_b))
        ratio   = overlap ÷ min(height_a, height_b)

    A box whose vertical overlap with the line's representative y-range
    exceeds 50 % of the shorter side is considered to share the same
    baseline — a font-agnostic decision that holds across CJK, Latin,
    and mixed scripts.

    .. note::
       The union of ``[line_top, line_bottom]`` grows monotonically as
       boxes are added, which could theoretically bridge adjacent lines
       if a box is much taller than the rest of the line (e.g. a vertical
       bracket or large inline graphic).  No guard is added because the
       algorithm's design boundary is clean single-orientation screenshots:
       in that regime every box on a line has roughly the same height,
       and inter-line spacing dominates intra-line height variance, so
       the union never reaches the next line.
    """
    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda b: b["top"])

    lines: list[list[dict]] = []
    current = [sorted_blocks[0]]
    line_top = sorted_blocks[0]["top"]
    line_bottom = sorted_blocks[0]["bottom"]

    for box in sorted_blocks[1:]:
        overlap = max(0.0, min(line_bottom, box["bottom"]) - max(line_top, box["top"]))
        min_h = min(line_bottom - line_top, box["height"])
        ratio = overlap / min_h if min_h > 0 else 0.0

        if ratio > threshold:
            current.append(box)
            line_top = min(line_top, box["top"])
            line_bottom = max(line_bottom, box["bottom"])
        else:
            lines.append(current)
            current = [box]
            line_top = box["top"]
            line_bottom = box["bottom"]

    lines.append(current)

    # Step 3: sort within each line left → right
    for line in lines:
        line.sort(key=lambda b: b["left"])

    # Step 4: sort lines top → bottom by average y_center
    lines.sort(key=lambda ln: sum(b["center_y"] for b in ln) / len(ln))

    return lines


def _greedy_column_cluster(
    blocks: list[dict], threshold: float = _OVERLAP_THRESHOLD,
) -> list[list[dict]]:
    """Greedy column clustering for vertical RTL text (CJK).

    The mirror of _greedy_line_cluster with x/y roles swapped and sort
    directions reversed:

    1. Sort boxes by x_right descending (right → left)
    2. Greedy: if box overlaps current column's x-range by > *threshold*,
       add to current column and update x-range to union.
    3. Within each column: sort by y_top (top → bottom)
    4. Between columns: sort by average x_center descending (right → left)

    .. note::
       The same bridging caveat as :func:`_greedy_line_cluster` applies:
       the column x-range union grows monotonically, but in clean
       single-orientation screenshots every box in a column has roughly
       equal width, so the union never reaches the next column.
    """
    if not blocks:
        return []

    # Step 1: sort right → left
    sorted_blocks = sorted(blocks, key=lambda b: -b["right"])

    columns: list[list[dict]] = []
    current = [sorted_blocks[0]]
    col_left = sorted_blocks[0]["left"]
    col_right = sorted_blocks[0]["right"]

    for box in sorted_blocks[1:]:
        overlap = max(0.0, min(col_right, box["right"]) - max(col_left, box["left"]))
        min_w = min(col_right - col_left, box["width"])
        ratio = overlap / min_w if min_w > 0 else 0.0

        if ratio > threshold:
            current.append(box)
            col_left = min(col_left, box["left"])
            col_right = max(col_right, box["right"])
        else:
            columns.append(current)
            current = [box]
            col_left = box["left"]
            col_right = box["right"]

    columns.append(current)

    # Step 3: sort within each column top → bottom
    for col in columns:
        col.sort(key=lambda b: b["top"])

    # Step 4: sort columns right → left by average x_center
    columns.sort(
        key=lambda col: -sum(b["center_x"] for b in col) / len(col)
    )

    return columns


def _build_lines_from_clusters(
    clusters: list[list[dict]],
) -> list[OcrLine]:
    """Convert clustered blocks into OcrLine objects.

    Each cluster (a line for horizontal text, or a column for vertical
    CJK) becomes one OcrLine.  Blocks within a cluster are already sorted
    in reading order (left→right for horizontal, top→bottom for vertical).
    """
    result: list[OcrLine] = []
    for cluster in clusters:
        text_parts: list[str] = []
        words: list[OcrWord] = []
        prev_block = None

        min_l = min(b["left"] for b in cluster)
        min_t = min(b["top"] for b in cluster)
        max_r = max(b["right"] for b in cluster)
        max_b = max(b["bottom"] for b in cluster)

        for block in cluster:
            if prev_block:
                sep = word_separator(prev_block["text"], block["text"])
                if sep:
                    text_parts.append(sep)
            text_parts.append(block["text"])
            words.append(OcrWord(
                text=block["text"],
                bounding_box=bbox_to_ocr_box(
                    block["left"], block["top"],
                    block["right"], block["bottom"],
                ),
            ))
            prev_block = block

        result.append(OcrLine(
            text="".join(text_parts).rstrip(),
            words=words,
            bounding_box=bbox_to_ocr_box(min_l, min_t, max_r, max_b),
        ))

    return result


# -- CJK spacing post-processing (core patterns from pangu.py) ----------
# Applied as a final safety net: PP-OCR sometimes merges CJK+Latin into
# a single detection block, so block-level word_separator() misses those
# boundaries.  These two regexes catch them.
# Reference: https://github.com/vinta/pangu.py (MIT licensed)
#
# CJK Unicode blocks (verified code points):
#   CJK Radicals Supplement       \u2E80-\u2EFF
#   Kangxi Radicals               \u2F00-\u2FDF
#   Hiragana                      \u3040-\u309F
#   Katakana                      \u30A0-\u30FF
#   Katakana/Hiragana marks       \u30FB-\u30FF
#   Bopomofo                      \u3100-\u312F
#   Enclosed CJK Letters          \u3200-\u32FF
#   CJK Extension A               \u3400-\u4DBF
#   CJK Unified Ideographs        \u4E00-\u9FFF
#   CJK Compatibility             \uF900-\uFAFF
_CJK_RANGES = (
    r'\u2E80-\u2EFF'
    r'\u2F00-\u2FDF'
    r'\u3040-\u309F'
    r'\u30A0-\u30FF'
    r'\u30FB-\u30FF'
    r'\u3100-\u312F'
    r'\u3200-\u32FF'
    r'\u3400-\u4DBF'
    r'\u4E00-\u9FFF'
    r'\uF900-\uFAFF'
)

# Non-CJK (ANS) character class matched on the other side of the boundary:
#   A-Z a-z          Latin letters
#   \u0370-\u03FF    Greek and Coptic
#   0-9              digits
#   @$%^&*\-+\\=\|/  common symbols
#   \u00A1-\u00FF    Latin-1 Supplement
#   \u2150-\u218F    Number Forms
#   \u2700-\u27BF    Dingbats
_ANS_CLASS = (
    r'A-Za-z'
    r'\u0370-\u03FF'
    r'0-9'
    r'@$%^&*\-+\\=\|/'
    r'\u00A1-\u00FF'
    r'\u2150-\u218F'
    r'\u2700-\u27BF'
)

# CJK followed by ANS -> insert space
_CJK_ANS_RE = re.compile(f'([{_CJK_RANGES}])([{_ANS_CLASS}])')

# ANS followed by CJK -> insert space
_ANS_CJK_RE = re.compile(
    f'([{_ANS_CLASS}~!;:,./?])([{_CJK_RANGES}])'
)


def _apply_cjk_spacing(text: str) -> str:
    """Ensure a single space between CJK and Latin characters.

    Idempotent: will not double-space text that already has correct spacing.

    URLs are exempted: the pangu-style CJK↔Latin spacers would otherwise
    insert a space at every CJK/Latin boundary *inside* a URL (e.g.
    ``kw=测试页面&fr=pb`` → ``kw= 测试页面 &fr=pb``), and the link highlighter —
    which stops at whitespace — would then only colour the fragment up to the
    first inserted space.  Spacing is applied only to the non-URL runs.
    """
    from .text import apply_outside_urls

    if not text:
        return text

    def _space_runs(s: str) -> str:
        s = _CJK_ANS_RE.sub(r'\1 \2', s)
        s = _ANS_CJK_RE.sub(r'\1 \2', s)
        return s

    return apply_outside_urls(text, _space_runs)


# -- public API ------------------------------------------------------------


def compose_ppocr_structures(blocks: list[dict], is_vertical: bool = False) -> list[OcrLine]:
    """Convert PP-OCR detection blocks into ordered OcrLines.

    Pipeline::

      1. Normalize raw blocks (filter empty / zero-size)
      2. Greedy overlap-based clustering → reading order
      3. Build OcrLine objects from clusters
      4. Post-process CJK-Latin spacing (pangu-style safety net)

    When *is_vertical* is True, the image contains predominantly vertical
    CJK text (tall boxes, h > w × 1.3).  Column clustering detects
    vertical columns; reading order is right→left, top→bottom.
    """
    # Step 1 - normalize
    normalized = _normalize_blocks(blocks)
    if not normalized:
        return []

    # Step 2 - overlap-based clustering into reading order
    if is_vertical:
        clusters = _greedy_column_cluster(normalized)
    else:
        clusters = _greedy_line_cluster(normalized)

    # Step 3 - build OcrLine objects from clusters
    lines = _build_lines_from_clusters(clusters)
    if not lines:
        return []

    # Step 4 - CJK spacing safety net (pangu-inspired regex)
    for line in lines:
        line.text = _apply_cjk_spacing(line.text)

    # Step 5 - detect indentation from left-edge clustering
    # (horizontal text only — indentation is meaningless for vertical text)
    if not is_vertical:
        lines = _apply_indentation(lines)

    return lines


def _apply_indentation(lines: list[OcrLine]) -> list[OcrLine]:
    """Apply leading spaces to indented lines.

    Baseline is the leftmost box edge.  Jitter threshold is
    0.5× average line height — smaller differences are detection noise,
    not indentation.

    The indent *unit* is the smallest non-jitter offset from baseline.
    Each line gets ``level × 4`` leading spaces, where ``level = round(offset / unit)``.
    Multi-level indentation (code, nested quotes, outlines) is handled
    without any per-language constants.
    """
    if len(lines) <= 1:
        return lines

    # Baseline: leftmost box — the body text / heading edge
    baseline = min(line.bounding_box.x for line in lines)

    heights = [line.bounding_box.height for line in lines
               if line.bounding_box.height > 0]
    if not heights:
        return lines
    avg_h = sum(heights) / len(heights)
    threshold = avg_h * 0.5

    # Indent unit: smallest offset that is clearly not jitter
    offsets = sorted(set(
        round(line.bounding_box.x) - baseline for line in lines
        if round(line.bounding_box.x) - baseline > threshold
    ))
    if not offsets:
        return lines
    unit = offsets[0]

    for line in lines:
        offset = line.bounding_box.x - baseline
        if offset > threshold:
            level = max(round(offset / unit), 1)
            line.text = ("    " * level) + line.text

    return lines


def compose_ppocr_text(blocks: list[dict]) -> str:
    """Compatibility wrapper that returns plain text string."""
    lines = compose_ppocr_structures(blocks)
    return "\n".join(line.text for line in lines).rstrip()


# -- engine singleton ---------------------------------------------------

_engine = None
_engine_lock = threading.Lock()
_active_requests = 0
_active_requests_cv = threading.Condition()
_engine_params_override: dict | None = None


def set_engine_params_override(params: dict | None):
    """Override engine parameters for the next engine creation.

    Forces release of the existing engine singleton so that the new
    parameters take effect on the next OCR call.  Pass ``None`` to
    revert to production defaults.

    Intended for benchmarking / A/B testing — the production path never
    calls this function.
    """
    global _engine_params_override, _engine
    _engine_params_override = dict(params) if params else None
    with _engine_lock:
        _engine = None
    logger.info("[PPOCR] Engine params override set: %s", _engine_params_override)


def _trim_working_set():
    """Trim the process working set once OCR is done.

    gc.collect() before the OS call was benchmarked and provides no
    additional benefit: SetProcessWorkingSetSize(-1, -1) already
    swaps out every page regardless of Python GC state.
    """
    # Guard against trimming while a recognition request is actively running
    # in another thread. Trimming during active inference causes heavy paging
    # lag (thrashing) as the OS swaps model data back into RAM immediately.
    with _active_requests_cv:
        if _active_requests > 0:
            logger.debug("[PPOCR] Skipping _trim_working_set: %d active requests", _active_requests)
            return

    before_mb = get_working_set_mb()
    logger.debug("[PPOCR] _trim_working_set: before trim  %s", fmt_memory())

    res = trim_working_set()

    after_mb = get_working_set_mb()
    if res:
        logger.debug("[PPOCR] _trim_working_set: after  trim  %s (delta=%.1f MB)",
                     fmt_memory(), after_mb - before_mb)
    else:
        logger.warning("[PPOCR] trim_working_set failed. %s", fmt_memory())


def _acquire_request():
    global _active_requests
    with _active_requests_cv:
        _active_requests += 1


def _release_request():
    global _active_requests
    with _active_requests_cv:
        _active_requests -= 1
        _active_requests_cv.notify_all()


def _get_engine() -> "PPOCR":
    global _engine
    if _engine is None:
        logger.debug("[PPOCR] _get_engine: Initializing new engine instance...")
        with _engine_lock:
            if _engine is None:
                ws_before = get_working_set_mb()
                logger.info("[PPOCR] Initializing engine singleton (models loading)...")
                
                # Optimized for desktop CPU inference (see module header for details)
                # CLS (direction classifier) disabled by default — only useful
                # for 180° flipped images (e.g. phone held upside-down).
                # Saves ~10% latency + ~6 MB memory with no accuracy impact
                # on correctly-oriented or slightly tilted input.
                params = {
                    "Det.ocr_version": OCRVersion.PPOCRV6,
                    "Det.model_type": ModelType.SMALL,
                    "Rec.ocr_version": OCRVersion.PPOCRV6,
                    "Rec.model_type": ModelType.SMALL,
                    "Global.use_cls": False,
                    **_DEFAULT_ENGINE_PARAMS,
                }
                if _engine_params_override:
                    params.update(_engine_params_override)
                    logger.info("[PPOCR] Applying engine params override: %s",
                                {k: v for k, v in _engine_params_override.items()})
                _engine = PPOCR(params=params)
                
                ws_after = get_working_set_mb()
                logger.debug(
                    "[PPOCR] Engine created. %s (delta=%.1f MB)",
                    fmt_memory(), ws_after - ws_before,
                )
    return _engine


def release_engine():
    """Release the PP-OCR engine singleton to free memory.

    Waits for in-flight requests, tears down ONNX sessions, then forces
    garbage collection and a working-set trim. The engine is lazily
    re-initialized on the next OCR call.
    """
    global _engine

    ws_entry = get_working_set_mb()
    logger.debug("[PPOCR] release_engine: entry  %s", fmt_memory())

    with _active_requests_cv:
        while _active_requests > 0:
            _active_requests_cv.wait()

        with _engine_lock:
            if _engine is None:
                logger.debug("[PPOCR] release_engine: engine already None, skipping")
                return
            # Let CPython's reference counting clean up ONNX sessions naturally
            _engine = None

    ws_before_trim = get_working_set_mb()
    logger.debug("[PPOCR] release_engine: after del, before trim  %s (delta from entry=%.1f MB)",
                 fmt_memory(), ws_before_trim - ws_entry)

    _trim_working_set()

    ws_exit = get_working_set_mb()
    logger.debug("[PPOCR] release_engine: exit  %s (total delta=%.1f MB)",
                 fmt_memory(), ws_exit - ws_entry)


# -- vertical text ordering -----------------------------------------------

# -- text orientation detection -------------------------------------------

_VERTICAL_BOX_RATIO = 1.3          # h/w threshold for a "tall" (vertical) text box
_VERTICAL_WEIGHTED_THRESHOLD = 0.5  # weighted tall-area fraction to trigger rotation


def _is_vertical_json(json_data: list[dict]) -> bool:
    """Return True if text is predominantly vertical (tall boxes by area).

    Uses area-weighted voting: each box votes for "vertical" or "horizontal"
    in proportion to its pixel area.  Small fragments (stray chars, labels)
    contribute negligible weight (< 1 % of a real text column), so no
    explicit noise-filter threshold is needed — the weighting is inherently
    robust to outliers.

    This replaces the earlier ad-hoc approach of filtering by a percentage
    of the largest box's area (5 %) with an absolute floor (500 px²), which
    required tuning per image resolution.
    """
    if not json_data:
        return False

    total_area = 0.0
    tall_area = 0.0
    for item in json_data:
        box = item.get("box", [])
        if not box:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if w <= 0:
            continue
        area = w * h
        total_area += area
        if h > w * _VERTICAL_BOX_RATIO:
            tall_area += area

    if total_area == 0.0:
        return False
    return (tall_area / total_area) >= _VERTICAL_WEIGHTED_THRESHOLD


# -- public API --------------------------------------------------------

def _recognize_without_detection(engine, arr) -> OcrRecognition:
    """Fallback: skip text detection and run recognition on the whole image.
    
    Includes automatic content cropping to handle large/padded images where 
    the text might be too small relative to the canvas for the recognizer's 
    fixed-height input window.
    """
    from ..constants import OCR_ENGINE_PPOCR
    import cv2
    import numpy as np

    orig_det = engine.use_det
    orig_cls = engine.use_cls
    try:
        # 1. Smart Content Crop: Find the actual text area to avoid destructive downscaling
        # Sample background from the top-left corner
        h, w = arr.shape[:2]
        gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        bg_val = int(gray[0, 0])
        # Find pixels significantly different from background
        mask = cv2.absdiff(gray, bg_val) > 20
        coords = np.column_stack(np.where(mask))
        
        if coords.size > 0:
            y0, x0 = coords.min(axis=0)
            y1, x1 = coords.max(axis=0)
            # Add a small 2px margin for safety
            y0, x0 = max(0, y0-2), max(0, x0-2)
            y1, x1 = min(h-1, y1+2), min(w-1, x1+2)
            arr = arr[y0:y1+1, x0:x1+1].copy()
            logger.debug("Fallback: auto-cropped to content area %dx%d", x1-x0, y1-y0)

        # 2. Pre-recognition enhancement: Normalize contrast
        gray_crop = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        min_v, max_v, _, _ = cv2.minMaxLoc(gray_crop)
        if max_v - min_v < _MIN_CONTRAST_RANGE:
            arr = cv2.normalize(arr, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX)

        _acquire_request()
        try:
            rec_result = engine(arr, use_det=False, use_cls=False)
        finally:
            _release_request()

        txts = getattr(rec_result, "txts", None)
        if not txts or not txts[0] or not txts[0].strip():
            logger.debug("PP-OCR recognition-only fallback returned no text")
            return OcrRecognition(
                engine_type=OCR_ENGINE_PPOCR,
            )

        recognized_text = txts[0].strip()
        logger.debug("PP-OCR recognition-only fallback succeeded: %r", recognized_text)

        return OcrRecognition(
            text=recognized_text,
            engine_type=OCR_ENGINE_PPOCR,
        )
    finally:
        engine.use_det = orig_det
        engine.use_cls = orig_cls


def recognize_ppocr_qimage(image_or_result, language_tag: str = "") -> OcrRecognition:
    from .preprocess import OcrPreprocessResult
    from ..constants import OCR_ENGINE_PPOCR

    if isinstance(image_or_result, OcrPreprocessResult):
        image = image_or_result.image
        original_size = image_or_result.original_size
    elif isinstance(image_or_result, QtGui.QImage):
        image = image_or_result
        original_size = image.size()
    else:
        # Handle QPixmap or other types (fallback to manual conversion)
        from .preprocess import prepare_ocr_image
        image = prepare_ocr_image(image_or_result)
        original_size = image.size()

    if image.isNull():
        return OcrRecognition(engine_type=OCR_ENGINE_PPOCR)

    # Defense: the buffer math below assumes devicePixelRatio == 1.0.
    # image.width()/height() return *logical* dimensions, while image.bits()
    # is the *physical* pixel buffer — at DPR 2.0 a 960x540 logical QImage
    # has a 1920x1080 buffer, and the reshape((height, width, 4)) would read
    # only a quarter of it, silently corrupting OCR. The normal pipeline
    # (run_minimal_pipeline → prepare_ocr_image) normalizes DPR to 1.0, but
    # this is a public entry point; a caller passing a raw high-DPR QImage
    # would hit the mismatch. Normalize here when needed. convertToFormat is
    # only called in the off-normal case, so the happy path pays no copy.
    if image.devicePixelRatio() != 1.0:
        image = image.convertToFormat(QtGui.QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(1.0)
        original_size = image.size()

    # Pre-declare to ensure cleanup in 'finally' doesn't fail
    result = None
    json_data = None
    arr = None
    bgr_image = None
    
    try:
        logger.debug("[ANCHOR] IMAGE_CONVERT_START")
        import numpy as np
        # Callers must pass a QImage whose format stores [B, G, R] in the
        # first three bytes on this platform (RGB32 / ARGB32 / ARGB32_PM on
        # little-endian).  prepare_ocr_image() in preprocess.py guarantees
        # RGB32, and ARGB32 is byte-identical for the first 3 channels.
        # convertToFormat is omitted because it would add a redundant copy
        # (~1.3 ms) with no effect on the BGR slice below.
        width = image.width()
        height = image.height()
        ptr = image.bits()
        ptr.setsize(image.sizeInBytes())
        # [:, :, :3] drops the X/A channel → contiguous BGR array for ONNX.
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4))[:, :, :3].copy()
        logger.debug("[ANCHOR] IMAGE_CONVERT_END")

        _acquire_request()
        try:
            engine = _get_engine()
            logger.debug("[ANCHOR] INFERENCE_START")
            result = engine(arr)
            logger.debug("[ANCHOR] INFERENCE_END")
            if hasattr(result, "elapse_list"):
                logger.debug("[ANCHOR] ELAPSE_DETAIL: %s", result.elapse_list)
            json_data = result.to_json()
        finally:
            _release_request()

        # ── vertical CJK layout ──────────────────────────────────────────
        # PP-OCRv6 SMALL handles upright CJK characters in vertical columns
        # natively — rotating 90° CCW is counterproductive (empirically:
        # rotation introduces garbage detections, merges short text, and
        # loses 5–20 % of CJK characters).  Instead, detect vertical text
        # with the area-weighted tall-box heuristic and route through a
        # vertical-aware line builder that respects column boundaries.
        is_vertical = _is_vertical_json(json_data) if json_data else False
        if is_vertical:
            logger.debug("PP-OCR: vertical CJK text detected — using vertical layout")

        if not json_data:
            logger.debug("PP-OCR detection returned empty - falling back to recognition-only")

            if width > original_size.width() or height > original_size.height():
                y_off = (height - original_size.height()) // 2
                x_off = (width - original_size.width()) // 2
                fallback_arr = arr[y_off : y_off + original_size.height(),
                                   x_off : x_off + original_size.width()].copy()
            else:
                fallback_arr = arr

            final_res = _recognize_without_detection(engine, fallback_arr)
            return final_res

        blocks = [{"text": item["txt"], "box": item["box"]} for item in json_data]
        lines = compose_ppocr_structures(blocks, is_vertical=is_vertical)
        text = "\n".join(line.text for line in lines).rstrip()

        return OcrRecognition(
            text=text,
            lines=lines,
            engine_type=OCR_ENGINE_PPOCR,
        )
    except Exception:
        logger.exception("PP-OCR engine call failed")
        return OcrRecognition(engine_type=OCR_ENGINE_PPOCR)
    finally:
        # Explicit GC: ONNX Runtime allocates large native buffers whose
        # lifetime Python ref-counting cannot fully track.  Without GC,
        # repeated OCR calls leak private bytes and kernel handles within
        # a handful of iterations.
        #
        # _trim_working_set() is deliberately NOT called here — trimming
        # while OCR is still active would thrash (swap out model pages
        # only to fault them back on the next call).  Trim belongs to
        # IdleMemoryManager (idle ≥20 s).
        import gc
        del result, json_data, arr, bgr_image
        gc.collect()



def recognize_ppocr_result_from_pixmap(
    image_or_result,
    language_tag: str = "",
) -> OcrRecognition:
    """PP-OCR engine entry point. Receives a preprocessed QImage or OcrPreprocessResult."""
    if isinstance(image_or_result, QtGui.QImage):
        if image_or_result.isNull():
            return OcrRecognition()
    elif isinstance(image_or_result, OcrPreprocessResult):
        if image_or_result.image.isNull():
            return OcrRecognition()

    return recognize_ppocr_qimage(image_or_result, language_tag=language_tag)


def warmup_ppocr():
    """Pre-initialize the PP-OCR engine singleton to avoid cold-start latency."""
    ws_before = get_working_set_mb()
    t0 = time.perf_counter()
    logger.debug("[PPOCR] warmup_ppocr: start  %s", fmt_memory())
    try:
        _get_engine()
        elapsed = (time.perf_counter() - t0) * 1000
        ws_after = get_working_set_mb()
        logger.debug(
            "[PPOCR] warmup_ppocr: done  %s (delta=%.1f MB, took %.1fms)",
            fmt_memory(), ws_after - ws_before, elapsed,
        )
    except Exception:
        logger.exception("PP-OCR engine warmup failed")


# Register PP-OCR engine
from .engine import register_engine  # noqa: E402
from ..constants import OCR_ENGINE_PPOCR  # noqa: E402
register_engine(
    OCR_ENGINE_PPOCR,
    recognize=recognize_ppocr_result_from_pixmap,
    release=release_engine,
    trim=_trim_working_set,
    warmup=warmup_ppocr,
    metadata={
        "display_name": "PP-OCR",
        "error_prefixes": [],
    },
)
