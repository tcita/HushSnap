import logging
import tempfile
import threading
from pathlib import Path
from typing import Any

# Defer rapidocr import to optimize application startup time
from PyQt6 import QtGui
RapidOCR = None

from .models import OcrRecognition

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
                gap = block["left"] - previous["right"]
                avg_height = (block["height"] + previous["height"]) / 2
                if gap > avg_height * 1.2:
                    pieces.append(" " + text)
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
        with _engine_lock:
            if _engine is None:
                global RapidOCR
                if RapidOCR is not None:
                    local_RapidOCR = RapidOCR
                else:
                    from rapidocr import RapidOCR as local_RapidOCR
                _engine = local_RapidOCR()
    return _engine


def release_engine():
    """Release the RapidOCR engine singleton to free memory.

    Waits for in-flight requests, tears down ONNX sessions and their
    sub-engine wrappers, then forces garbage collection and a working-set
    trim.  The engine is lazily re-initialized on the next OCR call.
    """
    global _engine
    with _active_requests_cv:
        while _active_requests > 0:
            _active_requests_cv.wait()

        with _engine_lock:
            if _engine is None:
                return
            # Tear down ONNX InferenceSessions and clear sub-engine refs
            for attr in ("text_det", "text_cls", "text_rec"):
                sub = getattr(_engine, attr, None)
                if sub is None:
                    continue
                session = getattr(sub, "session", None)
                if session is not None:
                    session.session = None
                    sub.session = None
                setattr(_engine, attr, None)
            _engine = None

    # Multiple passes: nested C++ wrappers may take >1 collection to finalise
    import gc
    for _ in range(3):
        gc.collect()

    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # Must declare proper 64-bit types for process handles and sizes
        # on 64-bit Windows; default ctypes returns c_int (32-bit) which
        # silently truncates the handle and causes the trim to fail.
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetProcessWorkingSetSize.argtypes = [
            ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_ssize_t,
        ]
        kernel32.SetProcessWorkingSetSize.restype = ctypes.c_int
        kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), -1, -1)
    except Exception:
        logger.debug("SetProcessWorkingSetSize failed", exc_info=True)


# ── public API ────────────────────────────────────────────────────────

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

        engine = _get_engine()
        _acquire_request()
        try:
            result = engine(arr)
        finally:
            _release_request()
        json_data = result.to_json()

        if json_data is None:
            logger.debug("RapidOCR returned no text (to_json is None)")
            return OcrRecognition(
                requested_language_supported=True,
                engine_language_tag="zh-CN",
                engine_type=OCR_ENGINE_RAPID,
            )

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
    finally:
        # Clear large returned outputs and force garbage collection of circular references
        # to ensure that operational memory remains flat.
        if result is not None:
            del result
        import gc
        gc.collect()


def recognize_rapidocr_result_from_pixmap(
    pixmap: QtGui.QImage | QtGui.QPixmap,
    language_tag: str = "",
    debug_dir: str | Path | None = None,
    preprocess_settings: Any = None,
) -> OcrRecognition:
    if pixmap.isNull():
        logger.debug("recognize_rapidocr_result_from_pixmap called with null pixmap")
        return OcrRecognition()

    from .preprocess import default_preprocess_settings, run_minimal_pipeline
    
    active_settings = preprocess_settings or default_preprocess_settings()
    
    resolved_scale_factor = 1.0
    if active_settings.auto_scale:
        from .recognition import estimate_auto_scale_factor

        resolved_scale_factor = estimate_auto_scale_factor(
            pixmap,
            language_tag=language_tag,
            preprocess_settings=active_settings,
        )

    preprocess_result = run_minimal_pipeline(
        pixmap,
        settings=active_settings,
        resolved_scale_factor=resolved_scale_factor,
    )

    # Debug save if needed
    if debug_dir:
        from .recognition import save_debug_preprocessed_image
        save_debug_preprocessed_image(preprocess_result.image, debug_dir)

    return recognize_rapidocr_qimage(preprocess_result.image, language_tag=language_tag)


# Register RapidOCR engine
from .engine import register_engine  # noqa: E402
from ..constants import OCR_ENGINE_RAPID  # noqa: E402
register_engine(
    OCR_ENGINE_RAPID,
    recognize=recognize_rapidocr_result_from_pixmap,
    release=release_engine,
    metadata={
        "display_name": "RapidOCR",
        "error_prefixes": [],
    },
)
