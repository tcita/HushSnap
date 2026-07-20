"""
PP-OCR Engine Implementation — parameter choices vs RapidOCR defaults.

Models
  Detection:  PP-OCRv6 SMALL (~10 MB) — language-agnostic, only locates text
              regions; does not recognise characters.
  Recognition: PP-OCRv6 SMALL (21 MB) — 50-language dictionary including
              Japanese, Traditional Chinese, and extended Unicode symbols.
  Classifier:  PP-OCRv4 (disabled — saves ~10 % latency, only needed for
              180° rotated images).

  Requires RapidOCR ≥ 3.9.1 (PP-OCRv6 model support).

Why SMALL det + SMALL rec
  ───────────────────────
  Det and rec are independent ONNX models — any det size can pair with any
  rec size.  The three PP-OCRv6 sizes (tiny/small/medium) differ only in
  channel width, not architecture; they share the same PPLCNetV4 backbone.

  Det choice — SMALL over TINY:
    TINY det underperforms on scripts beyond Simplified Chinese and English —
    most visibly it fragments vertical Japanese into many small square boxes
    that defeat column reconstruction.  SMALL det returns clean text regions
    across all supported scripts (incl. Traditional Chinese and Japanese), so
    it is used despite a small latency cost.  The detection-only Hmean
    benchmark (PaddleOCR) also favours SMALL det.

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

  Vertical CJK needs no rotation: PP-OCRv6 SMALL recognises upright CJK
  characters in vertical columns natively, so a 90° rotation (which loses
  5–20 % of CJK characters) is avoided.  Vertical text is detected via an
  area-weighted tall-box heuristic and rebuilt through a vertical-aware line
  builder.  Rotated/angled non-CJK text remains a model-level limitation.

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
import numpy as np

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
# Centre-distance ratio for greedy line/column clustering.  A box joins
# the current line when its centre is within 0.4 × median_height of the
# line's median centre (or 0.4 × median_width for vertical columns).
# This single font-agnostic condition replaces the previous two-condition
# (overlap + centre) approach — at 0.4 the overlap check is implied.
_CENTER_RATIO = 0.4
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


# -- Centre-distance line clustering ------------------------------------
# Greedy centre-distance clustering replaces the old recursive XY-Cut
# pipeline.  The single _CENTER_RATIO (0.4) governs all grouping
# decisions — no DPI-, font-size-, or line-spacing-dependent constants.
#
# Horizontal text: sort by y_top, greedily group into lines by centre
#   distance → sort lines top→bottom, within-line left→right.
# Vertical CJK text: sort by x_right descending, greedily group into
#   columns by centre distance → sort columns right→left, within-
#   column top→bottom.
#
# Design rationale — why a single centre-distance condition is enough.
#
# 1.  The previous two-condition approach (overlap + centre) was
#     redundant: at the 0.4 threshold, |Δcenter| < 0.4 × median_h
#     mathematically implies overlap_ratio > 0.5.  Dropping overlap
#     removes ~20 lines of ref-band / min()-denominator computation
#     with zero behavioural change.
#
#     The overlap-ratio check (the standard approach in layout analysis)
#     tests whether a candidate box sufficiently overlaps the reference
#     line's vertical band [M − ½H, M + ½H]:
#
#         overlap / min(box_h, H)  >  r      (e.g. r = 0.5)
#
#     The centre-distance gate tests a simpler condition:
#
#         |box.center_y − M|  <  k × H       (e.g. k = 0.4)
#
#     These two gates have a clean logical relationship.  Provided
#     k ≤ min(½, 1−r), whenever the centre-distance gate accepts a pair,
#     the overlap-ratio gate is *guaranteed* to accept it too.  In other
#     words, under this parameter regime centre-distance is a stricter
#     (more conservative) sufficient condition for overlap-ratio —
#     filtering by centre-distance never produces a false negative (it
#     won't reject a pair that overlap-ratio would accept), while
#     side-stepping overlap-ratio's sensitivity to detection-box
#     precision.
#
#     Our pair (k=0.4, r=0.5) satisfies 0.4 ≤ min(0.5, 0.5), so
#     centre-distance ⇒ overlap-ratio holds unconditionally.  If k is
#     ever raised past 0.5, the overlap-ratio check must be
#     reintroduced as a second gate.
#
# 2.  PP-OCR v6 small rarely fragments punctuation or CJK characters
#     into separate small boxes (verified on ~10 test images with
#     mixed Chinese/Latin/symbol text).  The "fragment-first" edge
#     case — a tiny detection box blocking subsequent normal boxes
#     from joining a line — can be triggered only with deliberate
#     special-symbol placement (™, •, ®).  Even then the fragmentation
#     is unstable: ±1 px viewport offset or 0.1× scale change reshapes
#     the fragment set entirely.  Optimising the denominator formula
#     for inputs that are not reproducible is not worthwhile.
#
# 3.  Zebra's word→line centreDistanceRatio (0.6) and height-averaged
#     denominator are designed for a pairwise (word vs word) model.
#     Our median-based (box vs line) model has different statistical
#     properties; the 0.4 threshold was tuned on real screenshots.
#     Importing Zebra's constants without Zebra's pairwise architecture
#     would be cargo-cult tuning.
#
# 4.  PP-OCR detection boxes are systematically taller than the actual
#     font-size (see scripts/measure_box_inflation.py).  Measured across
#     8–64 px, Latin + CJK, n ≈ 2700:
#
#         ratio = box_h / font_size
#         Latin:  median 1.23×   stdev 0.17×   p5–p95  1.00–1.56×
#         CJK:    median 1.19×   stdev 0.14×   p5–p95  1.00–1.43×
#         delta = box_h − font_size
#         Latin:  median +5.0 px           CJK:    median +4.0 px
#
#     The ratio is not a simple function of font-size — the within-size
#     variance (stdev ≈ 0.14–0.17×) dominates any systematic trend.
#     This means you cannot reliably recover the original typesetting
#     intent from box dimensions: box height is an unreliable proxy for
#     font-size, and therefore any gate built on box height (ref-band
#     width, overlap denominator) is gated by a quantity that is
#     systematically decoupled from the actual text.  Centre-distance
#     is structurally immune: the box centre is determined by the real
#     text position on the page, not by the detector's bounding-box
#     padding.  You don't need to know the font-size to know whether
#     two words sit on the same baseline.
#
#     So even if k were raised past 0.5 and overlap had to be
#     reintroduced as a second gate, the height-inflation problem would
#     make overlap a *less reliable* gate than centre-distance, not a
#     complementary one.  Centre-distance wins on two independent
#     grounds: it is mathematically sufficient (§1, no overlap needed
#     at k=0.4), and it does not depend on box-height estimates that
#     are systematically decoupled from font-size.
#
#     If the detector model is upgraded in the future, re-measure —
#     the ratio distribution above is model-specific.

# ---------------------------------------------------------------------------


def _normalize_blocks(blocks: list[dict]) -> list[dict]:
    """Convert raw PP-OCR detection blocks to internal representation; filter junk.

    Blocks without a valid bounding box are skipped — without real
    coordinates we cannot place them in reading order, and a fabricated
    box would distort line clustering.
    """
    empty_skipped = 0
    invalid_skipped = 0
    normalized: list[dict] = []
    for block in (blocks or []):
        raw_text = str(block.get("text", "") or "")
        # Filter out truly empty or whitespace-only blocks
        if not raw_text.strip():
            empty_skipped += 1
            continue

        left, top, right, bottom = ppocr_box_to_bbox(block.get("box"))
        w = right - left
        h = bottom - top
        if w <= 0 or h <= 0:
            # Block has text but no valid bounding box — skip it.
            # Without real coordinates we cannot place it in reading order,
            # and a fabricated box at (0,0) would distort line clustering.
            invalid_skipped += 1
            logger.debug("[DET] _normalize_blocks: skip invalid bbox (w=%.1f h=%.1f) %r",
                         w, h, raw_text[:80])
            continue
        normalized.append({
            "text": raw_text,
            "left": left, "top": top,
            "right": right, "bottom": bottom,
            "width": w, "height": h,
            "center_x": (left + right) / 2,
            "center_y": (top + bottom) / 2,
        })
    if empty_skipped or invalid_skipped:
        logger.debug("[DET] _normalize_blocks: %d blocks → %d valid (empty=%d invalid=%d)",
                     len(blocks or []), len(normalized), empty_skipped, invalid_skipped)
    return normalized


def _greedy_line_cluster(blocks: list[dict]) -> list[list[dict]]:
    """Greedy line clustering for horizontal LTR text.

    1. Sort boxes by y_top (top → bottom)
    2. Greedy: if box centre is close to the line's median centre,
       add to current line.
    3. Within each line: sort by x_left (left → right)
    4. Between lines: sort by average y_center (top → bottom)

    Single condition::

        abs(box.center_y - median_center) < 0.4 × median_height

    This centre-distance check alone is functionally equivalent to the
    previous two-condition (overlap + centre) approach: at the 0.4
    threshold the overlap check is mathematically implied and therefore
    redundant.
    """
    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda b: b["top"])

    lines: list[list[dict]] = []
    current: list[dict] = [sorted_blocks[0]]
    line_centers: list[float] = [sorted_blocks[0]["center_y"]]
    line_heights: list[float] = [sorted_blocks[0]["height"]]

    for box in sorted_blocks[1:]:
        sorted_centers = sorted(line_centers)
        n = len(sorted_centers)
        median_center = sorted_centers[n // 2]
        sorted_h = sorted(line_heights)
        median_h = sorted_h[n // 2]

        if abs(box["center_y"] - median_center) < 0.4 * median_h:
            current.append(box)
            line_centers.append(box["center_y"])
            line_heights.append(box["height"])
        else:
            lines.append(current)
            current = [box]
            line_centers = [box["center_y"]]
            line_heights = [box["height"]]

    lines.append(current)

    # Step 3: sort within each line left → right
    for line in lines:
        line.sort(key=lambda b: b["left"])

    # Step 4: sort lines top → bottom by average y_center
    lines.sort(key=lambda ln: sum(b["center_y"] for b in ln) / len(ln))

    logger.debug("[DET] _greedy_line_cluster: %d blocks → %d lines",
                 len(blocks), len(lines))
    for i, ln in enumerate(lines):
        ys = sorted({b["top"] for b in ln})
        texts = [b["text"][:30] for b in ln]
        logger.debug("[DET]   line[%d]: %d boxes  y_range=%s  texts=%s",
                     i, len(ln), ys[:6], texts)

    return lines


def _greedy_column_cluster(blocks: list[dict]) -> list[list[dict]]:
    """Greedy column clustering for vertical RTL text (CJK).

    Mirror of :func:`_greedy_line_cluster` with x/y roles swapped and
    sort directions reversed:

    1. Sort boxes by x_right descending (right → left)
    2. Greedy: if box centre is close to the column's median centre,
       add to current column.
    3. Within each column: sort by y_top (top → bottom)
    4. Between columns: sort by average x_center descending (right → left)

    Same single centre-distance condition as the line variant."""
    if not blocks:
        return []

    # Step 1: sort right → left
    sorted_blocks = sorted(blocks, key=lambda b: -b["right"])

    columns: list[list[dict]] = []
    current = [sorted_blocks[0]]
    col_centers: list[float] = [sorted_blocks[0]["center_x"]]
    col_widths: list[float] = [sorted_blocks[0]["width"]]

    for box in sorted_blocks[1:]:
        sorted_centers = sorted(col_centers)
        n = len(sorted_centers)
        median_center = sorted_centers[n // 2]
        sorted_w = sorted(col_widths)
        median_w = sorted_w[n // 2]

        if abs(box["center_x"] - median_center) < 0.4 * median_w:
            current.append(box)
            col_centers.append(box["center_x"])
            col_widths.append(box["width"])
        else:
            columns.append(current)
            current = [box]
            col_centers = [box["center_x"]]
            col_widths = [box["width"]]

    columns.append(current)

    # Step 3: sort within each column top → bottom
    for col in columns:
        col.sort(key=lambda b: b["top"])

    # Step 4: sort columns right → left by average x_center
    columns.sort(
        key=lambda col: -sum(b["center_x"] for b in col) / len(col)
    )

    logger.debug("[DET] _greedy_column_cluster: %d blocks → %d columns",
                 len(blocks), len(columns))
    for i, col in enumerate(columns):
        xs = sorted({b["left"] for b in col})
        texts = [b["text"][:30] for b in col]
        logger.debug("[DET]   column[%d]: %d boxes  x_range=%s  texts=%s",
                     i, len(col), xs[:6], texts)

    return columns


def _build_lines_from_clusters(
    clusters: list[list[dict]],
    is_vertical: bool = False,
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
                # ── inline gap spacing ──
                # Geometry is mirrored for vertical text: gap measured along
                # y (column stack) and normalised by box width, vs. x + box
                # height for horizontal.  Same ratio threshold & round rule -
                # a large visual gap between adjacent blocks becomes leading
                # spaces in the output regardless of orientation.
                if is_vertical:
                    gap = block["top"] - prev_block["bottom"]
                    est = min(prev_block["width"], block["width"])
                else:
                    gap = block["left"] - prev_block["right"]
                    est = min(prev_block["height"], block["height"])
                if est > 0:
                    gap_ratio = gap / est
                    if gap_ratio > 1.0:
                        n = max(1, round(gap_ratio))
                        sep = (sep or "") + (" " * n)
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


def _apply_paragraph_breaks(lines: list[OcrLine]) -> list[OcrLine]:
    """Insert a single blank line between lines whose vertical gap exceeds
    0.6× average line height.

    Only meaningful for horizontal text.  Gap magnitude beyond the threshold
    does not produce additional blank lines — there is no concept of
    "multi-level" paragraph spacing; a paragraph separator is binary.
    """
    if len(lines) <= 1:
        return lines

    heights = [ln.bounding_box.height for ln in lines if ln.bounding_box.height > 0]
    if not heights:
        return lines
    avg_h = sum(heights) / len(heights)
    threshold = avg_h * 0.6

    result: list[OcrLine] = []
    breaks = 0
    for i, line in enumerate(lines):
        result.append(line)
        if i < len(lines) - 1:
            cur_bottom = line.bounding_box.y + line.bounding_box.height
            next_top = lines[i + 1].bounding_box.y
            gap = next_top - cur_bottom
            if gap >= threshold:
                result.append(OcrLine(
                    text="", bounding_box=OcrBox(), paragraph_break=True,
                ))
                breaks += 1

    if breaks:
        logger.debug(
            "[DET] _apply_paragraph_breaks: avg_h=%.1f  %d blank lines inserted (%d lines → %d)",
            avg_h, breaks, len(lines), len(result),
        )

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
      5. Paragraph breaks (horizontal only: blank line when gap >= 1× line height)
      6. Indentation (horizontal only: left-edge clustering)

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
    lines = _build_lines_from_clusters(clusters, is_vertical=is_vertical)
    if not lines:
        return []

    # Step 4 - CJK spacing safety net (pangu-inspired regex)
    for line in lines:
        line.text = _apply_cjk_spacing(line.text)

    # Step 5 - paragraph breaks (horizontal text only)
    # Step 6 - detect indentation from left-edge clustering
    if not is_vertical:
        lines = _apply_paragraph_breaks(lines)
        lines = _apply_indentation(lines)

    return lines


def _apply_indentation(lines: list[OcrLine]) -> list[OcrLine]:
    """Apply leading spaces to indented lines.

    Baseline is the leftmost box edge.  Each line uses its own height as
    denominator: ``indent_ratio = (x - baseline) / line_height``.
    When the ratio exceeds 1.0 the line is considered intentionally
    indented; smaller offsets are treated as detection jitter.  Per-line
    thresholds handle mixed-height text correctly — a tall title with a
    small absolute offset is not falsely flagged, and a short body line
    with the same offset is properly recognised.

    The indent *unit* is the smallest non-jitter offset from baseline.
    Each line gets ``level × 4`` leading spaces, where ``level = round(offset / unit)``.
    Multi-level indentation (code, nested quotes, outlines) is handled
    without any per-language constants.
    """
    if len(lines) <= 1:
        return lines

    # Baseline: leftmost box — the body text / heading edge
    baseline = min(line.bounding_box.x for line in lines)

    def _is_indented(line: OcrLine) -> bool:
        """Offset exceeds jitter threshold relative to this line's own height."""
        h = line.bounding_box.height
        if h <= 0:
            return False
        indent_ratio = (line.bounding_box.x - baseline) / h
        return indent_ratio > 1.0

    # Indent unit: smallest offset that is clearly not jitter
    offsets = sorted(set(
        round(line.bounding_box.x) - baseline for line in lines
        if _is_indented(line)
    ))
    if not offsets:
        logger.debug("[DET] _apply_indentation: no offsets > per-line "
                     "threshold → no indent  (baseline=%.1f  n_lines=%d)",
                     baseline, len(lines))
        return lines
    unit = offsets[0]

    logger.debug("[DET] _apply_indentation: baseline=%.1f  indent_unit=%d px  "
                 "offsets=%s  n_lines=%d",
                 baseline, unit, offsets, len(lines))

    indented = 0
    for line in lines:
        if not _is_indented(line):
            continue
        offset = round(line.bounding_box.x) - baseline
        level = max(round(offset / unit), 1)
        line.text = ("    " * level) + line.text
        indented += 1
        logger.debug("[DET]   indent L%d: offset=%d px → level=%d (%d spaces)  %r",
                     indented, offset, level, level * 4, line.text[:60])

    if indented:
        logger.debug("[DET] _apply_indentation: %d/%d lines indented", indented, len(lines))

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
    ratio = tall_area / total_area
    is_vert = ratio >= _VERTICAL_WEIGHTED_THRESHOLD
    logger.debug("[DET] _is_vertical_json: tall_area_ratio=%.2f (threshold=%.2f) "
                 "total_area=%d → vertical=%s",
                 ratio, _VERTICAL_WEIGHTED_THRESHOLD, int(total_area), is_vert)
    return is_vert


