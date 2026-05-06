from dataclasses import dataclass, field
from pathlib import Path

from PyQt6 import QtGui

from .preprocess import OcrPreprocessSettings
from ..constants import OCR_ENGINE_RAPID


@dataclass
class OcrBox:
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class OcrWord:
    text: str = ""
    bounding_box: OcrBox = field(default_factory=OcrBox)


@dataclass
class OcrLine:
    text: str = ""
    words: list[OcrWord] = field(default_factory=list)
    bounding_box: OcrBox = field(default_factory=OcrBox)


@dataclass
class OcrRecognition:
    text: str = ""
    lines: list[OcrLine] = field(default_factory=list)
    angle: float = 0.0
    requested_language_supported: bool | None = None
    used_user_profile_fallback: bool = False
    engine_language_tag: str = ""
    engine_type: str = ""


@dataclass
class OcrRequest:
    pixmap: QtGui.QPixmap
    language_tag: str = ""
    engine: str = OCR_ENGINE_RAPID
    debug_dir: str | Path | None = None
    preprocess_settings: OcrPreprocessSettings | None = None


@dataclass
class OcrResponse:
    text: str = ""
    error: str = ""
    pixmap: QtGui.QPixmap | None = None
    recognition: OcrRecognition | None = None
