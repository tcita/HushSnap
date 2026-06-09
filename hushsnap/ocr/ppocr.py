import logging
import re
import statistics
import threading
import time

# Defer ppocr library import to optimize application startup time
from PyQt6 import QtCore, QtGui
PPOCR = None
OCRVersion = None

from .models import OcrBox, OcrLine, OcrRecognition, OcrWord
from .preprocess import OcrPreprocessResult
from ..system.memory_utils import get_working_set_mb, fmt_memory, trim_working_set

logger = logging.getLogger(__name__)


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
    if left[-1] == "-":
        return ""
    if right[0] in ",.;:!?)]}，。；：！？）】》":
        return ""
    return " "


# -- XY-Cut layout engine -----------------------------------------------
# Hierarchical recursive XY-Cut with adaptive per-region thresholds.
#
# Design (synthesised from three canonical variants):
#   1. CC-based XY-Cut   (Ha, Haralick & Phillips 1995) - bbox input, recursive alternation
#   2. ARXYC              (Sylwester 2001)              - gap-ratio threshold, locally adaptive
#   3. Augmented XY-Cut   (Gu et al. 2022)              - sorted-adjacency gap detection
#
# Thresholds are multiples of local character metrics -> DPI- and font-agnostic.

# Gap thresholds expressed as multiples of local character metrics.
# These are unitless ratios - they scale automatically with DPI and font size.
_GAP_RATIO_H_REGION = 2.5   # Y-gap > 2.5x char_h -> horizontal region (header/body/footer)
_GAP_RATIO_H_LINE   = 0.4   # Y-gap > 0.4x char_h -> text line separator
_GAP_RATIO_V_COLUMN = 3.5   # X-gap > 3.5x char_w -> column separator

# Minimum gap in pixels to avoid splitting on sub-pixel noise
_MIN_GAP_PX = 2.0

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
            # Block has text but no valid bbox - give it a minimal placement
            w, h = 1.0, 1.0
        normalized.append({
            "text": raw_text,
            "left": left, "top": top,
            "right": right, "bottom": bottom,
            "width": max(w, 0.0), "height": max(h, 0.0),
            "center_x": (left + right) / 2,
            "center_y": (top + bottom) / 2,
        })
    return normalized


def _region_metrics(blocks: list[dict]) -> dict:
    """Compute per-region adaptive metrics (median height / width)."""
    heights = [b["height"] for b in blocks]
    widths = [b["width"] for b in blocks]
    return {
        "med_h": statistics.median(heights) if heights else 15.0,
        "med_w": statistics.median(widths) if widths else 15.0,
    }


def _detect_gaps(
    sorted_blocks: list[dict], direction: str, threshold: float
) -> list[tuple[int, float]]:
    """Find gaps between adjacent blocks along *direction* that exceed *threshold*."""
    gaps: list[tuple[int, float]] = []
    for i in range(len(sorted_blocks) - 1):
        curr = sorted_blocks[i]
        nxt = sorted_blocks[i + 1]
        if direction == "y":
            gap = nxt["center_y"] - curr["center_y"]
        else:
            gap = nxt["left"] - curr["right"]
        if gap > threshold:
            gaps.append((i, gap))
    return gaps


