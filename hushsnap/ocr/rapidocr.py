import logging
import statistics
import threading
import time

# Defer rapidocr import to optimize application startup time
from PyQt6 import QtCore, QtGui
RapidOCR = None
OCRVersion = None

from .models import OcrBox, OcrLine, OcrRecognition, OcrWord
from .preprocess import OcrPreprocessResult
from ..system.memory_utils import get_working_set_mb, fmt_memory

logger = logging.getLogger(__name__)


# ── pure functions ────────────────────────────────────────────────────

def rapidocr_box_to_bbox(box) -> tuple[float, float, float, float]:
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


# ── XY-Cut layout engine ───────────────────────────────────────────────
# Hierarchical recursive XY-Cut with adaptive per-region thresholds.
#
# Design (synthesised from three canonical variants):
#   1. CC-based XY-Cut   (Ha, Haralick & Phillips 1995) — bbox input, recursive alternation
#   2. ARXYC              (Sylwester 2001)              — gap-ratio threshold, locally adaptive
#   3. Augmented XY-Cut   (Gu et al. 2022)              — sorted-adjacency gap detection
#
# Thresholds are multiples of local character metrics → DPI- and font-agnostic.

# Gap thresholds expressed as multiples of local character metrics.
# These are unitless ratios — they scale automatically with DPI and font size.
_GAP_RATIO_H_REGION = 2.5   # Y-gap > 2.5× char_h → horizontal region (header/body/footer)
_GAP_RATIO_H_LINE   = 0.4   # Y-gap > 0.4× char_h → text line separator
_GAP_RATIO_V_COLUMN = 3.5   # X-gap > 3.5× char_w → column separator
_GAP_RATIO_V_WORD   = 1.8   # X-gap > 1.8× char_w → word separator (Latin only)

# Minimum gap in pixels to avoid splitting on sub-pixel noise
_MIN_GAP_PX = 2.0

# ---------------------------------------------------------------------------


