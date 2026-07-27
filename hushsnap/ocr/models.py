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
    indent_level: int = 0
    """Leading-indent level decided by the layout engine (0 = flush left).
    Rendered as ``level * 4`` spaces; the decision is made on clean geometry
    before any text mutation."""
    is_blank: bool = False
    """True when this line is a paragraph-separator blank line inserted by
    the layout engine (gap >= 1.5x line height).  ``text`` is empty but the
    line survives compose-time filtering to produce a blank line."""


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
