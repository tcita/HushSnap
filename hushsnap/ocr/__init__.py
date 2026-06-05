from .models import OcrBox, OcrLine, OcrRecognition, OcrRequest, OcrResponse, OcrWord
from .preprocess import (
    DEFAULT_OCR_SCALE_FACTOR,
    OcrPreprocessResult,
    OcrPreprocessSettings,
    OcrPreprocessStep,
)
from . import ppocr  # noqa: F401  # triggers engine self-registration (must precede recognition to be default)

from .ocr_service import OcrService
from .text import OcrTextAdapter, compose_text_from_result, select_text_adapter

__all__ = [
    "OcrBox",
    "OcrLine",
    "OcrRecognition",
    "OcrPreprocessResult",
    "OcrPreprocessSettings",
    "OcrPreprocessStep",
    "OcrRequest",
    "OcrResponse",
    "OcrService",
    "OcrTextAdapter",
    "OcrWord",
    "DEFAULT_OCR_SCALE_FACTOR",
    "compose_text_from_result",
    "select_text_adapter",
]
