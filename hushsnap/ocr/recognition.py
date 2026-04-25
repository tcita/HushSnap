import logging
import tempfile
import time
from pathlib import Path

from PyQt6 import QtGui

from .models import OcrRecognition
from .parsing import parse_ocr_payload
from .preprocess import (
    DEFAULT_OCR_SCALE_FACTOR,
    OcrPreprocessSettings,
    default_preprocess_settings,
    run_preprocess_pipeline,
)
from .text import compose_text_from_result
from ..system.windows_ocr import run_windows_ocr_json

logger = logging.getLogger(__name__)


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
        if isinstance(payload, dict) and payload.get("Error"):
            raise RuntimeError(str(payload["Error"]).strip())
        return parse_ocr_payload(payload)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


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
    preprocess_settings: OcrPreprocessSettings | None = None,
) -> OcrRecognition:
    """
    Run OCR once against a single configurable preprocessing pipeline.
    """
    total_start = time.perf_counter()

    if pixmap.isNull():
        return OcrRecognition()

    active_settings = preprocess_settings or default_preprocess_settings()
    preprocess_result = run_preprocess_pipeline(pixmap, settings=active_settings)
    save_debug_preprocessed_image(preprocess_result.image, debug_dir)

    recognition = recognize_qimage(preprocess_result.image, language_tag=language_tag)
    if not recognition.text and not recognition.lines:
        logger.info(f"OCR Completed in {time.perf_counter() - total_start:.2f}s (empty result)")
        return OcrRecognition()

    logger.info(
        "OCR Completed in %.2fs (engine=windows, scale=%.2f, pipeline=%s, lines=%d)",
        time.perf_counter() - total_start,
        active_settings.scale_factor,
        preprocess_result.summary() or "raw",
        len(recognition.lines),
    )
    return recognition


def recognize_text_from_pixmap(
    pixmap: QtGui.QPixmap,
    language_tag: str = "",
    debug_dir: str | Path | None = None,
    preprocess_settings: OcrPreprocessSettings | None = None,
) -> str:
    result = recognize_result_from_pixmap(
        pixmap,
        language_tag=language_tag,
        debug_dir=debug_dir,
        preprocess_settings=preprocess_settings,
    )
    return compose_text_from_result(result, language_tag=language_tag)


INITIAL_SCALE_FACTOR = DEFAULT_OCR_SCALE_FACTOR
