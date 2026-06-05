import logging
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

    return final_lines


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
