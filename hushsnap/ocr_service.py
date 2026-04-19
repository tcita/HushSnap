import logging
import threading
from pathlib import Path

from PyQt6 import QtGui

from .ocr import (
    OcrBox,
    OcrLine,
    OcrRecognition,
    OcrRequest,
    OcrResponse,
    OcrTextAdapter,
    OcrWord,
)
from .ocr.recognition import recognize_result_from_pixmap as _recognize_result_from_pixmap
from .ocr.service import OcrService as _PackageOcrService
from .ocr.text import (
    NO_SPACE_SCRIPT_CHAR_CLASS,
    compose_text_from_result as _compose_text_from_result_impl,
    select_text_adapter as _select_text_adapter_impl,
)

logger = logging.getLogger(__name__)


def recognize_result_from_pixmap(
    pixmap: QtGui.QPixmap,
    language_tag: str = "",
    debug_dir: str | Path | None = None,
) -> OcrRecognition:
    return _recognize_result_from_pixmap(
        pixmap,
        language_tag=language_tag,
        debug_dir=debug_dir,
    )


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
    return _compose_text_from_result_impl(result, language_tag=language_tag)


def _compose_text_from_result(result: OcrRecognition, language_tag: str = "") -> str:
    return _compose_text_from_result_impl(result, language_tag=language_tag)


def _select_text_adapter(language_tag: str) -> OcrTextAdapter:
    return _select_text_adapter_impl(language_tag)


class OcrService(_PackageOcrService):
    """
    Compatibility wrapper for the legacy module path.
    Keeps monkeypatch-friendly indirection in this module while the implementation
    now lives under hushsnap.ocr.
    """

    def recognize(self, request: OcrRequest) -> OcrResponse:
        try:
            recognition = recognize_result_from_pixmap(
                request.pixmap,
                language_tag=request.language_tag,
                debug_dir=request.debug_dir,
            )
            text = _compose_text_from_result(result=recognition, language_tag=request.language_tag)
            return OcrResponse(
                text=text,
                error="",
                pixmap=request.pixmap,
                recognition=recognition,
            )
        except Exception as exc:
            logger.exception(f"OCR service failed: {exc}")
            return OcrResponse(
                text="",
                error=str(exc),
                pixmap=request.pixmap,
                recognition=None,
            )

    def recognize_async(self, request: OcrRequest, done_callback):
        def worker():
            done_callback(self.recognize(request))

        threading.Thread(target=worker, daemon=True).start()


__all__ = [
    "NO_SPACE_SCRIPT_CHAR_CLASS",
    "OcrBox",
    "OcrLine",
    "OcrRecognition",
    "OcrRequest",
    "OcrResponse",
    "OcrService",
    "OcrTextAdapter",
    "OcrWord",
    "recognize_result_from_pixmap",
    "recognize_text_from_pixmap",
    "_compose_text_from_result",
    "_select_text_adapter",
]
