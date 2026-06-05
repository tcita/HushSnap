import logging
import re
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


def compose_rapidocr_structures(blocks: list[dict]) -> list[OcrLine]:
    normalized_blocks = []
    vertical_aspect_count = 0
    total_valid = 0

    for block in blocks or []:
        text = str(block.get("text", "") or "").strip()
        if not text:
            continue
        left, top, right, bottom = rapidocr_box_to_bbox(block.get("box"))
        w = right - left
        h = bottom - top
        
        # Heuristic: if height is significantly greater than width, it's likely a vertical block.
        if h > w * 1.4:
            vertical_aspect_count += 1
        total_valid += 1
        
        normalized_blocks.append(
            {
                "text": text,
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "width": w,
                "height": h,
                "center_x": (left + right) / 2,
                "center_y": (top + bottom) / 2,
                "raw_box": block.get("box"),
            }
        )

    if not normalized_blocks:
        return []

    # Enhanced vertical layout detection:
    # 1. Check aspect ratios of individual blocks
    # 2. Compare vertical alignment vs horizontal neighbor trends using weighted evidence
    vert_align_count = 0.0
    horiz_neighbor_count = 0.0
    sample_blocks = normalized_blocks[:20]
    for i in range(len(sample_blocks)):
        for j in range(i + 1, len(sample_blocks)):
            bi, bj = sample_blocks[i], sample_blocks[j]
            x_dist = abs(bi["center_x"] - bj["center_x"])
            y_dist = abs(bi["center_y"] - bj["center_y"])
            
            # Potential vertical alignment (same column)
            if x_dist < min(bi["width"], bj["width"]) * 0.25:
                avg_h = min(bi["height"], bj["height"])
                if 0.7 * avg_h < y_dist < 2.0 * avg_h:
                    vert_align_count += 1.5  # Strong evidence: typical character spacing
                elif y_dist < 4.0 * avg_h:
                    vert_align_count += 0.5  # Weak evidence: distant but aligned
                else:
                    vert_align_count += 0.1  # Trace evidence: very distant
            
            # Potential horizontal neighbor (same line)
            if y_dist < min(bi["height"], bj["height"]) * 0.3 and x_dist > min(bi["width"], bj["width"]) * 0.5:
                horiz_neighbor_count += 1.0

    # Heuristic: Switch to vertical ONLY if:
    # - More than half the blocks are tall (classic vertical line boxes)
    # - OR Vertical alignment is very strong AND overwhelmingly outweighs horizontal connections
    is_vertical = (vertical_aspect_count / total_valid > 0.5)
    if not is_vertical:
        # Very conservative threshold: vertical evidence must be at least 2.5x horizontal 
        # to overcome the default horizontal bias.
        is_vertical = (vert_align_count > 5.0) and (vert_align_count > horiz_neighbor_count * 2.5)

    final_lines: list[OcrLine] = []

    if is_vertical:
        logger.debug("Vertical layout detected (aspect_score=%d, vert_align=%d, horiz_neigh=%d)", 
                     vertical_aspect_count, vert_align_count, horiz_neighbor_count)
        # 1. Sort columns from right to left (CJK standard)
        normalized_blocks.sort(key=lambda item: (-item["center_x"], item["center_y"]))
        
        columns: list[list[dict]] = []
        for block in normalized_blocks:
            if not columns:
                columns.append([block])
                continue

            last_col = columns[-1]
            avg_width = sum(item["width"] for item in last_col) / len(last_col)
            avg_center_x = sum(item["center_x"] for item in last_col) / len(last_col)
            
            # Group into the same column if horizontal center is close enough
            if abs(block["center_x"] - avg_center_x) <= max(avg_width, block["width"]) * 0.7:
                last_col.append(block)
            else:
                columns.append([block])

        for col in columns:
            col.sort(key=lambda item: item["center_y"])
            words: list[OcrWord] = []
            line_text_parts: list[str] = []
            prev_block = None
            
            min_l, min_t, max_r, max_b = float("inf"), float("inf"), float("-inf"), float("-inf")
            
            for block in col:
                text = block["text"]
                sep = word_separator(prev_block["text"], text) if prev_block else ""
                if sep:
                    line_text_parts.append(sep)
                line_text_parts.append(text)
                
                words.append(OcrWord(
                    text=text,
                    bounding_box=bbox_to_ocr_box(block["left"], block["top"], block["right"], block["bottom"])
                ))
                min_l, min_t = min(min_l, block["left"]), min(min_t, block["top"])
                max_r, max_b = max(max_r, block["right"]), max(max_b, block["bottom"])
                prev_block = block
                
            final_lines.append(OcrLine(
                text="".join(line_text_parts).strip(),
                words=words,
                bounding_box=bbox_to_ocr_box(min_l, min_t, max_r, max_b)
            ))
    else:
        # Standard horizontal mode: Robust Multi-line Grouping
        # We sort by center_y first to get a general top-to-bottom flow
        normalized_blocks.sort(key=lambda item: item["center_y"])
        
        line_groups: list[list[dict]] = []
        for block in normalized_blocks:
            # Look for an existing line that this block could belong to
            found_line = False
            for line in line_groups:
                # Use the average center_y and height of the line for matching
                avg_line_y = sum(item["center_y"] for item in line) / len(line)
                avg_line_h = sum(item["height"] for item in line) / len(line)
                
                # If the block's center is within 50% of the line's height
                if abs(block["center_y"] - avg_line_y) < avg_line_h * 0.5:
                    line.append(block)
                    found_line = True
                    break
            
            if not found_line:
                line_groups.append([block])

        # After grouping into lines, sort each line by X-coordinate
        # and then sort the lines themselves by their average Y-coordinate
        line_groups.sort(key=lambda line: sum(item["center_y"] for item in line) / len(line))

        for line in line_groups:
            line.sort(key=lambda item: item["left"])
            words: list[OcrWord] = []
            line_text_parts: list[str] = []
            prev_block = None
            
            min_l, min_t, max_r, max_b = float("inf"), float("inf"), float("-inf"), float("-inf")

            for block in line:
                text = block["text"]
                sep = word_separator(prev_block["text"], text) if prev_block else ""
                if sep:
                    line_text_parts.append(sep)
                line_text_parts.append(text)
                
                words.append(OcrWord(
                    text=text,
                    bounding_box=bbox_to_ocr_box(block["left"], block["top"], block["right"], block["bottom"])
                ))
                min_l, min_t = min(min_l, block["left"]), min(min_t, block["top"])
                max_r, max_b = max(max_r, block["right"]), max(max_b, block["bottom"])
                prev_block = block

            final_lines.append(OcrLine(
                text="".join(line_text_parts).strip(),
                words=words,
                bounding_box=bbox_to_ocr_box(min_l, min_t, max_r, max_b)
            ))

    # ── post-processing pipeline (spec ③④) ─────────────────────────
    # Compute stats needed for column detection
    valid_boxes = [l.bounding_box for l in final_lines if l.bounding_box.height > 0]
    avg_w = sum(b.width for b in valid_boxes) / len(valid_boxes) if valid_boxes else 100.0
    avg_h = sum(b.height for b in valid_boxes) / len(valid_boxes) if valid_boxes else 20.0

    # 1. Multi-column reading order (spec ④)
    final_lines = _detect_columns_and_reorder(final_lines, avg_w)

    # 2. Merge lines into paragraphs (spec ①②⑤)
    paragraphs = merge_lines_to_paragraphs(final_lines)

    # 3. Format headings with extra spacing (spec ③)
    for i, para in enumerate(paragraphs):
        if _is_heading_candidate(para, paragraphs, avg_h, avg_w):
            para.text = f"\n{para.text}\n"

    return paragraphs


