import logging
import tempfile
from pathlib import Path

from PyQt6 import QtGui

from .models import OcrRecognition
from .parsing import parse_ocr_payload
from .text import compose_text_from_result
from ..system.windows_ocr import run_windows_ocr_json

logger = logging.getLogger(__name__)
_ENGINE_ID = "windows"


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


def recognize_qimage(
    image: QtGui.QImage,
    language_tag: str = "",
) -> OcrRecognition:
    """Run Windows OCR on a temporary PNG saved from the preprocessed QImage."""
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


# ── Engine entry point (registered in engine.py) ────────────────────────

def recognize_result_from_pixmap(
    image: QtGui.QImage,
    language_tag: str = "",
) -> OcrRecognition:
    """Windows OCR engine entry point. Receives a preprocessed QImage."""
    if image.isNull():
        logger.debug("recognize_result_from_pixmap called with null image")
        return OcrRecognition()
    return recognize_qimage(image, language_tag=language_tag)


def recognize_text_from_pixmap(
    image: QtGui.QImage,
    language_tag: str = "",
) -> str:
    result = recognize_result_from_pixmap(image, language_tag=language_tag)
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