def _normalize_blocks(blocks: list[dict]) -> list[dict]:
    """Convert raw RapidOCR blocks to internal representation; filter junk.

    Blocks without a valid bounding box get a minimal placeholder so their
    text is preserved in the output (RapidOCR may occasionally return text
    without proper box coordinates in edge cases).
    """
    normalized: list[dict] = []
    for block in (blocks or []):
        text = str(block.get("text", "") or "").strip()
        if not text:
            continue
        left, top, right, bottom = rapidocr_box_to_bbox(block.get("box"))
        w = right - left
        h = bottom - top
        if w <= 0 or h <= 0:
            # Block has text but no valid bbox — give it a minimal placement
            w, h = 1.0, 1.0
        normalized.append({
            "text": text,
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

      * Single column — only top→bottom order matters (column direction irrelevant)
      * Multiple columns — traditional convention: right→left, top→bottom

    Horizontal text (default): top→bottom, left→right.
    """
    if len(blocks) <= 1:
        return list(blocks)

    # ── detect vertical CJK ──────────────────────────────────────────
    tall_count = sum(1 for b in blocks if b["height"] > b["width"] * 1.3)
    is_vertical_cjk = len(blocks) >= 3 and tall_count / len(blocks) > 0.5

    if is_vertical_cjk:
        # Are blocks clustered at a single X position or spread across columns?
        x_centers = sorted(b["center_x"] for b in blocks)
        x_span = x_centers[-1] - x_centers[0]
        widths = [b["width"] for b in blocks]
        med_w = statistics.median(widths) if widths else 15.0

        if x_span > med_w * 2.0:
            # Multi-column vertical CJK → traditional right→left column order.
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

            # Sort columns right→left (larger center_x = further right)
            columns.sort(
                key=lambda col: -sum(b["center_x"] for b in col) / len(col)
            )

            # Sort top→bottom within each column
            result: list[dict] = []
            for col in columns:
                col.sort(key=lambda b: b["center_y"])
                result.extend(col)
            blocks = result
        else:
            # Single-column vertical → top→bottom only (X order irrelevant)
            blocks.sort(key=lambda b: b["center_y"])
    else:
        # Standard horizontal: top→bottom, left→right
        blocks.sort(key=lambda b: (b["center_y"], b["left"]))

    return blocks


def _xy_cut(
    blocks: list[dict], direction: str, depth: int = 0
) -> list[dict]:
    """Recursive XY-Cut: partition blocks by alternating Y/X projection gaps.

    Returns blocks in reading order:

    * Y-cut first (top-level) → separate horizontal regions (header / body / footer)
    * X-cut within each region → separate columns
    * Y-cut within each column → separate text lines
    * Terminal: sort for reading direction (handles CJK vertical text)
    """
    if len(blocks) <= 1:
        return list(blocks)

    m = _region_metrics(blocks)
    med_h = m["med_h"]
    med_w = m["med_w"]

    # ── gap threshold: coarse at top level, fine at deeper levels ────
    if direction == "y":
        threshold = med_h * (
            _GAP_RATIO_H_REGION if depth == 0 else _GAP_RATIO_H_LINE
        )
    else:
        # X direction: column gaps only (never split individual words)
        threshold = med_w * _GAP_RATIO_V_COLUMN

    threshold = max(threshold, _MIN_GAP_PX)

    # ── sort + detect gaps ───────────────────────────────────────────
    if direction == "y":
        sorted_blocks = sorted(blocks, key=lambda b: (b["center_y"], b["left"]))
    else:
        sorted_blocks = sorted(blocks, key=lambda b: (b["left"], b["center_y"]))

    gaps = _detect_gaps(sorted_blocks, direction, threshold)

    if not gaps:
        # Terminal: no significant gaps → leaf region
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
    """Group reading-order blocks into OcrLine objects via center_y proximity."""
    if not ordered_blocks:
        return []

    heights = [b["height"] for b in ordered_blocks]
    med_h = statistics.median(heights) if heights else 15.0

    # Group consecutive blocks whose center_y is within 0.6× median height
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

    # Build OcrLine objects
    result: list[OcrLine] = []
    for group in line_groups:
        # ── sort within line ─────────────────────────────────────────
        # Horizontal text: left→right (standard).
        # Vertical CJK text: preserve the column order that _leaf_reading_order
        #   already established (right→left).  Sorting by left here would
        #   reverse it back to left→right.
        tall_in_group = sum(
            1 for b in group if b["height"] > b["width"] * 1.3
        )
        if tall_in_group / len(group) > 0.5 and len(group) >= 3:
            # Vertical CJK — keep _leaf_reading_order sequence
            pass
        else:
            group.sort(key=lambda b: b["left"])
        words: list[OcrWord] = []
        text_parts: list[str] = []
        prev_text = ""

        min_l = min(b["left"] for b in group)
        min_t = min(b["top"] for b in group)
        max_r = max(b["right"] for b in group)
        max_b = max(b["bottom"] for b in group)

        for block in group:
            sep = word_separator(prev_text, block["text"]) if prev_text else ""
            if sep:
                text_parts.append(sep)
            text_parts.append(block["text"])
            prev_text = block["text"]
            words.append(OcrWord(
                text=block["text"],
                bounding_box=bbox_to_ocr_box(
                    block["left"], block["top"],
                    block["right"], block["bottom"],
                ),
            ))

        result.append(OcrLine(
            text="".join(text_parts).strip(),
            words=words,
            bounding_box=bbox_to_ocr_box(min_l, min_t, max_r, max_b),
        ))

    return result


def _merge_lines_to_paragraphs(lines: list[OcrLine]) -> list[OcrLine]:
    """Merge consecutive lines into paragraphs using layout + text signals.

    Layout signals (XY-Cut already resolved column / reading order):
      - Line gap < 1.6× median line height  → same paragraph
      - Significant left-edge shift          → new paragraph (indent)
      - Short previous line                  → likely paragraph end

    Text signals:
      - Line ends with '-'                   → merge (hyphenated word)
      - Line ends with 。or .                → paragraph boundary
      - Open-ended line + next starts lower  → same sentence, merge
    """
    if len(lines) <= 1:
        return list(lines)

    heights = [l.bounding_box.height for l in lines if l.bounding_box.height > 0]
    if not heights:
        return list(lines)
    med_h = statistics.median(heights)
    widths = [l.bounding_box.width for l in lines if l.bounding_box.width > 0]
    med_w = statistics.median(widths) if widths else 300.0

    merged: list[OcrLine] = []
    para_lines: list[OcrLine] = [lines[0]]

    for i in range(1, len(lines)):
        prev = lines[i - 1]
        curr = lines[i]

        prev_text = prev.text.strip()
        curr_text = curr.text.strip()

        # ── text signals ─────────────────────────────────────────────
        hyphen_merge = prev_text.endswith("-")
        period_end = prev_text.endswith(("。", ".", "！", "？", "!", "?"))
        ends_open = prev_text and prev_text[-1] not in "。.！？!?,;；:："
        next_starts_lower = curr_text and curr_text[0].islower()
        lowercase_continue = ends_open and next_starts_lower

        # ── layout signals ───────────────────────────────────────────
        prev_bottom = prev.bounding_box.y + prev.bounding_box.height
        curr_top = curr.bounding_box.y
        gap = curr_top - prev_bottom

        x_shift = abs(curr.bounding_box.x - prev.bounding_box.x)
        prev_right = prev.bounding_box.x + prev.bounding_box.width
        global_right = max(
            l.bounding_box.x + l.bounding_box.width for l in lines
        )
        prev_is_short = (global_right - prev_right) > med_w * 0.35

        large_gap = gap > med_h * 1.6
        indent_jump = x_shift > max(med_w * 0.15, 12.0)

        # ── merge decision ───────────────────────────────────────────
        if hyphen_merge and not large_gap:
            para_lines.append(curr)
        elif period_end and gap > med_h * 0.7:
            merged.append(_flush_paragraph(para_lines))
            para_lines = [curr]
        elif large_gap or indent_jump or prev_is_short:
            merged.append(_flush_paragraph(para_lines))
            para_lines = [curr]
        elif gap <= med_h * 1.6:
            para_lines.append(curr)
        else:
            merged.append(_flush_paragraph(para_lines))
            para_lines = [curr]

    merged.append(_flush_paragraph(para_lines))
    return merged


def _is_heading(
    line: OcrLine, all_lines: list[OcrLine], med_h: float, med_w: float,
) -> bool:
    """Detect heading lines via geometry + isolation (no keyword coupling)."""
    text = line.text.strip()
    if not text or len(text) > 60:
        return False

    box = line.bounding_box
    tall = box.height > med_h * 1.25
    short_text = len(text) < 50 and box.width < med_w * 0.75

    if not (tall or short_text):
        return False

    # Check isolation (gaps above / below)
    try:
        idx = all_lines.index(line)
    except ValueError:
        return False

    isolation = 0
    if idx > 0:
        prev = all_lines[idx - 1]
        gap_before = box.y - (prev.bounding_box.y + prev.bounding_box.height)
        if gap_before > med_h * 1.0:
            isolation += 1
    else:
        isolation += 1

    if idx < len(all_lines) - 1:
        nxt = all_lines[idx + 1]
        gap_after = nxt.bounding_box.y - (box.y + box.height)
        if gap_after > med_h * 1.0:
            isolation += 1
    else:
        isolation += 1

    return isolation >= 1 and (tall or short_text)


# ── public API ────────────────────────────────────────────────────────────


def compose_rapidocr_structures(blocks: list[dict]) -> list[OcrLine]:
    """Convert RapidOCR word/character detection blocks into ordered OcrLines.

    Pipeline::

      1. Normalize raw blocks (filter empty / zero-size)
      2. Hierarchical recursive XY-Cut → reading order
      3. Group ordered blocks into OcrLine objects (center_y proximity)
      4. Merge consecutive lines into paragraphs (gap + text signals)
      5. Annotate headings with extra spacing
    """
    # Step 1 — normalize
    normalized = _normalize_blocks(blocks)
    if not normalized:
        return []

    # Step 2 — recursive XY-Cut → ordered blocks in reading order
    ordered = _xy_cut(normalized, direction="y", depth=0)

    # Step 3 — group into OcrLine objects
    lines = _build_lines_from_ordered_blocks(ordered)
    if not lines:
        return []

    # Step 4 — merge into paragraphs
    paragraphs = _merge_lines_to_paragraphs(lines)

    # Step 5 — annotate headings
    valid = [p for p in paragraphs if p.bounding_box.height > 0]
    if valid:
        med_h = statistics.median([p.bounding_box.height for p in valid])
        med_w = statistics.median([p.bounding_box.width for p in valid])
        for para in paragraphs:
            if _is_heading(para, paragraphs, med_h, med_w):
                para.text = f"\n{para.text}\n"

    return paragraphs


def _flush_paragraph(lines: list[OcrLine]) -> OcrLine:
    """Merge a paragraph's constituent lines into one OcrLine.

    Handles hyphenation repair: strips trailing '-' and joins without separator.
    """
    if len(lines) == 1:
        return lines[0]

    parts: list[str] = []
    prev_text = ""
    for line in lines:
        text = line.text
        if prev_text:
            if prev_text.rstrip().endswith("-"):
                if parts:
                    parts[-1] = parts[-1].rstrip()[:-1]
            else:
                sep = word_separator(prev_text, text)
                if sep:
                    parts.append(sep)
        parts.append(text)
        prev_text = text

    merged_text = "".join(parts).strip()

    min_x = min(l.bounding_box.x for l in lines)
    min_y = min(l.bounding_box.y for l in lines)
    max_x = max(l.bounding_box.x + l.bounding_box.width for l in lines)
    max_y = max(l.bounding_box.y + l.bounding_box.height for l in lines)

    all_words: list[OcrWord] = []
    for line in lines:
        all_words.extend(line.words)

    return OcrLine(
        text=merged_text,
        words=all_words,
        bounding_box=OcrBox(x=min_x, y=min_y, width=max_x - min_x, height=max_y - min_y),
    )


def compose_rapidocr_text(blocks: list[dict]) -> str:
    """Compatibility wrapper that returns plain text string."""
    lines = compose_rapidocr_structures(blocks)
    return "\n".join(line.text for line in lines).strip()


# ── engine singleton ───────────────────────────────────────────────────

_engine = None
_engine_lock = threading.Lock()
_active_requests = 0
_active_requests_cv = threading.Condition()


def _trim_working_set():
    """Aggressively collect garbage and trim the process working set once on release."""
    import gc

    before_mb = get_working_set_mb()
    logger.debug("[RapidOCR] _trim_working_set: before GC  %s", fmt_memory())

    gc.collect()

    after_gc_mb = get_working_set_mb()
    logger.debug("[RapidOCR] _trim_working_set: after  GC  %s (delta=%.1f MB)",
                 fmt_memory(), after_gc_mb - before_mb)

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # Signature is cached by memory_utils._init(); re-declare only if
        # this function is called before _init() has run (should not happen,
        # but guard against it).
        try:
            kernel32.SetProcessWorkingSetSize.argtypes
        except AttributeError:
            kernel32.SetProcessWorkingSetSize.argtypes = [
                ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_ssize_t,
            ]
            kernel32.SetProcessWorkingSetSize.restype = ctypes.c_int

        logger.debug("[RapidOCR] Calling SetProcessWorkingSetSize(-1, -1)...")
        res = kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), -1, -1)

        after_mb = get_working_set_mb()
        if res != 0:
            logger.debug("[RapidOCR] _trim_working_set: after  trim %s (delta=%.1f MB)",
                         fmt_memory(), after_mb - after_gc_mb)
        else:
            logger.warning(
                "[RapidOCR] SetProcessWorkingSetSize failed, err=%d. %s",
                ctypes.get_last_error(), fmt_memory(),
            )
    except Exception as exc:
        logger.error("[RapidOCR] _trim_working_set failed: %s", exc, exc_info=True)


def _acquire_request():
    global _active_requests
    with _active_requests_cv:
        _active_requests += 1


def _release_request():
    global _active_requests
    with _active_requests_cv:
        _active_requests -= 1
        _active_requests_cv.notify_all()


def _get_engine() -> "RapidOCR":
    global _engine
    if _engine is None:
        logger.debug("[RapidOCR] _get_engine: Initializing new engine instance...")
        with _engine_lock:
            if _engine is None:
                global RapidOCR, OCRVersion
                if RapidOCR is not None:
                    local_RapidOCR = RapidOCR
                else:
                    logger.debug("[RapidOCR] Importing RapidOCR...")
                    from rapidocr import RapidOCR as local_RapidOCR
                if OCRVersion is not None:
                    local_OCRVersion = OCRVersion
                else:
                    logger.debug("[RapidOCR] Importing OCRVersion...")
                    from rapidocr import OCRVersion as local_OCRVersion
                
                ws_before = get_working_set_mb()
                logging.info("[RapidOCR] Initializing engine singleton (models loading)...")
                _engine = local_RapidOCR(params={
                    "Det.ocr_version": local_OCRVersion.PPOCRV5,
                    "Rec.ocr_version": local_OCRVersion.PPOCRV5,
                    "Cls.ocr_version": local_OCRVersion.PPOCRV5,
                    "Global.use_cls": True,
                })
                ws_after = get_working_set_mb()
                logger.debug(
                    "[RapidOCR] Engine created. %s (delta=%.1f MB)",
                    fmt_memory(), ws_after - ws_before,
                )
    return _engine


def release_engine():
    """Release the RapidOCR engine singleton to free memory.

    Waits for in-flight requests, tears down ONNX sessions, then forces
    garbage collection and a working-set trim. The engine is lazily
    re-initialized on the next OCR call.
    """
    global _engine

    ws_entry = get_working_set_mb()
    logger.debug("[RapidOCR] release_engine: entry  %s", fmt_memory())

    with _active_requests_cv:
        while _active_requests > 0:
            _active_requests_cv.wait()

        with _engine_lock:
            if _engine is None:
                logger.debug("[RapidOCR] release_engine: engine already None, skipping")
                return
            # Let CPython's reference counting clean up ONNX sessions naturally
            _engine = None

    ws_before_trim = get_working_set_mb()
    logger.debug("[RapidOCR] release_engine: after del, before trim  %s (delta from entry=%.1f MB)",
                 fmt_memory(), ws_before_trim - ws_entry)

    _trim_working_set()

    ws_exit = get_working_set_mb()
    logger.debug("[RapidOCR] release_engine: exit  %s (total delta=%.1f MB)",
                 fmt_memory(), ws_exit - ws_entry)


# ── public API ────────────────────────────────────────────────────────

def _recognize_without_detection(engine, arr) -> OcrRecognition:
    """Fallback: skip text detection and run recognition on the whole image.
    
    Includes automatic content cropping to handle large/padded images where 
    the text might be too small relative to the canvas for the recognizer's 
    fixed-height input window.
    """
    from ..constants import OCR_ENGINE_RAPID
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
            logger.debug("RapidOCR recognition-only fallback returned no text")
            return OcrRecognition(
                requested_language_supported=True,
                engine_language_tag="zh-CN",
                engine_type=OCR_ENGINE_RAPID,
            )

        recognized_text = txts[0].strip()
        logger.debug("RapidOCR recognition-only fallback succeeded: %r", recognized_text)

        return OcrRecognition(
            text=recognized_text,
            requested_language_supported=True,
            engine_language_tag="zh-CN",
            engine_type=OCR_ENGINE_RAPID,
        )
    finally:
        engine.use_det = orig_det
        engine.use_cls = orig_cls


def recognize_rapidocr_qimage(image_or_result, language_tag: str = "") -> OcrRecognition:
    from .preprocess import OcrPreprocessResult
    from ..constants import OCR_ENGINE_RAPID

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
        return OcrRecognition(engine_type=OCR_ENGINE_RAPID)

    result = None
    try:
        # Convert QImage directly to an in-memory NumPy BGR array to bypass disk I/O entirely
        import numpy as np
        bgr_image = image.convertToFormat(QtGui.QImage.Format.Format_RGB32)
        width = bgr_image.width()
        height = bgr_image.height()
        ptr = bgr_image.bits()
        ptr.setsize(bgr_image.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4))[:, :, :3].copy()

        _acquire_request()
        try:
            engine = _get_engine()
            result = engine(arr)
            json_data = result.to_json()
        finally:
            _release_request()

        if not json_data:
            logger.debug("RapidOCR detection returned empty — falling back to recognition-only")
            
            # Smart Fallback: If image was padded, crop back to original size for the recognizer.
            # Rationale: The recognition model (CRNN) uses a fixed input height (typically 48px).
            # If we send a 960px padded image, the engine downscales it by 20x. For a small 
            # 24px original text, this downscaling results in sub-pixel dots (~1.2px), 
            # making recognition impossible. Reverting to original size ensures the recognizer 
            # receives the text at a readable scale (often a slight upscale like 1.5x-2x).
            if width > original_size.width() or height > original_size.height():
                y_off = (height - original_size.height()) // 2
                x_off = (width - original_size.width()) // 2
                fallback_arr = arr[y_off : y_off + original_size.height(), 
                                   x_off : x_off + original_size.width()].copy()
                logger.debug("Fallback: cropped back to %dx%d from %dx%d", 
                             original_size.width(), original_size.height(), width, height)
            else:
                fallback_arr = arr

            return _recognize_without_detection(engine, fallback_arr)

        blocks = [{"text": item["txt"], "box": item["box"]} for item in json_data]
        lines = compose_rapidocr_structures(blocks)
        text = "\n".join(line.text for line in lines).strip()
        
        return OcrRecognition(
            text=text,
            lines=lines,
            requested_language_supported=True,
            engine_language_tag="zh-CN",
            engine_type=OCR_ENGINE_RAPID,
        )
    except Exception:
        logger.exception("RapidOCR engine call failed")
        return OcrRecognition(engine_type=OCR_ENGINE_RAPID)



def recognize_rapidocr_result_from_pixmap(
    image_or_result,
    language_tag: str = "",
) -> OcrRecognition:
    """RapidOCR engine entry point. Receives a preprocessed QImage or OcrPreprocessResult."""
    if isinstance(image_or_result, QtGui.QImage):
        if image_or_result.isNull():
            return OcrRecognition()
    elif isinstance(image_or_result, OcrPreprocessResult):
        if image_or_result.image.isNull():
            return OcrRecognition()

    return recognize_rapidocr_qimage(image_or_result, language_tag=language_tag)


def warmup_rapidocr():
    """Pre-initialize the RapidOCR singleton to avoid cold-start latency."""
    ws_before = get_working_set_mb()
    t0 = time.perf_counter()
    logger.debug("[RapidOCR] warmup_rapidocr: start  %s", fmt_memory())
    try:
        _get_engine()
        elapsed = (time.perf_counter() - t0) * 1000
        ws_after = get_working_set_mb()
        logger.debug(
            "[RapidOCR] warmup_rapidocr: done  %s (delta=%.1f MB, took %.1fms)",
            fmt_memory(), ws_after - ws_before, elapsed,
        )
    except Exception:
        logger.exception("RapidOCR engine warmup failed")


# Register RapidOCR engine
from .engine import register_engine  # noqa: E402
from ..constants import OCR_ENGINE_RAPID  # noqa: E402
register_engine(
    OCR_ENGINE_RAPID,
    recognize=recognize_rapidocr_result_from_pixmap,
    release=release_engine,
    trim=_trim_working_set,
    warmup=warmup_rapidocr,
    metadata={
        "display_name": "RapidOCR",
        "error_prefixes": [],
    },
)