# ── line classification helpers ──────────────────────────────────────

_LIST_MARKER_RE = re.compile(
    r"^\s*(?:\d+[\.\)]\s|[A-Za-z][\.\)]\s|[•·▪▸►○●◦\-–—]\s)"
)

def _is_list_item(text: str) -> bool:
    """Detect if text starts with a list marker (bullet, number, letter)."""
    return bool(_LIST_MARKER_RE.match(text))


def _is_heading_candidate(
    line: OcrLine, all_lines: list[OcrLine], avg_h: float, avg_w: float
) -> bool:
    """Heuristic heading detection using geometry only (no keyword coupling)."""
    text = line.text.strip()
    if not text:
        return False

    box = line.bounding_box
    # Geometry: noticeably taller font than body text
    tall = box.height > avg_h * 1.25
    # Geometry: significantly narrower than average (whitespace on sides)
    narrow = box.width < avg_w * 0.7
    # Short text that doesn't fill the line
    short_text = len(text) < 40 and box.width < avg_w * 0.75

    if not (tall or narrow or short_text):
        return False

    # Isolation: check gaps before and after
    idx = all_lines.index(line) if line in all_lines else -1
    if idx < 0:
        return False

    isolation_score = 0
    if idx > 0:
        prev = all_lines[idx - 1]
        prev_bottom = prev.bounding_box.y + prev.bounding_box.height
        gap_before = box.y - prev_bottom
        if gap_before > avg_h * 1.2:
            isolation_score += 1
    else:
        isolation_score += 1

    if idx < len(all_lines) - 1:
        next_line = all_lines[idx + 1]
        gap_after = next_line.bounding_box.y - (box.y + box.height)
        if gap_after > avg_h * 1.2:
            isolation_score += 1
    else:
        isolation_score += 1

    # Heading = isolated + geometrically distinct
    return isolation_score >= 1 and (tall or (narrow and short_text))


