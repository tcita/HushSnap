import logging
import tempfile
import time
from pathlib import Path

from PyQt6 import QtGui

from .models import OcrRecognition
from .parsing import parse_ocr_payload
from .preprocess import (
    OcrPreprocessSettings,
    default_preprocess_settings,
    run_minimal_pipeline,
)
from .text import compose_text_from_result
from ..system.windows_ocr import run_windows_ocr_json

logger = logging.getLogger(__name__)
_ENGINE_ID = "windows"


def recognize_qimage(
    image: QtGui.QImage,
    language_tag: str = "",
) -> OcrRecognition:
    """Run OCR on a temporary file generated from QImage."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        if not image.save(str(temp_path), "PNG"):
            logger.warning("Failed to save QImage to temp PNG for Windows OCR")
            return OcrRecognition()
        payload = run_windows_ocr_json(temp_path, language_tag)
        if isinstance(payload, dict) and payload.get("Error"):
            logger.error("Windows OCR error: %s", payload["Error"])
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
    image_or_pixmap: QtGui.QImage | QtGui.QPixmap,
    language_tag: str = "",
    debug_dir: str | Path | None = None,
    preprocess_settings: OcrPreprocessSettings | None = None,
) -> OcrRecognition:
    """
    Run OCR once against a single configurable preprocessing pipeline.
    """
    total_start = time.perf_counter()

    if image_or_pixmap.isNull():
        logger.debug("recognize_result_from_pixmap called with null image_or_pixmap")
        return OcrRecognition()

    active_settings = preprocess_settings or default_preprocess_settings()
    preprocess_result = run_minimal_pipeline(image_or_pixmap, settings=active_settings)

    save_debug_preprocessed_image(preprocess_result.image, debug_dir)

    recognition = recognize_qimage(preprocess_result.image, language_tag=language_tag)
    if not recognition.text and not recognition.lines:
        logger.info(f"OCR Completed in {time.perf_counter() - total_start:.2f}s (empty result)")
        return OcrRecognition()

    logger.info(
        "OCR Completed in %.2fs (engine=%s, scale=%.2f, pipeline=%s, lines=%d)",
        time.perf_counter() - total_start,
        _ENGINE_ID,
        preprocess_result.resolved_scale_factor,
        preprocess_result.summary() or "raw",
        len(recognition.lines),
    )
    return recognition


def recognize_text_from_pixmap(
    image_or_pixmap: QtGui.QImage | QtGui.QPixmap,
    language_tag: str = "",
    debug_dir: str | Path | None = None,
    preprocess_settings: OcrPreprocessSettings | None = None,
) -> str:
    result = recognize_result_from_pixmap(
        image_or_pixmap,
        language_tag=language_tag,
        debug_dir=debug_dir,
        preprocess_settings=preprocess_settings,
    )
    return compose_text_from_result(result, language_tag=language_tag)


# Register Windows OCR engine
from .engine import register_engine  # noqa: E402
from ..constants import OCR_ENGINE_WINDOWS  # noqa: E402
register_engine(
    OCR_ENGINE_WINDOWS,
    recognize=recognize_result_from_pixmap,
    release=None,
    metadata={
        "display_name": "WindowsOCR",
        "error_prefixes": ["windows ocr engine unavailable"],
    },
)
