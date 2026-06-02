import logging
import threading

# Defer rapidocr import to optimize application startup time
from PyQt6 import QtGui
RapidOCR = None
OCRVersion = None

from .models import OcrRecognition
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


def compose_rapidocr_text(blocks: list[dict]) -> str:
    normalized_blocks = []
    for block in blocks or []:
        text = str(block.get("text", "") or "").strip()
        if not text:
            continue
        left, top, right, bottom = rapidocr_box_to_bbox(block.get("box"))
        height = max(1.0, bottom - top)
        normalized_blocks.append(
            {
                "text": text,
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "center_y": (top + bottom) / 2,
                "height": height,
            }
        )

    if not normalized_blocks:
        return ""

    normalized_blocks.sort(key=lambda item: (item["center_y"], item["left"]))
    lines: list[list[dict]] = []
    for block in normalized_blocks:
        if not lines:
            lines.append([block])
            continue

        current_line = lines[-1]
        avg_height = sum(item["height"] for item in current_line) / len(current_line)
        avg_center_y = sum(item["center_y"] for item in current_line) / len(current_line)
        if abs(block["center_y"] - avg_center_y) <= max(avg_height, block["height"]) * 0.55:
            current_line.append(block)
        else:
            lines.append([block])

    rendered_lines: list[str] = []
    for line in lines:
        line.sort(key=lambda item: item["left"])
        pieces: list[str] = []
        previous = None
        for block in line:
            text = block["text"]
            if previous is None:
                pieces.append(text)
            else:
                pieces.append(word_separator(previous["text"], text) + text)
            previous = block

        rendered = "".join(pieces).rstrip()
        if rendered:
            rendered_lines.append(rendered)

    return "\n".join(rendered_lines).strip()


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

    The RapidOCR engine's detection model has a minimum-area threshold that
    can reject very small images (e.g. a single word).  When that happens,
    we feed the entire image directly to the recognizer.
    """
    from ..constants import OCR_ENGINE_RAPID

    orig_det = engine.use_det
    orig_cls = engine.use_cls
    try:
        _acquire_request()
        try:
            rec_result = engine(arr, use_det=False, use_cls=False)
        finally:
            _release_request()

        txts = getattr(rec_result, "txts", None)
        if not txts or not txts[0] or not txts[0].strip():
            logger.debug("RapidOCR recognition-only fallback also returned no text")
            return OcrRecognition(
                requested_language_supported=True,
                engine_language_tag="zh-CN",
                engine_type=OCR_ENGINE_RAPID,
            )

        recognized_text = txts[0].strip()
        logger.debug("RapidOCR recognition-only fallback succeeded: %r", recognized_text)

        h, w = arr.shape[:2]
        blocks = [{"text": recognized_text, "box": [[0, 0], [w, 0], [w, h], [0, h]]}]
        text = compose_rapidocr_text(blocks)
        return OcrRecognition(
            text=text,
            requested_language_supported=True,
            engine_language_tag="zh-CN",
            engine_type=OCR_ENGINE_RAPID,
        )
    finally:
        engine.use_det = orig_det
        engine.use_cls = orig_cls


def recognize_rapidocr_qimage(image: QtGui.QImage, language_tag: str = "") -> OcrRecognition:
    from ..constants import OCR_ENGINE_RAPID

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
            return _recognize_without_detection(engine, arr)

        blocks = [{"text": item["txt"], "box": item["box"]} for item in json_data]
        text = compose_rapidocr_text(blocks)
        return OcrRecognition(
            text=text,
            requested_language_supported=True,
            engine_language_tag="zh-CN",
            engine_type=OCR_ENGINE_RAPID,
        )
    except Exception:
        logger.exception("RapidOCR engine call failed")
        return OcrRecognition(engine_type=OCR_ENGINE_RAPID)



def recognize_rapidocr_result_from_pixmap(
    image: QtGui.QImage,
    language_tag: str = "",
) -> OcrRecognition:
    """RapidOCR engine entry point. Receives a preprocessed QImage."""
    if image.isNull():
        logger.debug("recognize_rapidocr_result_from_pixmap called with null image")
        return OcrRecognition()

    return recognize_rapidocr_qimage(image, language_tag=language_tag)


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