def _detect_columns_and_reorder(
    lines: list[OcrLine], avg_w: float
) -> list[OcrLine]:
    """Detect multi-column layout and reorder for reading order.

    Strategy: cluster lines by their horizontal center. If two clear clusters
    exist (left / right), read left column first, then right column.
    Single-column documents pass through unchanged.
    """
    if len(lines) < 4:
        return lines

    centers = [l.bounding_box.x + l.bounding_box.width / 2 for l in lines]
    min_c, max_c = min(centers), max(centers)
    span = max_c - min_c
    if span < avg_w * 1.5:
        return lines  # too narrow for multi-column

    # Two-means clustering on X centers
    c0, c1 = min_c, max_c
    for _ in range(10):
        g0 = [c for c in centers if abs(c - c0) <= abs(c - c1)]
        g1 = [c for c in centers if abs(c - c0) > abs(c - c1)]
        if g0:
            c0 = sum(g0) / len(g0)
        if g1:
            c1 = sum(g1) / len(g1)

    if not g0 or not g1:
        return lines

    col_gap = abs(c0 - c1)
    # Only treat as columns if the gap is substantial
    if col_gap < avg_w * 0.5:
        return lines

    left_col = [l for l in lines if abs((l.bounding_box.x + l.bounding_box.width / 2) - c0) <= abs((l.bounding_box.x + l.bounding_box.width / 2) - c1)]
    right_col = [l for l in lines if l not in left_col]

    # Sort each column by Y, then concatenate left → right
    left_col.sort(key=lambda l: l.bounding_box.y)
    right_col.sort(key=lambda l: l.bounding_box.y)

    logger.debug("Multi-column detected: left=%d lines, right=%d lines", len(left_col), len(right_col))
    return left_col + right_col


# ── paragraph merging (enhanced with text signals) ───────────────────

