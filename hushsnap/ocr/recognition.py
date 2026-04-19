import logging
import tempfile
import time
from pathlib import Path

from PyQt6 import QtGui

from .models import OcrRecognition
from .parsing import parse_ocr_payload
from .preprocess import preprocess_for_ocr
from .text import compose_text_from_result
from ..system.windows_ocr import run_windows_ocr_json

logger = logging.getLogger(__name__)

INITIAL_SCALE_FACTOR = 1.0
IDEAL_LINE_HEIGHT_PX = 40.0
MAX_OCR_IMAGE_DIMENSION = 2600
MIN_RESCALE_DELTA = 0.15


def recognize_qimage(
    image: QtGui.QImage,
    language_tag: str = "",
) -> OcrRecognition:
    """Run OCR on a temporary file generated from QImage."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        if not image.save(str(temp_path), "BMP"):
            return OcrRecognition()
        payload = run_windows_ocr_json(temp_path, language_tag)
        return parse_ocr_payload(payload)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def recommend_scale_factor(result: OcrRecognition, width: int, height: int) -> float:
    """Estimate a second-pass OCR scale based on detected word heights."""
    heights = [
        word.bounding_box.height
        for line in result.lines
        for word in line.words
        if word.bounding_box.height > 0
    ]

    line_height = (sum(heights) / len(heights)) if heights else 10.0
    if line_height <= 0:
        return INITIAL_SCALE_FACTOR

    scale_factor = IDEAL_LINE_HEIGHT_PX / line_height
    larger_dimension = max(width, height, 1)
    max_allowed_scale = MAX_OCR_IMAGE_DIMENSION / larger_dimension
    scale_factor = min(scale_factor, max_allowed_scale)
    return max(0.25, min(scale_factor, 4.0))


def save_debug_preprocessed_image(image: QtGui.QImage, debug_dir: str | Path | None) -> None:
    """Best-effort debug image dump; failures are logged but non-fatal."""
    if not debug_dir:
        return
    try:
        debug_path = Path(debug_dir) / "ocr_debug_preprocessed.png"
        image.save(str(debug_path), "PNG")
        logger.debug(f"Saved OCR debug image to: {debug_path}")
    except Exception as exc:
        logger.warning(f"Failed to save OCR debug image: {exc}")


def recognize_result_from_pixmap(
    pixmap: QtGui.QPixmap,
    language_tag: str = "",
    debug_dir: str | Path | None = None,
) -> OcrRecognition:
    """
    Run OCR with optional adaptive second pass.
    First pass is fast baseline; second pass is used only when scale estimate differs enough.
    """
    total_start = time.perf_counter()

    if pixmap.isNull():
        return OcrRecognition()

    initial_image = preprocess_for_ocr(pixmap, INITIAL_SCALE_FACTOR)
    save_debug_preprocessed_image(initial_image, debug_dir)

    initial_result = recognize_qimage(initial_image, language_tag=language_tag)
    if not initial_result.text and not initial_result.lines:
        logger.info(f"OCR Completed in {time.perf_counter() - total_start:.2f}s (empty result)")
        return OcrRecognition()

    recommended_scale = recommend_scale_factor(initial_result, initial_image.width(), initial_image.height())
    final_result = initial_result

    if abs(recommended_scale - INITIAL_SCALE_FACTOR) >= MIN_RESCALE_DELTA:
        rescaled_image = preprocess_for_ocr(pixmap, recommended_scale)
        rescaled_result = recognize_qimage(rescaled_image, language_tag=language_tag)
        if rescaled_result.text or rescaled_result.lines:
            final_result = rescaled_result

    logger.info(
        f"OCR Completed in {time.perf_counter() - total_start:.2f}s "
        f"(engine=windows, scale={recommended_scale:.2f}, lines={len(final_result.lines)})"
    )
    return final_result


def recognize_text_from_pixmap(
    pixmap: QtGui.QPixmap,
    language_tag: str = "",
    debug_dir: str | Path | None = None,
) -> str:
    result = recognize_result_from_pixmap(
        pixmap,
        language_tag=language_tag,
        debug_dir=debug_dir,
    )
    return compose_text_from_result(result, language_tag=language_tag)