def _leaf_reading_order(
    blocks: list[dict], came_from: str
) -> list[dict]:
    """Sort blocks within a terminal (leaf) region for reading order.

    Vertical CJK text (e.g. sidebars, traditional documents)::

      * Single column - only top->bottom order matters (column direction irrelevant)
      * Multiple columns - traditional convention: right->left, top->bottom

    Horizontal text (default): top->bottom, left->right.
    """
    if len(blocks) <= 1:
        return list(blocks)

    # -- detect vertical CJK ------------------------------------------
    tall_count = sum(1 for b in blocks if b["height"] > b["width"] * 1.3)
    is_vertical_cjk = len(blocks) >= 3 and tall_count / len(blocks) > 0.5

    if is_vertical_cjk:
        # Are blocks clustered at a single X position or spread across columns?
        x_centers = sorted(b["center_x"] for b in blocks)
        x_span = x_centers[-1] - x_centers[0]
        widths = [b["width"] for b in blocks]
        med_w = statistics.median(widths) if widths else 15.0

        if x_span > med_w * 2.0:
            # Multi-column vertical CJK -> traditional right->left column order.
            blocks.sort(key=lambda b: b["center_x"])

            columns: list[list[dict]] = []
            for block in blocks:
                if not columns:
                    columns.append([block])
                    continue
                last = columns[-1]
                avg_cx = sum(b["center_x"] for b in last) / len(last)
                avg_w = sum(b["width"] for b in last) / len(last)
                if (
                    abs(block["center_x"] - avg_cx)
                    <= max(avg_w, block["width"]) * 0.8
                ):
                    last.append(block)
                else:
                    columns.append([block])

            # Sort columns right->left (larger center_x = further right)
            columns.sort(
                key=lambda col: -sum(b["center_x"] for b in col) / len(col)
            )

            # Sort top->bottom within each column
            result: list[dict] = []
            for col in columns:
                col.sort(key=lambda b: b["center_y"])
                result.extend(col)
            blocks = result
        else:
            # Single-column vertical -> top->bottom only (X order irrelevant)
            blocks.sort(key=lambda b: b["center_y"])
    else:
        # Standard horizontal: top->bottom, left->right
        blocks.sort(key=lambda b: (b["center_y"], b["left"]))

    return blocks


def _xy_cut(
    blocks: list[dict], direction: str, depth: int = 0
) -> list[dict]:
    """Recursive XY-Cut: partition blocks by alternating Y/X projection gaps.

    Returns blocks in reading order:

    * Y-cut first (top-level) -> separate horizontal regions (header / body / footer)
    * X-cut within each region -> separate columns
    * Y-cut within each column -> separate text lines
    * Terminal: sort for reading direction (handles CJK vertical text)
    """
    if len(blocks) <= 1:
        return list(blocks)

    m = _region_metrics(blocks)
    med_h = m["med_h"]
    med_w = m["med_w"]

    # -- gap threshold: coarse at top level, fine at deeper levels ----
    if direction == "y":
        threshold = med_h * (
            _GAP_RATIO_H_REGION if depth == 0 else _GAP_RATIO_H_LINE
        )
    else:
        # X direction: column gaps only (never split individual words)
        threshold = med_w * _GAP_RATIO_V_COLUMN

    threshold = max(threshold, _MIN_GAP_PX)

    # -- sort + detect gaps -------------------------------------------
    if direction == "y":
        sorted_blocks = sorted(blocks, key=lambda b: (b["center_y"], b["left"]))
    else:
        sorted_blocks = sorted(blocks, key=lambda b: (b["left"], b["center_y"]))

    gaps = _detect_gaps(sorted_blocks, direction, threshold)

    if not gaps:
        # Terminal: no significant gaps -> leaf region
        return _leaf_reading_order(sorted_blocks, direction)

    # Split at the largest gap
    gaps.sort(key=lambda g: -g[1])
    split_idx, _best_gap = gaps[0]

    group1 = sorted_blocks[:split_idx + 1]
    group2 = sorted_blocks[split_idx + 1:]

    next_dir = "x" if direction == "y" else "y"

    result: list[dict] = []
    result.extend(_xy_cut(group1, next_dir, depth + 1))
    result.extend(_xy_cut(group2, next_dir, depth + 1))
    return result