def merge_lines_to_paragraphs(lines: list[OcrLine]) -> list[OcrLine]:
    """Merge consecutive OCR lines into paragraphs using layout + text signals.

    Layout signals (from spec ①②):
      - Line gap < 1.5× average line height  (normal inter-line spacing)
      - Left edges align within tolerance     (same column / no indent change)
      - Short previous line → paragraph end

    Text signals (from spec ⑤):
      - Line ends with '-' hyphen → always merge (broken word)
      - Line ends with '。' or '.'  → paragraph boundary
      - Line ends without punct and next starts lowercase → same sentence, merge
    """
    if len(lines) <= 1:
        return lines

    valid = [l for l in lines if l.bounding_box.height > 0]
    if not valid:
        return lines

    avg_h = sum(l.bounding_box.height for l in valid) / len(valid)
    avg_w = sum(l.bounding_box.width for l in valid) / len(valid)
    global_right = max(l.bounding_box.x + l.bounding_box.width for l in valid)

    PARA_GAP_RATIO = 1.5
    X_ALIGN_TOLERANCE = max(avg_w * 0.12, 12.0)
    SHORT_LINE_RATIO = 0.65
    INDENT_THRESHOLD = max(avg_w * 0.18, 15.0)

    merged: list[OcrLine] = []
    para_lines: list[OcrLine] = [lines[0]]

    for i in range(1, len(lines)):
        prev = lines[i - 1]
        curr = lines[i]

        prev_text = prev.text.strip()
        curr_text = curr.text.strip()

        # ── text signals (spec ⑤) ──────────────────────────────────
        # Hyphenation: line ends with '-' → always merge (broken word)
        hyphen_merge = prev_text.endswith("-")

        # Period boundary: line ends with 。or . → paragraph boundary
        period_end = prev_text.endswith(("。", ".", "！", "？", "!", "?"))

        # Lowercase continuation: prev has no end punct, next starts lowercase
        ends_open = not prev_text[-1] in "。.！？!?,;；:："
        next_starts_lower = curr_text and curr_text[0].islower()
        lowercase_continue = ends_open and next_starts_lower

        # ── layout signals ─────────────────────────────────────────
        prev_bottom = prev.bounding_box.y + prev.bounding_box.height
        curr_top = curr.bounding_box.y
        gap = curr_top - prev_bottom

        if gap <= 1.0:
            para_lines.append(curr)
            continue

        prev_left = prev.bounding_box.x
        curr_left = curr.bounding_box.x
        x_shift = abs(curr_left - prev_left)

        prev_right = prev.bounding_box.x + prev.bounding_box.width
        prev_is_short = (global_right - prev_right) > avg_w * (1.0 - SHORT_LINE_RATIO)

        large_gap = gap > avg_h * PARA_GAP_RATIO
        indent_jump = x_shift > INDENT_THRESHOLD

        # ── merge decision ──────────────────────────────────────────
        # Text signals only apply when layout says lines are adjacent
        text_signals_active = not large_gap and not indent_jump

        if text_signals_active and (hyphen_merge or lowercase_continue):
            para_lines.append(curr)
        elif period_end and gap > avg_h * 0.8:
            # Period + spacing → definite paragraph break
            merged.append(_flush_paragraph(para_lines))
            para_lines = [curr]
        elif large_gap or indent_jump or prev_is_short:
            merged.append(_flush_paragraph(para_lines))
            para_lines = [curr]
        elif x_shift <= X_ALIGN_TOLERANCE:
            para_lines.append(curr)
        else:
            merged.append(_flush_paragraph(para_lines))
            para_lines = [curr]

    merged.append(_flush_paragraph(para_lines))
    return merged



def _flush_paragraph(lines: list[OcrLine]) -> OcrLine:
    """Merge a paragraph's lines into one OcrLine with flowing text.

    Handles hyphenation repair: if a line ends with '-' it is a broken word;
    the hyphen is stripped and the next line is joined without a separator.
    """
    if len(lines) == 1:
        return lines[0]

    parts: list[str] = []
    prev_line: OcrLine | None = None
    for line in lines:
        text = line.text
        if prev_line is not None:
            # Hyphenation repair (spec ⑤): strip trailing '-' and join directly
            if prev_line.text.rstrip().endswith("-"):
                # Remove the trailing hyphen from the previous part
                if parts:
                    parts[-1] = parts[-1].rstrip()[:-1]
            else:
                sep = word_separator(prev_line.text, text)
                if sep:
                    parts.append(sep)
        parts.append(text)
        prev_line = line

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
