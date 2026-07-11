from dataclasses import dataclass, field
from pathlib import Path

from PyQt6 import QtGui

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
    engine_type: str = ""


@dataclass
class OcrRequest:
    pixmap: QtGui.QPixmap | QtGui.QImage
    language_tag: str = ""
    engine: str = ""
    debug_dir: str | Path | None = None


@dataclass
class OcrResponse:
    text: str = ""
    error: str = ""
    pixmap: QtGui.QPixmap | QtGui.QImage | None = None
    recognition: OcrRecognition | None = None