def _build_lines_from_ordered_blocks(
    ordered_blocks: list[dict],
) -> list[OcrLine]:
    """Group reading-order blocks into OcrLine objects via center_y proximity.

    Within each line, blocks are joined left->right using *word_separator*
    (one space or nothing, based on character-class boundaries).
    Geometric-gap spacing has been intentionally removed - it was fragile
    across varying DPI, font sizes, and OCR detection granularities.
    """
    if not ordered_blocks:
        return []

    heights = [b["height"] for b in ordered_blocks]
    med_h = statistics.median(heights) if heights else 15.0

    # Group consecutive blocks whose center_y is within 0.6x median height
    line_groups: list[list[dict]] = []
    current_line = [ordered_blocks[0]]

    for block in ordered_blocks[1:]:
        avg_y = sum(b["center_y"] for b in current_line) / len(current_line)
        avg_h = sum(b["height"] for b in current_line) / len(current_line)
        if abs(block["center_y"] - avg_y) < avg_h * 0.6:
            current_line.append(block)
        else:
            line_groups.append(current_line)
            current_line = [block]

    line_groups.append(current_line)

    # Build OcrLine objects - simple character-class spacing only
    result: list[OcrLine] = []
    for group in line_groups:
        group.sort(key=lambda b: b["left"])
        text_parts: list[str] = []
        words: list[OcrWord] = []
        prev_block = None

        min_l = min(b["left"] for b in group)
        min_t = min(b["top"] for b in group)
        max_r = max(b["right"] for b in group)
        max_b = max(b["bottom"] for b in group)

        for block in group:
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
    """
    if not text:
        return text
    text = _CJK_ANS_RE.sub(r'\1 \2', text)
    text = _ANS_CJK_RE.sub(r'\1 \2', text)
    return text


def _separate_paragraphs(lines: list[OcrLine]) -> list[OcrLine]:
    """Insert a trailing blank-line marker on lines whose Y-gap to the
    next line exceeds 1.6x median line height (simple paragraph boundary)."""
    if len(lines) <= 1:
        return list(lines)

    heights = [l.bounding_box.height for l in lines if l.bounding_box.height > 0]
    if not heights:
        return list(lines)
    med_h = statistics.median(heights)

    for i in range(1, len(lines)):
        prev = lines[i - 1]
        curr = lines[i]
        prev_bottom = prev.bounding_box.y + prev.bounding_box.height
        curr_top = curr.bounding_box.y
        gap = curr_top - prev_bottom

        if gap > med_h * 1.6:
            lines[i - 1].text = lines[i - 1].text.rstrip() + "\n"

    return lines


# -- public API ------------------------------------------------------------


def compose_ppocr_structures(blocks: list[dict]) -> list[OcrLine]:
    """Convert PP-OCR detection blocks into ordered OcrLines.

    Pipeline::

      1. Normalize raw blocks (filter empty / zero-size)
      2. Hierarchical recursive XY-Cut -> reading order
      3. Group ordered blocks into OcrLine objects (center_y proximity)
      4. Separate paragraphs by Y-gap threshold
      5. Post-process CJK-Latin spacing (pangu-style safety net)
    """
    # Step 1 - normalize
    normalized = _normalize_blocks(blocks)
    if not normalized:
        return []

    # Step 2 - recursive XY-Cut -> ordered blocks in reading order
    ordered = _xy_cut(normalized, direction="y", depth=0)

    # Step 3 - group into OcrLine objects (character-class spacing only)
    lines = _build_lines_from_ordered_blocks(ordered)
    if not lines:
        return []

    # Step 4 - simple paragraph separation by Y-gap
    lines = _separate_paragraphs(lines)

    # Step 5 - CJK spacing safety net (pangu-inspired regex)
    for line in lines:
        line.text = _apply_cjk_spacing(line.text)

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
                global PPOCR, OCRVersion
                if PPOCR is not None:
                    local_ppocr = PPOCR
                else:
                    logger.debug("[PPOCR] Importing PP-OCR library...")
                    from rapidocr import RapidOCR as local_ppocr
                if OCRVersion is not None:
                    local_OCRVersion = OCRVersion
                else:
                    logger.debug("[PPOCR] Importing OCRVersion...")
                    from rapidocr import OCRVersion as local_OCRVersion
                
                ws_before = get_working_set_mb()
                logger.info("[PPOCR] Initializing engine singleton (models loading)...")
                
                # PRODUCTION OPTIMIZED PROFILE (Empirically validated via Benchmark)
                # This configuration provides the best speed-to-memory ratio for CPU inference.
                #
                # 1. Global.max_side_len (1536): Limits Detector input resolution.
                #    Benchmarked on full-screen screenshot (2560×1600 px, 4.1 Mpx,
                #    1707×1067 logical @ 1.5 DPR) via python -m hushsnap.benchmark
                #    -n 3 -p (warm-iteration data, UI overhead suppressed):
                #
                #       max_side_len | WS peak  | Pvt peak | Latency  | AUC (norm)
                #       -------------|---------|----------|---------|-----------
                #       1536 (prod)  | 508 MB  | 1186 MB  | 2018 ms  | 70.5 MB
                #       2000 (Rapi-  | 705 MB  | 1372 MB  | 2131 ms  | 89.8 MB
                #          dOCR def) |  (+39%) |  (+16%)  |  (+6%)   |  (+27%)
                #       3072 (no     | 977 MB  | 1643 MB  | 3913 ms  | 150.6 MB
                #          limit)    |  (+92%) |  (+39%)  |  (+94%)  |  (+114%)
                #
                #    1536 vs RapidOCR default 2000: saves ~197 MB WS (-28%),
                #    ~113 ms latency (-5%). Text output differed by ≤10 chars
                #    (~0.16% of 6300+ total), limited to toolbar micro-text;
                #    body text identical across all three settings.
                # 2. Rec.rec_batch_num (1): Sequential recognition. Counter-intuitively faster
                #    on CPU than batching (e.g. 4 or 10) by reducing ONNX overhead and
                #    improving cache locality. Reduces peak memory by ~100MB.
                # 3. intra_op_num_threads (8): Optimal thread saturation for modern CPUs.
                #    Faster than 4 threads and more stable than using all cores (-1).
                # 4. enable_cpu_mem_arena (False): Disables ONNX memory pooling to ensure
                #    transient buffers are returned to the OS immediately, preventing creep.
                params = {
                    "Det.ocr_version": local_OCRVersion.PPOCRV5,
                    "Rec.ocr_version": local_OCRVersion.PPOCRV5,
                    "Cls.ocr_version": local_OCRVersion.PPOCRV5,
                    "Global.max_side_len": 1536,
                    "Rec.rec_batch_num": 1,
                    "EngineConfig.onnxruntime.intra_op_num_threads": 8,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                    "EngineConfig.onnxruntime.enable_cpu_mem_arena": False,
                }
                if _engine_params_override:
                    params.update(_engine_params_override)
                    logger.info("[PPOCR] Applying engine params override: %s",
                                {k: v for k, v in _engine_params_override.items()})
                _engine = local_ppocr(params=params)
                
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
        if max_v - min_v < 80:
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
                requested_language_supported=True,
                engine_language_tag="zh-CN",
                engine_type=OCR_ENGINE_PPOCR,
            )

        recognized_text = txts[0].strip()
        logger.debug("PP-OCR recognition-only fallback succeeded: %r", recognized_text)

        return OcrRecognition(
            text=recognized_text,
            requested_language_supported=True,
            engine_language_tag="zh-CN",
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

    # Pre-declare to ensure cleanup in 'finally' doesn't fail
    result = None
    json_data = None
    arr = None
    bgr_image = None
    
    try:
        logger.info("[ANCHOR] IMAGE_CONVERT_START")
        import numpy as np
        # Use RGB32 because it's always 4-byte aligned, avoiding padding/stride issues
        # that break the NumPy reshape. In little-endian, RGB32 is actually BGRX.
        bgr_image = image.convertToFormat(QtGui.QImage.Format.Format_RGB32)
        width = bgr_image.width()
        height = bgr_image.height()
        ptr = bgr_image.bits()
        ptr.setsize(bgr_image.sizeInBytes())
        # Slice [:, :, :3] converts BGRA to BGR, then copy() makes it contiguous for ONNX
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4))[:, :, :3].copy()
        logger.info("[ANCHOR] IMAGE_CONVERT_END")

        _acquire_request()
        try:
            engine = _get_engine()
            logger.info("[ANCHOR] INFERENCE_START")
            result = engine(arr)
            logger.info("[ANCHOR] INFERENCE_END")
            if hasattr(result, "elapse_list"):
                logger.info("[ANCHOR] ELAPSE_DETAIL: %s", result.elapse_list)
            json_data = result.to_json()
        finally:
            _release_request()

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
        lines = compose_ppocr_structures(blocks)
        text = "\n".join(line.text for line in lines).rstrip()
        
        return OcrRecognition(
            text=text,
            lines=lines,
            requested_language_supported=True,
            engine_language_tag="zh-CN",
            engine_type=OCR_ENGINE_PPOCR,
        )
    except Exception:
        logger.exception("PP-OCR engine call failed")
        return OcrRecognition(engine_type=OCR_ENGINE_PPOCR)
    finally:
        # Crucial: Explicitly trigger GC after inference to prevent peak accumulation
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
