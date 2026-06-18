from enum import Enum
from typing import Optional
from PIL import Image
from PyQt6 import QtCore, QtGui

class UndoChangeType(Enum):
    """Type of change an undo entry captures, determining what state is stored."""
    FULL = "full"                    # PIL image + annotations + text (crop, full ops)
    ANNOTATIONS = "annotations"      # Only annotations pixmap + text items
    TEXT = "text"                    # Only text items (text tool add/edit/delete/move)
    REGION = "region"                # Only a rectangular region of PIL pixels (mosaic)

class TextItem:
    """Data model for a single text annotation."""
    def __init__(self, text: str, img_pos: QtCore.QPointF,
                 color: QtGui.QColor, font_family: str, font_size: int):
        self.text = text
        self.img_pos = img_pos  # Image-space coordinates
        self.color = color
        self.font_family = font_family
        self.font_size = font_size

class _UndoEntry:
    """Snapshot of editor state, optimized by change type."""

    __slots__ = (
        "change_type",
        "pil_image",           # FULL only
        "annotations_pixmap",  # FULL, ANNOTATIONS
        "text_items",          # FULL, ANNOTATIONS, TEXT
        "region_bounds",       # REGION only: QtCore.QRect
        "region_pixels",       # REGION only: bytes from PIL Image.tobytes()
    )

    def __init__(
        self,
        change_type: UndoChangeType,
        pil_img: Optional[Image.Image] = None,
        annot_pxm: Optional[QtGui.QPixmap] = None,
        text_items: Optional[list[TextItem]] = None,
        region_bounds: Optional[QtCore.QRect] = None,
        region_pixels: Optional[bytes] = None,
    ):
        self.change_type = change_type
        self.pil_image = pil_img.copy() if pil_img else None
        self.annotations_pixmap = annot_pxm.copy() if annot_pxm else None
        # Deep copy text items
        self.text_items = [
            TextItem(t.text, QtCore.QPointF(t.img_pos), QtGui.QColor(t.color),
                     t.font_family, t.font_size)
            for t in text_items
        ] if text_items else []
        self.region_bounds = region_bounds
        self.region_pixels = region_pixels
