from .models import OcrBox, OcrLine, OcrRecognition, OcrRequest, OcrResponse, OcrWord
from .preprocess import (
    DEFAULT_OCR_PREPROCESS_SETTINGS,
    DEFAULT_OCR_SCALE_FACTOR,
    OcrPreprocessResult,
    OcrPreprocessSettings,
    OcrPreprocessStep,
    default_preprocess_settings,
    resolve_scale_factor,
)
from . import rapidocr  # noqa: F401  # triggers engine self-registration (must precede recognition to be default)

from .recognition import (
    estimate_auto_scale_factor,
    prepare_preprocess_result,
    recognize_result_from_pixmap,
    recognize_text_from_pixmap,
)
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
    "DEFAULT_OCR_PREPROCESS_SETTINGS",
    "DEFAULT_OCR_SCALE_FACTOR",
    "compose_text_from_result",
    "default_preprocess_settings",
    "estimate_auto_scale_factor",
    "prepare_preprocess_result",
    "recognize_result_from_pixmap",
    "recognize_text_from_pixmap",
    "resolve_scale_factor",
    "select_text_adapter",
]