# -- public API --------------------------------------------------------

def _recognize_without_detection(engine, arr) -> OcrRecognition:
    """Fallback: skip text detection and run recognition on the whole image.
    
    Includes automatic content cropping to handle large/padded images where 
    the text might be too small relative to the canvas for the recognizer's 
    fixed-height input window.
    """
    from ..constants import OCR_ENGINE_PPOCR

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
        # [:, :, :3] drops the X/A channel; .copy() makes it contiguous for ONNX.
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
            # DET debug: raw detection summary
            n_raw = len(json_data) if json_data else 0
            n_valid_raw = 0
            if json_data:
                for item in json_data:
                    box = item.get("box", [])
                    txt = item.get("txt", "") or ""
                    if box and txt.strip():
                        left, top, right, bottom = ppocr_box_to_bbox(box)
                        if right > left and bottom > top:
                            n_valid_raw += 1
            logger.debug("[DET] engine returned %d raw blocks (%d with valid box+text)",
                         n_raw, n_valid_raw)
            if json_data and n_valid_raw < n_raw:
                logger.debug("[DET] %d/%d blocks have invalid/missing box → will be filtered",
                             n_raw - n_valid_raw, n_raw)
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
        else:
            logger.debug("[DET] layout direction: horizontal (vertical not detected)")

        if not json_data:
            logger.debug("[DET] FALLBACK TRIGGERED: json_data empty → _recognize_without_detection()")
            logger.debug("[DET]   reason: PP-OCR detector found zero text regions")

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

        logger.debug("[DET] compose_ppocr_structures → %d OcrLines, %d chars total",
                     len(lines), len(text))
        for i, ln in enumerate(lines):
            b = ln.bounding_box
            logger.debug("[DET]   L%d: (%d,%d %dx%d) text=%r",
                         i, int(b.x), int(b.y), int(b.width), int(b.height),
                         ln.text[:80])

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
