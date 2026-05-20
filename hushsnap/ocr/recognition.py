import logging
import tempfile
import time
from statistics import fmean
from pathlib import Path

from PyQt6 import QtGui, QtCore

from .models import OcrRecognition
from .parsing import parse_ocr_payload
from .preprocess import (
    DEFAULT_OCR_SCALE_FACTOR,
    OcrPreprocessSettings,
    default_preprocess_settings,
    normalize_source_image,
    run_preprocess_pipeline,
    run_minimal_pipeline,
)
from .text import compose_text_from_result
from ..system.windows_ocr import run_windows_ocr_json
from ..config import get_ocr_engine
from ..constants import OCR_ENGINE_RAPID

logger = logging.getLogger(__name__)
IDEAL_OCR_WORD_HEIGHT_PX = 40.0
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


def estimate_auto_scale_factor(
    pixmap: QtGui.QPixmap,
    language_tag: str = "",
    preprocess_settings: OcrPreprocessSettings | None = None,
) -> float:
    if pixmap.isNull():
        return DEFAULT_OCR_SCALE_FACTOR

    active_settings = preprocess_settings or default_preprocess_settings()
    if active_settings.normalize_source:
        image = normalize_source_image(pixmap)
    else:
        image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)

    # Downscale for faster estimation
    orig_w = image.width()
    orig_h = image.height()
    max_dim = 1000
    if orig_w > max_dim or orig_h > max_dim:
        image = image.scaled(
            max_dim, max_dim, 
            QtCore.Qt.AspectRatioMode.KeepAspectRatio, 
            QtCore.Qt.TransformationMode.SmoothTransformation
        )
    
    recognition = recognize_qimage(image, language_tag=language_tag)
    if not recognition.lines:
        return 1.0

    # Adjust heights back to original scale
    scale_back = orig_w / image.width()
    heights = [
        word.bounding_box.height * scale_back 
        for line in recognition.lines 
        for word in line.words 
        if word.bounding_box.height > 0
    ]

    average_height = fmean(heights) if heights else 10.0
    scale_factor = IDEAL_OCR_WORD_HEIGHT_PX / max(1.0, average_height)
    return max(1.0, scale_factor)


def prepare_preprocess_result(
    pixmap: QtGui.QPixmap,
    language_tag: str = "",
    preprocess_settings: OcrPreprocessSettings | None = None,
):
    active_settings = preprocess_settings or default_preprocess_settings()
    resolved_scale_factor = None
    if active_settings.auto_scale:
        resolved_scale_factor = estimate_auto_scale_factor(
            pixmap,
            language_tag=language_tag,
            preprocess_settings=active_settings,
        )
    return run_preprocess_pipeline(
        pixmap,
        settings=active_settings,
        resolved_scale_factor=resolved_scale_factor,
    )


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
        logger.debug("recognize_result_from_pixmap called with null pixmap")
        return OcrRecognition()

    preprocess_result = prepare_preprocess_result(
        pixmap,
        language_tag=language_tag,
        preprocess_settings=preprocess_settings,
    )

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
