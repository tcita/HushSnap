"""
Compatibility wrapper for legacy imports.

The OCR implementation now lives in `hushsnap.text_grab` as a decoupled,
reusable Python module.
"""

from ..text_grab import (
    OcrBox,
    OcrLine,
    OcrRecognition,
    OcrWord,
    TextGrabOcrService,
    TextGrabRequest,
    TextGrabResponse,
    recognize_result_from_pixmap,
    recognize_text_from_pixmap,
)

__all__ = [
    "OcrBox",
    "OcrWord",
    "OcrLine",
    "OcrRecognition",
    "TextGrabRequest",
    "TextGrabResponse",
    "TextGrabOcrService",
    "recognize_result_from_pixmap",
    "recognize_text_from_pixmap",
]
