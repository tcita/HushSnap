"""
HushSnap lightweight image editor.
Provides brush, highlighter, eraser, mosaic, crop, zoom,
text annotation, shape, arrow tools, and undo/redo.
Opened from the thumbnail right-click "Edit" action.
"""

from __future__ import annotations

import logging
import io
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Optional, Callable

from PIL import Image, ImageDraw, ImageFont
from PyQt6 import QtCore, QtGui, QtWidgets, QtSvg

from ..config import get_config_path
from ..dpi import current_dpr
from .styles import BRAND_GREEN

logger = logging.getLogger(__name__)

# ── Style constants ──────────────────────────────────────────────────────────

EDITOR_WINDOW_STYLE = """
QWidget#editorWindow {
    background-color: #2d2d2d;
    color: #e0e0e0;
    font-size: 12px;
}
"""

EDITOR_TOOLBAR_ROW_STYLE = """
QWidget#toolbarRow {
    background-color: #252525;
    border-bottom: 1px solid rgba(95, 201, 138, 30);
}
"""

EDITOR_TOOL_BUTTON_STYLE = """
QToolButton {
    background-color: transparent;
    border: 1px solid rgba(255, 255, 255, 25);
    border-radius: 6px;
    padding: 5px 10px;
    color: #ccc;
    font-size: 12px;
    min-width: 28px;
    min-height: 28px;
}
QToolButton:hover {
    background-color: rgba(95, 201, 138, 50);
    border-color: rgba(95, 201, 138, 120);
    color: #fff;
}
QToolButton:checked {
    background-color: rgba(95, 201, 138, 70);
    border-color: #5FC98A;
    color: #fff;
}
"""

EDITOR_PUSH_BUTTON_STYLE = """
QPushButton {
    background-color: #3a3a3a;
    border: 1px solid rgba(255, 255, 255, 25);
    border-radius: 5px;
    padding: 5px 14px;
    color: #ccc;
    font-size: 12px;
}
QPushButton:hover {
    background-color: rgba(95, 201, 138, 50);
    border-color: rgba(95, 201, 138, 120);
    color: #fff;
}
QPushButton:disabled {
    background-color: #333;
    color: #666;
    border-color: rgba(255, 255, 255, 10);
}
"""

EDITOR_SAVE_BUTTON_STYLE = f"""
QPushButton#editorSaveBtn {{
    background-color: {BRAND_GREEN};
    border: none;
    border-radius: 5px;
    padding: 6px 18px;
    color: #ffffff;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#editorSaveBtn:hover {{
    background-color: #7ad9a0;
}}
"""

EDITOR_STATUS_STYLE = """
QLabel#statusLabel {
    color: #999;
    font-size: 11px;
    padding: 4px 10px;
    background-color: #222;
    border-top: 1px solid rgba(255, 255, 255, 10);
}
"""

EDITOR_OPTIONS_STYLE = """
QWidget#optionsArea {
    background-color: #282828;
    border-bottom: 1px solid rgba(255, 255, 255, 15);
}
QLabel#optionLabel {
    color: #aaa;
    font-size: 11px;
    padding: 0 6px;
}
QSlider::groove:horizontal {
    border: none;
    height: 4px;
    background-color: #444;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background-color: #5FC98A;
    border: none;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background-color: #5FC98A;
    border-radius: 2px;
}
QComboBox {
    background-color: #353535;
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 5px;
    padding: 5px 28px 5px 10px;
    color: #ccc;
    font-size: 12px;
        min-width: 80px;
}
QComboBox:hover {
    border-color: rgba(95, 201, 138, 140);
    background-color: #3a3a3a;
}
QComboBox:focus {
    border-color: #5FC98A;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #999;
    width: 0;
    height: 0;
    margin-right: 6px;
}
QComboBox QAbstractItemView {
    background-color: #2d2d2d;
    border: 1px solid rgba(255, 255, 255, 20);
    border-radius: 5px;
    outline: 0px;
    padding: 4px 0;
    color: #ccc;
    font-size: 12px;
        selection-background-color: rgba(95, 201, 138, 50);
    selection-color: #fff;
}
QComboBox QAbstractItemView::item {
    padding: 7px 14px;
    min-height: 28px;
}
QComboBox QAbstractItemView::item:hover {
    background-color: rgba(95, 201, 138, 25);
    color: #fff;
}
QSpinBox {
    background-color: #353535;
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 5px;
    padding: 5px 10px;
    color: #ccc;
    font-size: 12px;
}
QSpinBox:hover {
    border-color: rgba(95, 201, 138, 140);
    background-color: #3a3a3a;
}
QSpinBox:focus {
    border-color: #5FC98A;
}
"""

# ── Editor-specific combobox with frameless popup ─────────────────────────────

class _EditorComboBox(QtWidgets.QComboBox):
    """QComboBox whose dropdown popup is frameless / translucent, matching
    the editor's dark theme — the same approach as settings' SleekComboBox."""

    def showPopup(self) -> None:
        popup = self.view().window()
        if popup:
            popup.setWindowFlags(
                QtCore.Qt.WindowType.Popup
                | QtCore.Qt.WindowType.FramelessWindowHint,
            )
            popup.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
        super().showPopup()


class _EditorFontComboBox(QtWidgets.QFontComboBox):
    """QFontComboBox variant with the same frameless popup treatment."""

    def showPopup(self) -> None:
        popup = self.view().window()
        if popup:
            popup.setWindowFlags(
                QtCore.Qt.WindowType.Popup
                | QtCore.Qt.WindowType.FramelessWindowHint,
            )
            popup.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
        super().showPopup()


# ── Icon helpers ─────────────────────────────────────────────────────────────

def _load_editor_icon(name: str, color: QtGui.QColor = QtGui.QColor("#ccc")) -> QtGui.QIcon:
    """Load an SVG icon, apply color, and return QIcon."""
    import os
    icon_path = os.path.join(os.path.dirname(__file__), "icons", f"edit_{name}.svg")
    if not os.path.exists(icon_path):
        logger.warning(f"Editor icon not found: {icon_path}")
        return QtGui.QIcon()

    try:
        with open(icon_path, "r", encoding="utf-8") as f:
            svg_data = f.read()
        
        # Colorize by replacing currentColor
        svg_data = svg_data.replace('currentColor', color.name())
        
        renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg_data.encode("utf-8")))
        pixmap = QtGui.QPixmap(32, 32)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        
        painter = QtGui.QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QtGui.QIcon(pixmap)
    except Exception as e:
        logger.error(f"Failed to load editor icon {name}: {e}")
        return QtGui.QIcon()


# ── Helper functions ─────────────────────────────────────────────────────────

def _make_circle_cursor(size: int = 12) -> QtGui.QCursor:
    """Create a Photoshop-style circle cursor matching the brush size."""
    # Ensure minimum size for visibility
    size = max(4, size)
    # Pad pixmap to avoid clipping the circle
    s = size + 4
    px = QtGui.QPixmap(s, s)
    px.fill(QtCore.Qt.GlobalColor.transparent)
    
    painter = QtGui.QPainter(px)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    
    cx, cy = s / 2.0, s / 2.0
    r = size / 2.0
    
    # Draw a high-contrast circle (white outer, black inner)
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    
    # Outer white ring
    painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 200), 1.2))
    painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)
    
    # Inner black ring (slightly smaller for contrast)
    painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 180), 0.8))
    painter.drawEllipse(QtCore.QPointF(cx, cy), r - 0.5, r - 0.5)
    
    # Optional: Tiny dot in center for precision
    painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 200), 1))
    painter.drawPoint(QtCore.QPointF(cx, cy))
    
    painter.end()
    return QtGui.QCursor(px, int(cx), int(cy))


def _pil_to_qpixmap(pil_img: Image.Image) -> QtGui.QPixmap:
    """Convert PIL Image to QPixmap."""
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qimage = QtGui.QImage(
        data, pil_img.size[0], pil_img.size[1],
        QtGui.QImage.Format.Format_RGBA8888,
    ).copy()
    return QtGui.QPixmap.fromImage(qimage)


def _qpixmap_to_pil(pixmap: QtGui.QPixmap) -> Image.Image:
    """Convert QPixmap to PIL Image via PNG buffer."""
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QBuffer.OpenModeFlag.ReadWrite)
    pixmap.save(buffer, "PNG")
    return Image.open(io.BytesIO(buffer.data().data()))


# ── Undo system ──────────────────────────────────────────────────────────────

class UndoChangeType(Enum):
    """Type of change an undo entry captures, determining what state is stored."""
    FULL = "full"                    # PIL image + annotations + text (crop, full ops)
    ANNOTATIONS = "annotations"      # Only annotations pixmap + text items (brush, eraser, shapes, arrows, highlighter)
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
    """Snapshot of editor state, optimized by change type to avoid copying the
    full PIL image when only annotations or text items change."""

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


# ── Abstract base tool ───────────────────────────────────────────────────────

class BaseTool(ABC):
    """Abstract base for all editing tools."""

    def __init__(self, editor: "ImageEditorWindow"):
        self._editor = editor

    @abstractmethod
    def tool_id(self) -> str:
        """Unique identifier for this tool."""
        ...

    def on_activate(self) -> None:
        """Called when this tool becomes active."""
        pass

    def on_deactivate(self) -> None:
        """Called when switching away from this tool."""
        pass

    def on_mouse_press(
        self, canvas: "EditorCanvas", event: QtGui.QMouseEvent
    ) -> bool:
        """Return True if event was handled."""
        return False

    def on_mouse_move(
        self, canvas: "EditorCanvas", event: QtGui.QMouseEvent
    ) -> bool:
        return False

    def on_mouse_release(
        self, canvas: "EditorCanvas", event: QtGui.QMouseEvent
    ) -> bool:
        return False

    def on_key_press(
        self, canvas: "EditorCanvas", event: QtGui.QKeyEvent
    ) -> bool:
        return False

    def on_mouse_double_click(
        self, canvas: "EditorCanvas", event: QtGui.QMouseEvent
    ) -> bool:
        """Return True if handled."""
        return False

    def on_paint(
        self, canvas: "EditorCanvas", painter: QtGui.QPainter
    ) -> None:
        """Optional: paint additional decorations."""
        pass

    def _to_image_coords(
        self, canvas: "EditorCanvas", widget_pos: QtCore.QPoint
    ) -> tuple[int, int]:
        """Convert widget coordinates to image pixel coordinates."""
        offset = canvas._image_offset()
        scale = canvas._editor._effective_scale()
        return (
            int((widget_pos.x() - offset.x()) / scale),
            int((widget_pos.y() - offset.y()) / scale),
        )

    # ── Shared stroke infrastructure (Pinta-style path accumulation) ─────

    def _stroke_begin(self, pt: tuple[int, int]) -> None:
        """Initialise per-stroke state. Subclasses set _stroke_color / _stroke_size."""
        img_size = self._editor._pil_image.size
        self._stroke_pixmap = QtGui.QPixmap(QtCore.QSize(*img_size))
        self._stroke_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._stroke_path = QtGui.QPainterPath()
        self._stroke_path.moveTo(QtCore.QPointF(*pt))

    def _stroke_add_point(self, pt: tuple[int, int]) -> None:
        """Add a lineTo to the accumulated path and redraw the stroke pixmap."""
        self._stroke_path.lineTo(QtCore.QPointF(*pt))
        self._stroke_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(self._stroke_pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        if self._stroke_path.elementCount() <= 1:
            # Single point — draw filled circle
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QBrush(getattr(self, '_stroke_color', QtGui.QColor("#000"))))
            r = max(1, getattr(self, '_stroke_size', 3) / 2.0)
            el = self._stroke_path.elementAt(0)
            painter.drawEllipse(QtCore.QPointF(el.x, el.y), r, r)
        else:
            pen = QtGui.QPen(
                getattr(self, '_stroke_color', QtGui.QColor("#000")),
                getattr(self, '_stroke_size', 3),
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
                QtCore.Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawPath(self._stroke_path)
        painter.end()

    def _stroke_commit(self, composition_mode=QtGui.QPainter.CompositionMode.CompositionMode_SourceOver) -> None:
        """Composite per-stroke pixmap onto annotations layer."""
        if not getattr(self, '_stroke_pixmap', None):
            return
        painter = QtGui.QPainter(self._editor._annotations_pixmap)
        painter.setCompositionMode(composition_mode)
        painter.drawPixmap(0, 0, self._stroke_pixmap)
        painter.end()
        self._stroke_pixmap = None
        self._stroke_path = None

    def _stroke_cleanup(self) -> None:
        """Release stroke resources."""
        self._stroke_pixmap = None
        self._stroke_path = None


# ── Concrete tools ───────────────────────────────────────────────────────────

class BrushTool(BaseTool):
    """Freehand brush: Pinta-style path-accumulated stroke on annotations layer."""

    def __init__(self, editor: "ImageEditorWindow"):
        super().__init__(editor)
        self._stroke_color = QtGui.QColor("#5FC98A")
        self._stroke_size = 3
        self._stroke_pixmap: Optional[QtGui.QPixmap] = None
        self._stroke_path: Optional[QtGui.QPainterPath] = None

    # Public aliases for toolbar binding
    @property
    def color(self) -> QtGui.QColor: return self._stroke_color
    @color.setter
    def color(self, v: QtGui.QColor): self._stroke_color = v
    @property
    def size(self) -> int: return self._stroke_size
    @size.setter
    def size(self, v: int): self._stroke_size = v

    def tool_id(self) -> str:
        return "brush"

    def on_activate(self) -> None:
        self._editor._update_tool_cursor()

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._editor._save_undo(UndoChangeType.ANNOTATIONS)
            pt = self._to_image_coords(canvas, event.position().toPoint())
            self._stroke_begin(pt)
            self._stroke_add_point(pt)
            canvas.update()
            return True
        return False

    def on_mouse_move(self, canvas, event) -> bool:
        if getattr(self, '_stroke_path', None) and (
            event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            pt = self._to_image_coords(canvas, event.position().toPoint())
            self._stroke_add_point(pt)
            canvas.update()
            return True
        return False

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and getattr(self, '_stroke_path', None):
            self._stroke_commit()
            self._stroke_cleanup()
            self._editor._modified = True
            return True
        return False


class HighlighterTool(BaseTool):
    """Semi-transparent marker; shares Pinta-style path accumulation."""

    def __init__(self, editor: "ImageEditorWindow"):
        super().__init__(editor)
        self._stroke_color = QtGui.QColor(255, 255, 0, 80)
        self._stroke_size = 20
        self._stroke_pixmap: Optional[QtGui.QPixmap] = None
        self._stroke_path: Optional[QtGui.QPainterPath] = None

    @property
    def color(self) -> QtGui.QColor: return self._stroke_color
    @color.setter
    def color(self, v: QtGui.QColor): self._stroke_color = v
    @property
    def size(self) -> int: return self._stroke_size
    @size.setter
    def size(self, v: int): self._stroke_size = v

    def tool_id(self) -> str:
        return "highlighter"

    def on_activate(self) -> None:
        self._editor._update_tool_cursor()

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._editor._save_undo(UndoChangeType.ANNOTATIONS)
            pt = self._to_image_coords(canvas, event.position().toPoint())
            self._stroke_begin(pt)
            self._stroke_add_point(pt)
            canvas.update()
            return True
        return False

    def on_mouse_move(self, canvas, event) -> bool:
        if getattr(self, '_stroke_path', None) and (
            event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            pt = self._to_image_coords(canvas, event.position().toPoint())
            self._stroke_add_point(pt)
            canvas.update()
            return True
        return False

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and getattr(self, '_stroke_path', None):
            self._stroke_commit()
            self._stroke_cleanup()
            self._editor._modified = True
            return True
        return False


class EraserTool(BaseTool):
    """Erase annotations directly on the annotations layer (Pinta-style:
    draws with Clear operator, no intermediate pixmap)."""

    def __init__(self, editor: "ImageEditorWindow"):
        super().__init__(editor)
        self._stroke_size = 20
        self._last_point: Optional[tuple[int, int]] = None

    @property
    def size(self) -> int: return self._stroke_size
    @size.setter
    def size(self, v: int): self._stroke_size = v

    def tool_id(self) -> str:
        return "eraser"

    def on_activate(self) -> None:
        self._editor._update_tool_cursor()

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._editor._save_undo(UndoChangeType.ANNOTATIONS)
            self._last_point = self._to_image_coords(canvas, event.position().toPoint())
            # Draw initial dot directly on annotations
            painter = QtGui.QPainter(self._editor._annotations_pixmap)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Clear)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QBrush(QtCore.Qt.GlobalColor.black))
            r = max(1, self._stroke_size / 2.0)
            painter.drawEllipse(QtCore.QPointF(*self._last_point), r, r)
            painter.end()
            canvas.update()
            return True
        return False

    def on_mouse_move(self, canvas, event) -> bool:
        if self._last_point is not None and (
            event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            current = self._to_image_coords(canvas, event.position().toPoint())
            painter = QtGui.QPainter(self._editor._annotations_pixmap)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Clear)
            pen = QtGui.QPen(
                QtCore.Qt.GlobalColor.black, self._stroke_size,
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
                QtCore.Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawLine(
                QtCore.QPointF(*self._last_point),
                QtCore.QPointF(*current),
            )
            painter.end()
            self._last_point = current
            canvas.update()
            return True
        return False

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._last_point is not None:
            self._last_point = None
            self._editor._modified = True
            return True
        return False


class MosaicTool(BaseTool):
    """Drag a rectangle to pixelate (mosaic) the region."""

    def __init__(self, editor: "ImageEditorWindow"):
        super().__init__(editor)
        self.block_size = 10
        self._start_point: Optional[tuple[int, int]] = None
        self._current_point: Optional[tuple[int, int]] = None

    def tool_id(self) -> str:
        return "mosaic"

    def on_activate(self) -> None:
        self._editor._canvas.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._start_point = self._to_image_coords(canvas, event.position().toPoint())
            self._current_point = self._start_point
            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            return True
        return False

    def on_mouse_move(self, canvas, event) -> bool:
        if self._start_point is not None and (
            event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            self._current_point = self._to_image_coords(canvas, event.position().toPoint())
            # Draw selection rect on overlay
            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(self._editor._overlay_pixmap)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            pen = QtGui.QPen(QtGui.QColor(95, 201, 138, 200), 1.5, QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QtGui.QBrush(QtGui.QColor(95, 201, 138, 30)))
            x1, y1 = self._start_point
            x2, y2 = self._current_point
            painter.drawRect(QtCore.QRect(
                min(x1, x2), min(y1, y2),
                abs(x2 - x1), abs(y2 - y1),
            ))
            painter.end()
            canvas.update()
            return True
        return False

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._start_point:
            end = self._to_image_coords(canvas, event.position().toPoint())
            x1, y1 = self._start_point
            x2, y2 = end
            x1, x2 = min(x1, x2), max(x1, x2)
            y1, y2 = min(y1, y2), max(y1, y2)

            # Clamp to image bounds so dragging partially outside still
            # pixelates the intersection region.
            img_w, img_h = self._editor._pil_image.size
            x1 = max(0, min(x1, img_w))
            y1 = max(0, min(y1, img_h))
            x2 = max(0, min(x2, img_w))
            y2 = max(0, min(y2, img_h))

            w, h = x2 - x1, y2 - y1
            if w > 2 and h > 2:
                try:
                    # Save undo region BEFORE pixelating
                    old_region = self._editor._pil_image.crop((x1, y1, x2, y2))
                    region_bytes = old_region.tobytes()
                    self._editor._save_undo(
                        UndoChangeType.REGION,
                        region_bounds=QtCore.QRect(x1, y1, w, h),
                        region_pixels=region_bytes,
                    )
                    region = self._editor._pil_image.crop((x1, y1, x2, y2))
                    bs = max(1, self.block_size)
                    small_w = max(1, region.width // bs)
                    small_h = max(1, region.height // bs)
                    small = region.resize((small_w, small_h), Image.NEAREST)
                    pixelated = small.resize(region.size, Image.NEAREST)
                    self._editor._pil_image.paste(pixelated, (x1, y1))
                    self._editor._rebuild_display()
                except Exception:
                    logger.exception("MosaicTool: failed to apply mosaic")

            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            self._editor._modified = True
            self._start_point = None
            self._current_point = None
            canvas.update()
            return True
        return False

    def on_key_press(self, canvas, event) -> bool:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self._start_point = None
            self._current_point = None
            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            canvas.update()
            return True
        return False


class CropTool(BaseTool):
    """Crop tool with draggable corner / edge handles and dimmed overlay.

    Standard UX — the crop rect covers the full image initially; drag any
    corner or edge to trim.  Enter / toolbar button confirms; Esc cancels.
    """

    HANDLE_R = 6   # image-pixel hit radius for handles
    MIN_CROP = 10  # minimum crop dimension in image pixels

    def __init__(self, editor: "ImageEditorWindow"):
        super().__init__(editor)
        self._crop_rect: Optional[QtCore.QRect] = None
        self._dragging: Optional[str] = None
        self._drag_start_rect: Optional[QtCore.QRect] = None
        self._drag_start_img: Optional[tuple[int, int]] = None
        # Floating action buttons (children of the canvas)
        self._action_cancel: Optional[QtWidgets.QPushButton] = None
        self._action_apply: Optional[QtWidgets.QPushButton] = None

    def tool_id(self) -> str:
        return "crop"

    # ── activation ──────────────────────────────────────────────────────

    def on_activate(self) -> None:
        img_w, img_h = self._editor._pil_image.size
        self._crop_rect = QtCore.QRect(0, 0, img_w, img_h)
        self._editor._canvas.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self._create_action_buttons()
        self._redraw_overlay()
        self._editor._canvas.update()

    def on_deactivate(self) -> None:
        self._crop_rect = None
        self._dragging = None
        self._destroy_action_buttons()
        self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._editor._canvas.update()

    # ── floating action buttons ──────────────────────────────────────────

    def _create_action_buttons(self) -> None:
        canvas = self._editor._canvas
        t = self._editor._tr

        self._action_cancel = QtWidgets.QPushButton(t("editor_crop_cancel"), canvas)
        self._action_cancel.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._action_cancel.setStyleSheet(f"""
            QPushButton {{
                background: rgba(40,40,40,200);
                border: 1px solid rgba(255,255,255,20);
                border-radius: 11px;
                padding: 4px 14px;
                color: #ccc;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: rgba(60,60,60,220);
                border-color: rgba(255,255,255,35);
                color: #fff;
            }}
        """)
        self._action_cancel.clicked.connect(self.cancel_crop)

        self._action_apply = QtWidgets.QPushButton(t("editor_crop_confirm"), canvas)
        self._action_apply.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._action_apply.setStyleSheet(f"""
            QPushButton {{
                background: #5FC98A;
                border: none;
                border-radius: 11px;
                padding: 4px 16px;
                color: #fff;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: #6fd99d;
            }}
            QPushButton:pressed {{
                background: #4ab87a;
            }}
        """)
        self._action_apply.clicked.connect(self.apply_crop)

        self._position_action_buttons()

    def _destroy_action_buttons(self) -> None:
        for b in (self._action_cancel, self._action_apply):
            if b:
                b.deleteLater()
        self._action_cancel = None
        self._action_apply = None

    def _position_action_buttons(self) -> None:
        """Place the two floating buttons centred just below the crop rect."""
        r = self._crop_rect
        canvas = self._editor._canvas
        if r is None or not self._action_apply:
            return
        scale = self._editor._effective_scale()
        offset = canvas._image_offset()
        # Anchor: centre of the crop-rect bottom edge
        cx = r.center().x() * scale + offset.x()
        cy = r.bottom() * scale + offset.y() + 8  # 8px gap below the rect
        # Position the two buttons side-by-side
        cancel_w = self._action_cancel.sizeHint().width()
        apply_w = self._action_apply.sizeHint().width()
        gap = 8
        total_w = cancel_w + apply_w + gap
        left = int(cx - total_w / 2)
        self._action_cancel.move(left, int(cy))
        self._action_apply.move(left + cancel_w + gap, int(cy))
        self._action_cancel.show()
        self._action_apply.show()

    # ── mouse ───────────────────────────────────────────────────────────

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return False
        pos = event.position().toPoint()
        # 1. Handle hit → resize crop
        handle = self._hit_test(canvas, pos) if self._crop_rect else None
        if handle:
            self._dragging = handle
            self._drag_start_rect = QtCore.QRect(self._crop_rect)
            self._drag_start_img = self._to_image_coords(canvas, pos)
            return True
        # 2. Inside crop rect → reposition crop
        img = self._to_image_coords(canvas, pos)
        if self._crop_rect and self._crop_rect.contains(img[0], img[1]):
            self._dragging = "move"
            self._drag_start_rect = QtCore.QRect(self._crop_rect)
            self._drag_start_img = img
            return True
        # 3. Outside crop rect → pan the image (like hand tool)
        self._dragging = "pan"
        self._drag_start_img = (
            event.globalPosition().toPoint().x(),
            event.globalPosition().toPoint().y(),
        )
        canvas.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
        return True

    def on_mouse_move(self, canvas, event) -> bool:
        pos = event.position().toPoint()
        if self._dragging == "pan":
            cur = event.globalPosition().toPoint()
            sx, sy = self._drag_start_img
            dx, dy = cur.x() - sx, cur.y() - sy
            self._drag_start_img = (cur.x(), cur.y())
            sa = self._editor._scroll_area
            hb = sa.horizontalScrollBar()
            vb = sa.verticalScrollBar()
            hb.setValue(int(hb.value() - dx))
            vb.setValue(int(vb.value() - dy))
            return True

        if self._dragging:
            img = self._to_image_coords(canvas, pos)
            self._update_rect(img[0], img[1])
            self._redraw_overlay()
            canvas.update()
            return True

        # cursor feedback (hover, not dragging)
        h = self._hit_test(canvas, pos) if self._crop_rect else None
        if h in ("tl", "br"):
            canvas.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor)
        elif h in ("tr", "bl"):
            canvas.setCursor(QtCore.Qt.CursorShape.SizeBDiagCursor)
        elif h in ("t", "b"):
            canvas.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
        elif h in ("l", "r"):
            canvas.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
        elif h == "move":
            canvas.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
        else:
            canvas.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        return bool(h)

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            if self._dragging == "pan":
                canvas.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            self._dragging = None
            self._drag_start_rect = None
            self._drag_start_img = None
            return True
        return False

    def on_key_press(self, canvas, event) -> bool:
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            self.apply_crop()
            return True
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.cancel_crop()
            return True
        return False

    # ── public API (called from toolbar buttons) ─────────────────────────

    def apply_crop(self) -> None:
        if not self._crop_rect:
            return
        r = self._crop_rect
        if r.width() < self.MIN_CROP or r.height() < self.MIN_CROP:
            return
        # Full-image crop is a no-op — just exit without modifying anything.
        img_w, img_h = self._editor._pil_image.size
        if (r.left() <= 0 and r.top() <= 0
                and r.x() + r.width() >= img_w
                and r.y() + r.height() >= img_h):
            self._editor._activate_tool("pan")
            return
        self._editor._save_undo(UndoChangeType.FULL)
        try:
            cropped = self._editor._pil_image.crop(
                (r.left(), r.top(), r.right(), r.bottom())
            )
            self._editor._pil_image = cropped
            self._editor._clear_annotations()
            self._editor._rebuild_display()
            self._editor._resize_canvas()
            self._editor._center_image_on_canvas()
            self._editor._modified = True
        except Exception:
            logger.exception("CropTool: failed to apply crop")
        # After cropping, exit crop mode — the user is done.
        self._editor._activate_tool("pan")

    def cancel_crop(self) -> None:
        self._editor._activate_tool("pan")

    # ── internals ────────────────────────────────────────────────────────

    _HANDLES = {
        "tl":  lambda r: (r.left(),  r.top()),
        "t":   lambda r: (r.center().x(), r.top()),
        "tr":  lambda r: (r.right(), r.top()),
        "l":   lambda r: (r.left(),  r.center().y()),
        "r":   lambda r: (r.right(), r.center().y()),
        "bl":  lambda r: (r.left(),  r.bottom()),
        "b":   lambda r: (r.center().x(), r.bottom()),
        "br":  lambda r: (r.right(), r.bottom()),
    }

    def _hit_test(self, canvas, screen_pos: QtCore.QPoint) -> Optional[str]:
        """Return handle id under *screen_pos*, or 'move', or None."""
        r = self._crop_rect
        if r is None:
            return None
        img = self._to_image_coords(canvas, screen_pos)
        ix, iy = img[0], img[1]
        # Check corner / edge handles first
        for name, fn in self._HANDLES.items():
            hx, hy = fn(r)
            if abs(ix - hx) <= self.HANDLE_R and abs(iy - hy) <= self.HANDLE_R:
                return name
        if r.contains(ix, iy):
            return "move"
        return None

    def _update_rect(self, img_x: int, img_y: int) -> None:
        sx, sy = self._drag_start_img
        dx, dy = img_x - sx, img_y - sy
        r = self._drag_start_rect
        img_w, img_h = self._editor._pil_image.size
        d = self._dragging

        nl, nt = r.left(), r.top()
        nr, nb = r.right(), r.bottom()

        if d == "move":
            nw, nh = r.width(), r.height()
            nl = max(0, min(r.left() + dx, img_w - nw))
            nt = max(0, min(r.top() + dy, img_h - nh))
            nr, nb = nl + nw, nt + nh
        else:
            if d in ("tl", "l", "bl"):
                nl = max(0, min(r.left() + dx, r.right() - self.MIN_CROP))
            if d in ("tr", "r", "br"):
                nr = max(r.left() + self.MIN_CROP, min(r.right() + dx, img_w))
            if d in ("tl", "t", "tr"):
                nt = max(0, min(r.top() + dy, r.bottom() - self.MIN_CROP))
            if d in ("bl", "b", "br"):
                nb = max(r.top() + self.MIN_CROP, min(r.bottom() + dy, img_h))

        self._crop_rect = QtCore.QRect(nl, nt, nr - nl, nb - nt)

    def _redraw_overlay(self) -> None:
        overlay = self._editor._overlay_pixmap
        overlay.fill(QtCore.Qt.GlobalColor.transparent)
        r = self._crop_rect
        if r is None:
            return

        painter = QtGui.QPainter(overlay)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        img_w, img_h = self._editor._pil_image.size

        # Dim outside the crop rect
        dim = QtGui.QColor(0, 0, 0, 130)
        painter.setBrush(QtGui.QBrush(dim))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        # top / bottom / left / right strips
        if r.top() > 0:
            painter.drawRect(0, 0, img_w, r.top())
        if r.bottom() < img_h:
            painter.drawRect(0, r.bottom(), img_w, img_h - r.bottom())
        if r.left() > 0:
            painter.drawRect(0, r.top(), r.left(), r.height())
        if r.right() < img_w:
            painter.drawRect(r.right(), r.top(), img_w - r.right(), r.height())

        # Crop border
        pen = QtGui.QPen(QtGui.QColor("#5FC98A"), 1.5,
                         QtCore.Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRect(r)

        # Rule-of-thirds guides
        tw, th = r.width() / 3.0, r.height() / 3.0
        gpen = QtGui.QPen(QtGui.QColor(255, 255, 255, 70), 0.5,
                          QtCore.Qt.PenStyle.DashLine)
        painter.setPen(gpen)
        for i in range(1, 3):
            painter.drawLine(
                QtCore.QPointF(r.left() + tw * i, r.top()),
                QtCore.QPointF(r.left() + tw * i, r.bottom()),
            )
            painter.drawLine(
                QtCore.QPointF(r.left(), r.top() + th * i),
                QtCore.QPointF(r.right(), r.top() + th * i),
            )

        # Corner handles (white fill + green border)
        corner_sz = 7
        edge_sz = 5
        painter.setPen(QtGui.QPen(QtGui.QColor("#5FC98A"), 1.5))
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
        for name, fn in self._HANDLES.items():
            hx, hy = fn(r)
            sz = corner_sz if len(name) == 2 else edge_sz
            painter.drawRect(QtCore.QRectF(
                hx - sz / 2.0, hy - sz / 2.0, sz, sz,
            ))

        # Dimension label (e.g. "800 × 600") at top-right corner
        dim_text = f"{r.width()} × {r.height()}"
        font = QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.SystemFont.GeneralFont
        )
        font.setPixelSize(11)
        painter.setFont(font)
        fm = QtGui.QFontMetricsF(font)
        text_rect = fm.boundingRect(dim_text)
        pad = 4.0
        bw = text_rect.width() + pad * 2
        bh = text_rect.height() + pad * 2
        # Prefer above the rect; flip below if too close to the top edge
        label_x = r.right() - bw - 4
        label_y = r.top() - bh - 4
        if label_y < 2:
            label_y = r.top() + 4
        bg = QtCore.QRectF(label_x, label_y, bw, bh)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(0, 0, 0, 160)))
        painter.drawRoundedRect(bg, 3, 3)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff")))
        painter.drawText(bg.adjusted(pad, pad, -pad, -pad),
                         QtCore.Qt.AlignmentFlag.AlignCenter, dim_text)

        painter.end()

        # Keep floating action buttons pinned below the crop rect
        self._position_action_buttons()


class ShapeTool(BaseTool):
    """Draw rectangle or ellipse outlines on the annotations layer."""

    def __init__(self, editor: "ImageEditorWindow", shape: str = "rectangle"):
        super().__init__(editor)
        self._shape = shape  # "rectangle" or "ellipse"
        self._color = QtGui.QColor("#FF4444")
        self._size = 3
        self._start_pt: Optional[tuple[int, int]] = None
        self._end_pt: Optional[tuple[int, int]] = None

    @property
    def color(self) -> QtGui.QColor: return self._color
    @color.setter
    def color(self, v: QtGui.QColor): self._color = v
    @property
    def size(self) -> int: return self._size
    @size.setter
    def size(self, v: int): self._size = v

    def tool_id(self) -> str:
        return self._shape

    def on_activate(self) -> None:
        self._editor._canvas.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._editor._save_undo(UndoChangeType.ANNOTATIONS)
            self._start_pt = self._to_image_coords(canvas, event.position().toPoint())
            self._end_pt = self._start_pt
            return True
        return False

    def on_mouse_move(self, canvas, event) -> bool:
        if self._start_pt is not None and (
            event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            self._end_pt = self._to_image_coords(canvas, event.position().toPoint())
            self._redraw_overlay()
            canvas.update()
            return True
        return False

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._start_pt is not None:
            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            self._draw_shape(self._editor._annotations_pixmap)
            self._editor._modified = True
            self._start_pt = None
            self._end_pt = None
            canvas.update()
            return True
        return False

    def on_key_press(self, canvas, event) -> bool:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self._start_pt = None
            self._end_pt = None
            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            canvas.update()
            return True
        return False

    def _redraw_overlay(self) -> None:
        self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._draw_shape(self._editor._overlay_pixmap)

    def _draw_shape(self, target: QtGui.QPixmap) -> None:
        if self._start_pt is None or self._end_pt is None:
            return
        x1, y1 = self._start_pt
        x2, y2 = self._end_pt
        rect = QtCore.QRect(
            min(x1, x2), min(y1, y2),
            abs(x2 - x1), abs(y2 - y1),
        )
        painter = QtGui.QPainter(target)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        pen = QtGui.QPen(self._color, self._size,
                         QtCore.Qt.PenStyle.SolidLine,
                         QtCore.Qt.PenCapStyle.SquareCap,
                         QtCore.Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        if self._shape == "rectangle":
            painter.drawRect(rect)
        else:
            painter.drawEllipse(rect)
        painter.end()


class ArrowTool(BaseTool):
    """Draw arrows with an arrowhead on the annotations layer."""

    def __init__(self, editor: "ImageEditorWindow"):
        super().__init__(editor)
        self._color = QtGui.QColor("#FF4444")
        self._size = 3
        self._start_pt: Optional[tuple[int, int]] = None
        self._end_pt: Optional[tuple[int, int]] = None

    @property
    def color(self) -> QtGui.QColor: return self._color
    @color.setter
    def color(self, v: QtGui.QColor): self._color = v
    @property
    def size(self) -> int: return self._size
    @size.setter
    def size(self, v: int): self._size = v

    def tool_id(self) -> str:
        return "arrow"

    def on_activate(self) -> None:
        self._editor._canvas.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._editor._save_undo(UndoChangeType.ANNOTATIONS)
            self._start_pt = self._to_image_coords(canvas, event.position().toPoint())
            self._end_pt = self._start_pt
            return True
        return False

    def on_mouse_move(self, canvas, event) -> bool:
        if self._start_pt is not None and (
            event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            self._end_pt = self._to_image_coords(canvas, event.position().toPoint())
            self._redraw_overlay()
            canvas.update()
            return True
        return False

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._start_pt is not None:
            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            self._draw_arrow(self._editor._annotations_pixmap)
            self._editor._modified = True
            self._start_pt = None
            self._end_pt = None
            canvas.update()
            return True
        return False

    def on_key_press(self, canvas, event) -> bool:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self._start_pt = None
            self._end_pt = None
            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            canvas.update()
            return True
        return False

    def _redraw_overlay(self) -> None:
        self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._draw_arrow(self._editor._overlay_pixmap)

    def _draw_arrow(self, target: QtGui.QPixmap) -> None:
        if self._start_pt is None or self._end_pt is None:
            return
        import math
        x1, y1 = self._start_pt
        x2, y2 = self._end_pt

        painter = QtGui.QPainter(target)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        # Line
        pen = QtGui.QPen(self._color, self._size,
                         QtCore.Qt.PenStyle.SolidLine,
                         QtCore.Qt.PenCapStyle.RoundCap,
                         QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))

        # Arrowhead (only if line has length)
        dx, dy = x2 - x1, y2 - y1
        line_len = math.hypot(dx, dy)
        if line_len < 1:
            painter.end()
            return

        angle = math.atan2(dy, dx)
        arrow_len = max(10.0, self._size * 4.0)
        spread = 0.45  # radians (~26°)

        px = x2 - arrow_len * math.cos(angle - spread)
        py = y2 - arrow_len * math.sin(angle - spread)
        qx = x2 - arrow_len * math.cos(angle + spread)
        qy = y2 - arrow_len * math.sin(angle + spread)

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QBrush(self._color))
        arrowhead = QtGui.QPolygonF([
            QtCore.QPointF(x2, y2),
            QtCore.QPointF(px, py),
            QtCore.QPointF(qx, qy),
        ])
        painter.drawPolygon(arrowhead)
        painter.end()


class PanTool(BaseTool):
    """Hand/grab tool: drag to pan the canvas (Photoshop-style)."""

    def __init__(self, editor: "ImageEditorWindow"):
        super().__init__(editor)
        self._last_pos: Optional[QtCore.QPointF] = None

    def tool_id(self) -> str:
        return "pan"

    def on_activate(self) -> None:
        self._editor._canvas.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._last_pos = event.globalPosition()
            canvas.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            return True
        return False

    def on_mouse_move(self, canvas, event) -> bool:
        if self._last_pos is not None and (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            cur = event.globalPosition()
            delta = cur - self._last_pos
            self._last_pos = cur
            sa = self._editor._scroll_area
            # Adjust scrollbars (inverse of delta because we drag the content)
            h_bar = sa.horizontalScrollBar()
            v_bar = sa.verticalScrollBar()
            h_bar.setValue(int(h_bar.value() - delta.x()))
            v_bar.setValue(int(v_bar.value() - delta.y()))
            return True
        return False

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._last_pos = None
            canvas.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            return True
        return False


class TextTool(BaseTool):
    """Text annotation tool with WeChat-style UX:
    - Click empty space: create new text.
    - Click existing text: select and drag.
    - Double-click text: edit.
    - Enter/FocusOut: commit.
    """

    _FONT_DIR = None
    _FONT_CACHE: dict[str, str] = {}

    def __init__(self, editor: "ImageEditorWindow"):
        super().__init__(editor)
        # Use the system default font — safe on every platform / locale
        self.font_family = QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.SystemFont.GeneralFont
        ).family()
        self.font_size = 24
        self.color = QtGui.QColor("#FFFFFF")
        
        self._dragging_item: Optional[TextItem] = None
        self._drag_offset = QtCore.QPointF()
        self._editing_widget: Optional[_InlineTextEditor] = None

    def tool_id(self) -> str:
        return "text"

    def on_activate(self) -> None:
        self._editor._canvas.setCursor(QtCore.Qt.CursorShape.IBeamCursor)

    def on_deactivate(self) -> None:
        if self._editing_widget:
            self._editing_widget.commit_edit()
        self._dragging_item = None

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return False
        
        # 1. Close active editor if clicking elsewhere
        if self._editing_widget:
            self._editing_widget.commit_edit()

        # 2. Hit test existing items (reversed to catch top-most)
        pos = event.position()
        hit_idx = self._hit_test(canvas, pos)
        
        if hit_idx != -1:
            # Found an item -> start dragging
            self._dragging_item = self._editor._text_items[hit_idx]
            # Calculate offset in image space
            img_pos = self._to_image_coords(canvas, pos.toPoint())
            self._drag_offset = QtCore.QPointF(img_pos[0], img_pos[1]) - self._dragging_item.img_pos
            canvas.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
            return True
        
        # 3. No hit -> create new item
        img_pt = self._to_image_coords(canvas, pos.toPoint())
        new_item = TextItem("", QtCore.QPointF(img_pt[0], img_pt[1]),
                            QtGui.QColor(self.color), self.font_family, self.font_size)
        self._editor._save_undo(UndoChangeType.TEXT)
        self._editor._text_items.append(new_item)
        self._spawn_editor(canvas, new_item)
        return True

    def on_mouse_move(self, canvas, event) -> bool:
        if self._dragging_item and (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            img_pt = self._to_image_coords(canvas, event.position().toPoint())
            self._dragging_item.img_pos = QtCore.QPointF(img_pt[0], img_pt[1]) - self._drag_offset
            self._editor._modified = True
            canvas.update()
            return True
        return False

    def on_mouse_release(self, canvas, event) -> bool:
        if self._dragging_item:
            self._dragging_item = None
            canvas.setCursor(QtCore.Qt.CursorShape.IBeamCursor)
            return True
        return False

    def on_mouse_double_click(self, canvas, event) -> bool:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return False
        
        hit_idx = self._hit_test(canvas, event.position())
        if hit_idx != -1:
            item = self._editor._text_items[hit_idx]
            self._spawn_editor(canvas, item)
            return True
        return False

    def _hit_test(self, canvas, screen_pos) -> int:
        """Return index of item at screen_pos, or -1."""
        scale = self._editor._effective_scale()
        offset = canvas._image_offset()
        
        # Test in reverse order (top items first)
        for i in range(len(self._editor._text_items)-1, -1, -1):
            item = self._editor._text_items[i]
            if not item.text: continue
            
            # Calculate bounding box in screen space
            fs = max(1, int(item.font_size * scale))
            font = QtGui.QFont(item.font_family, fs)
            metrics = QtGui.QFontMetrics(font)
            rect = metrics.boundingRect(item.text)
            
            screen_x = item.img_pos.x() * scale + offset.x()
            screen_y = item.img_pos.y() * scale + offset.y()
            
            item_rect = QtCore.QRect(int(screen_x), int(screen_y), 
                                      rect.width() + 10, rect.height() + 10)
            if item_rect.contains(screen_pos.toPoint()):
                return i
        return -1

    def _spawn_editor(self, canvas, item: TextItem) -> None:
        """Pop up the temporary QLineEdit for editing."""
        if self._editing_widget:
            self._editing_widget.commit_edit()

        # Adopt the tool's current color/font — re-editing applies
        # whatever is selected in the toolbar right now.
        item.color = QtGui.QColor(self.color)
        item.font_family = self.font_family
        item.font_size = self.font_size

        self._editing_widget = _InlineTextEditor(canvas, self, item)
        self._editing_widget.show()
        self._editing_widget.setFocus()
        canvas.update()

    def _sync_widgets(self) -> None:
        """Push current toolbar state to the active editor and refresh it."""
        if self._editing_widget:
            item = self._editing_widget._item
            item.color = QtGui.QColor(self.color)
            item.font_family = self.font_family
            item.font_size = self.font_size
            self._editing_widget._apply_style()
            self._editing_widget._update_geometry()
        self._editor._canvas.update()

    def _mark_modified(self) -> None:
        self._editor._modified = True

    @classmethod
    def _resolve_font_path(cls, family: str) -> Optional[str]:
        if family in cls._FONT_CACHE:
            return cls._FONT_CACHE[family]
        if cls._FONT_DIR is None:
            import sys
            cls._FONT_DIR = ("C:\\Windows\\Fonts" if sys.platform == "win32"
                             else "/usr/share/fonts")
        known: dict[str, str] = {
            "Microsoft YaHei": "msyh.ttc", "Microsoft JhengHei": "msjh.ttc",
            "SimSun": "simsun.ttc", "SimHei": "simhei.ttf",
            "KaiTi": "simkai.ttf", "Arial": "arial.ttf",
            "Consolas": "consola.ttf", "Courier New": "cour.ttf",
            "Times New Roman": "times.ttf",
        }
        filename = known.get(family)
        import os
        if filename:
            path = os.path.join(cls._FONT_DIR, filename)
            if os.path.isfile(path):
                cls._FONT_CACHE[family] = path
                return path
        for ext in (".ttf", ".ttc"):
            path = os.path.join(cls._FONT_DIR, family + ext)
            if os.path.isfile(path):
                cls._FONT_CACHE[family] = path
                return path
        cls._FONT_CACHE[family] = ""
        return None


class _InlineTextEditor(QtWidgets.QLineEdit):
    """Temporary editor widget that appears only during text entry."""

    def __init__(self, parent: QtWidgets.QWidget, tool: "TextTool", item: TextItem):
        super().__init__(parent)
        self._tool = tool
        self._item = item
        self._before_edit_text = item.text
        
        self.setText(item.text)
        self.setFrame(False)
        self._update_geometry()
        self._apply_style()
        
        self.textChanged.connect(self._on_text_changed)

    def _apply_style(self) -> None:
        scale = self._tool._editor._effective_scale()
        fs = max(10, int(self._item.font_size * scale))
        ff = self._item.font_family
        color = self._item.color.name()
        self.setStyleSheet(
            f"QLineEdit {{ background: rgba(30,30,30,180); border: 1px solid #5FC98A;"
            f"border-radius: 2px; color: {color}; padding: 2px 4px;"
            f"font-size: {fs}px;"
            f"selection-background-color: #5FC98A; }}"
        )

    def _update_geometry(self) -> None:
        canvas = self.parentWidget()
        scale = self._tool._editor._effective_scale()
        offset = canvas._image_offset()

        screen_x = self._item.img_pos.x() * scale + offset.x()
        screen_y = self._item.img_pos.y() * scale + offset.y()

        fm = self.fontMetrics()
        # Scale minimum size and padding with zoom so the widget grows /
        # shrinks in proportion to the image, not in absolute pixels.
        min_w = int(100 * scale)
        pad_w = int(20 * scale)
        pad_h = int(10 * scale)
        w = max(min_w, fm.horizontalAdvance(self.text()) + pad_w)
        h = fm.height() + pad_h
        self.setGeometry(int(screen_x), int(screen_y), int(w), int(h))

    def _on_text_changed(self) -> None:
        self._update_geometry()

    def commit_edit(self) -> None:
        txt = self.text().strip()
        if txt:
            self._item.text = txt
            self._tool._mark_modified()
        elif not self._before_edit_text:
            # New empty item canceled -> remove it
            if self._item in self._tool._editor._text_items:
                self._tool._editor._text_items.remove(self._item)
        else:
            # Existing item cleared -> revert or remove? WeChat reverts.
            self._item.text = self._before_edit_text
            
        self._tool._editing_widget = None
        self.parentWidget().update()
        self.deleteLater()

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if e.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            self.commit_edit()
            e.accept()
        elif e.key() == QtCore.Qt.Key.Key_Escape:
            self.setText(self._before_edit_text)
            self.commit_edit()
            e.accept()
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e: QtGui.QFocusEvent) -> None:
        self.commit_edit()
        super().focusOutEvent(e)


# ── Editor Canvas ────────────────────────────────────────────────────────────

class EditorCanvas(QtWidgets.QWidget):
    """Custom QWidget that renders the working image and tool overlays.
    Forwards mouse/key events to the active tool.
    """

    def __init__(self, editor_window: "ImageEditorWindow"):
        super().__init__()
        self._editor = editor_window
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def _image_offset(self) -> QtCore.QPointF:
        """Offset to center the image within the canvas."""
        if not self._editor._display_pixmap:
            return QtCore.QPointF(0, 0)
        pm = self._editor._display_pixmap
        scale = self._editor._effective_scale()
        scaled_w = pm.width() * scale
        scaled_h = pm.height() * scale
        # Center the image in the canvas (which is now larger than viewport)
        ox = (self.width() - scaled_w) / 2.0
        oy = (self.height() - scaled_h) / 2.0
        return QtCore.QPointF(ox, oy)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        scale = self._editor._effective_scale()
        # <100% → smooth interpolation (clean thumbnails).
        # ≥100% → no hint = nearest-neighbour (sharp pixels, fast).
        if scale < 1.0:
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QtGui.QColor("#1a1a1a"))

        # Checkerboard only matters when zoomed out (transparency visible
        # around the image).  Skip at ≥100% to avoid millions of cells.
        if scale < 1.0:
            self._draw_checkerboard(painter, event.rect())

        pm = self._editor._display_pixmap
        if pm:
            scale = self._editor._effective_scale()
            offset = self._image_offset()
            painter.save()
            painter.translate(offset)
            painter.scale(scale, scale)
            painter.drawPixmap(0, 0, pm)
            # Annotations layer
            if self._editor._annotations_pixmap:
                painter.drawPixmap(0, 0, self._editor._annotations_pixmap)
            # Active stroke (per-stroke pixmap during drawing)
            tool = self._editor._active_tool
            spm = getattr(tool, '_stroke_pixmap', None) if tool else None
            if spm:
                painter.drawPixmap(0, 0, spm)
            # Overlay (tool feedback: crop rect, mosaic rect, etc.)
            if self._editor._overlay_pixmap:
                painter.drawPixmap(0, 0, self._editor._overlay_pixmap)
            painter.restore()

        # Render text items (always visible)
        self._render_text_items(painter)

        # Tool-specific paint decorations
        tool = self._editor._active_tool
        if tool:
            tool.on_paint(self, painter)

        painter.end()

    def _render_text_items(self, painter: QtGui.QPainter) -> None:
        """Render persistent text items."""
        scale = self._editor._effective_scale()
        offset = self._image_offset()

        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)

        # Check if we are currently editing an item (to skip drawing it)
        editing_item = None
        text_tool = self._editor._tools.get("text")
        if text_tool and hasattr(text_tool, "_editing_widget") and text_tool._editing_widget:
            editing_item = text_tool._editing_widget._item

        for item in self._editor._text_items:
            if item == editing_item:
                continue

            fs = max(1, int(item.font_size * scale))
            font = QtGui.QFont(item.font_family, fs)
            painter.setFont(font)
            painter.setPen(item.color)

            screen_x = item.img_pos.x() * scale + offset.x()
            screen_y = item.img_pos.y() * scale + offset.y()

            metrics = painter.fontMetrics()
            painter.drawText(int(screen_x), int(screen_y + metrics.ascent()), item.text)

        painter.restore()

    def _draw_checkerboard(self, painter: QtGui.QPainter,
                           clip_rect: QtCore.QRect) -> None:
        """Draw a subtle checkerboard pattern to indicate transparency.
        Restricted to *clip_rect* (the paint-event dirty rect) so the
        double loop stays cheap at any zoom level."""
        pm = self._editor._display_pixmap
        if not pm:
            return
        scale = self._editor._effective_scale()
        offset = self._image_offset()
        scaled_w = pm.width() * scale
        scaled_h = pm.height() * scale
        img_rect = QtCore.QRectF(offset.x(), offset.y(), scaled_w, scaled_h)

        cs = 8  # checker size
        light = QtGui.QColor("#2a2a2a")
        dark = QtGui.QColor("#222222")

        x_start = max(int(clip_rect.x() / cs) * cs,
                      int(img_rect.x() / cs) * cs)
        y_start = max(int(clip_rect.y() / cs) * cs,
                      int(img_rect.y() / cs) * cs)
        x_end = min(int(clip_rect.right()), int(img_rect.right()))
        y_end = min(int(clip_rect.bottom()), int(img_rect.bottom()))

        for y in range(y_start, y_end, cs):
            for x in range(x_start, x_end, cs):
                if ((x // cs) + (y // cs)) % 2 == 0:
                    c = light
                else:
                    c = dark
                rx = max(x, img_rect.x())
                ry = max(y, img_rect.y())
                rw = min(x + cs, x_end) - rx
                rh = min(y + cs, y_end) - ry
                painter.fillRect(QtCore.QRectF(rx, ry, rw, rh), c)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        tool = self._editor._active_tool
        if tool and tool.on_mouse_press(self, event):
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        tool = self._editor._active_tool
        if tool and tool.on_mouse_move(self, event):
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        tool = self._editor._active_tool
        if tool and tool.on_mouse_release(self, event):
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        tool = self._editor._active_tool
        if tool and tool.on_mouse_double_click(self, event):
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        """Zoom in/out centered on cursor position."""
        editor = self._editor
        if not editor._display_pixmap:
            event.ignore()
            return

        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return

        # cursor_pos is in canvas coords (already includes scroll offset)
        cursor_pos = event.position()
        old_offset = self._image_offset()

        # Image-space coordinate under the cursor (physical pixels)
        old_effective = editor._effective_scale()
        img_x = (cursor_pos.x() - old_offset.x()) / old_effective
        img_y = (cursor_pos.y() - old_offset.y()) / old_effective

        # Apply zoom factor (10% per step)
        factor = 1.10 if delta > 0 else 1.0 / 1.10
        new_scale = editor._scale * factor
        new_scale = max(0.10, min(new_scale, 5.0))
        if abs(new_scale - editor._scale) < 0.001:
            event.accept()
            return
        editor._scale = new_scale
        editor._resize_canvas()

        # Compute new scroll position: the image pixel under the cursor
        # before zoom should stay under the cursor after zoom.
        old_scroll_x = editor._scroll_area.horizontalScrollBar().value()
        old_scroll_y = editor._scroll_area.verticalScrollBar().value()
        new_offset = self._image_offset()
        new_effective = editor._effective_scale()
        new_scroll_x = int(img_x * new_effective + new_offset.x() - cursor_pos.x() + old_scroll_x)
        new_scroll_y = int(img_y * new_effective + new_offset.y() - cursor_pos.y() + old_scroll_y)

        h_bar = editor._scroll_area.horizontalScrollBar()
        v_bar = editor._scroll_area.verticalScrollBar()
        h_bar.setValue(max(h_bar.minimum(), min(new_scroll_x, h_bar.maximum())))
        v_bar.setValue(max(v_bar.minimum(), min(new_scroll_y, v_bar.maximum())))

        self.update()
        editor._update_zoom_label()
        event.accept()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        tool = self._editor._active_tool
        if tool and tool.on_key_press(self, event):
            event.accept()
        else:
            super().keyPressEvent(event)


# ── Color swatch popup ────────────────────────────────────────────────────────

# Preset color palette: 4 columns × 4 rows
_SWATCH_COLORS = [
    # Row 1
    ("#FF4444", "Red"),     ("#FF8800", "Orange"),  ("#FFFF00", "Yellow"),  ("#5FC98A", "Green"),
    # Row 2
    ("#00CCCC", "Cyan"),    ("#4488FF", "Blue"),    ("#8844FF", "Purple"),  ("#FF44AA", "Pink"),
    # Row 3
    ("#FFFFFF", "White"),   ("#CCCCCC", "LtGray"),  ("#888888", "Gray"),   ("#444444", "DkGray"),
    # Row 4
    ("#000000", "Black"),
]

_SWATCH_COLS = 4
_SWATCH_SIZE = 26  # diameter
_SWATCH_PAD = 4
_SWATCH_GAP = 2


class _ColorButton(QtWidgets.QWidget):
    """Custom circular color selection button — plain QWidget to avoid
    QPushButton's native-OS background on Windows."""

    clicked = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._color = QtGui.QColor("#FFFFFF")
        self._hovered = False

    def setColor(self, color: QtGui.QColor) -> None:
        self._color = color
        self.update()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = False
        self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        # Draw the perfectly circular color swatch
        rect = QtCore.QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)

        # Solid fill
        painter.setBrush(QtGui.QBrush(self._color))

        # Border
        if self._hovered:
            pen = QtGui.QPen(QtGui.QColor("#5FC98A"), 2.0)
        else:
            pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 102), 2.0)

        painter.setPen(pen)
        painter.drawEllipse(rect)
        painter.end()


class _SwatchPopup(QtWidgets.QFrame):
    """Lightweight color swatch grid popup — replaces QColorDialog."""

    color_selected = QtCore.pyqtSignal(QtGui.QColor)

    def __init__(self, parent: QtWidgets.QWidget, alpha: int = 255):
        super().__init__(parent)
        self.setWindowFlags(
            QtCore.Qt.WindowType.Popup
            | QtCore.Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("_SwatchPopup { background: #333; border: 1px solid #555; border-radius: 6px; }")

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(_SWATCH_PAD + 2, _SWATCH_PAD + 2, _SWATCH_PAD + 2, _SWATCH_PAD + 2)
        grid.setSpacing(_SWATCH_GAP)

        for i, (hex_color, tooltip) in enumerate(_SWATCH_COLORS):
            btn = QtWidgets.QPushButton()
            btn.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tooltip)
            r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
            # White swatch needs a subtle border to be visible on dark bg
            border = "1px solid rgba(255,255,255,30)" if hex_color == "#FFFFFF" else "none"
            btn.setStyleSheet(
                f"QPushButton {{"
                f"  background-color: rgba({r},{g},{b},{alpha});"
                f"  border: {border}; border-radius: {_SWATCH_SIZE // 2}px;"
                f"}}"
                f"QPushButton:hover {{ border: 2px solid #fff; }}"
            )
            btn.clicked.connect(lambda checked, c=QtGui.QColor(hex_color): self._on_pick(c))
            row, col = divmod(i, _SWATCH_COLS)
            grid.addWidget(btn, row, col)

        self.adjustSize()

    def _on_pick(self, color: QtGui.QColor) -> None:
        self.color_selected.emit(color)
        self.close()

    def show_near(self, anchor: QtWidgets.QWidget) -> None:
        """Position popup below (or above) the anchor widget."""
        pos = anchor.mapToGlobal(QtCore.QPoint(0, anchor.height() + 3))
        screen = QtWidgets.QApplication.screenAt(pos)
        if screen:
            sg = screen.availableGeometry()
            if pos.y() + self.height() > sg.bottom():
                pos = anchor.mapToGlobal(QtCore.QPoint(0, -self.height() - 3))
            if pos.x() + self.width() > sg.right():
                pos.setX(sg.right() - self.width() - 4)
        self.move(pos)
        self.show()


# ── Editor Window ────────────────────────────────────────────────────────────

class ImageEditorWindow(QtWidgets.QWidget):
    """Main editor window with toolbar, canvas, and controls."""

    MAX_UNDO = 25

    def __init__(
        self,
        pil_image: Image.Image,
        translate_fn: Callable[[str], str],
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(parent)
        self._tr = translate_fn
        self._original_pil = pil_image.copy()
        self._pil_image = pil_image.copy()

        # Layers
        self._display_pixmap: Optional[QtGui.QPixmap] = None
        self._annotations_pixmap: Optional[QtGui.QPixmap] = None
        self._overlay_pixmap: Optional[QtGui.QPixmap] = None

        # State
        self._scale = 1.0
        # Device pixel ratio: at 100% zoom the image displays at 1:1
        # physical-pixel mapping, matching pinned-image behaviour.
        self._dpr = current_dpr()
        self._modified = False
        self._undo_stack: list[_UndoEntry] = []
        self._redo_stack: list[_UndoEntry] = []
        self._active_tool: Optional[BaseTool] = None
        self._tools: dict[str, BaseTool] = {}
        self._tool_buttons: dict[str, QtWidgets.QToolButton] = {}
        self._option_widgets: dict[tuple[str, str], QtWidgets.QWidget] = {}
        # Text annotation data (persistent items)
        self._text_items: list[TextItem] = []

        self._setup_ui()
        self._setup_tools()
        self._init_from_image()
        self._activate_tool("pan")

    # ── UI Setup ──────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self.setObjectName("editorWindow")
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.Window
        )
        self.setStyleSheet(EDITOR_WINDOW_STYLE)
        self.setWindowTitle(self._tr("editor_title"))
        self.setMinimumSize(640, 480)
        self.resize(960, 700)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Title bar
        title_bar = self._create_title_bar()
        main_layout.addWidget(title_bar)

        # Toolbar row 1 — tools + transforms
        toolbar1 = self._create_toolbar_row1()
        main_layout.addWidget(toolbar1)

        # Toolbar row 2 — tool options
        self._options_stack = QtWidgets.QStackedWidget()
        self._options_stack.setObjectName("optionsArea")
        self._options_stack.setStyleSheet(EDITOR_OPTIONS_STYLE)
        self._options_stack.setMaximumHeight(38)
        self._setup_option_pages()
        main_layout.addWidget(self._options_stack)

        # Canvas (in scroll area)
        self._canvas = EditorCanvas(self)
        self._scroll_area = QtWidgets.QScrollArea()
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setWidget(self._canvas)
        self._scroll_area.setStyleSheet("QScrollArea { border: none; background-color: #1a1a1a; }")
        self._scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        main_layout.addWidget(self._scroll_area, 1)

        # Status bar
        status = self._create_status_bar()
        main_layout.addWidget(status)

    def _create_title_bar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(36)
        bar.setStyleSheet("background-color: #1e1e1e; border-bottom: 1px solid #333;")
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(8)

        title = QtWidgets.QLabel(self._tr("editor_title"))
        title.setStyleSheet("color: #ccc; font-size: 13px; font-weight: 600; background: transparent; border: none;")
        layout.addWidget(title)
        layout.addStretch()

        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #999; font-size: 14px; }"
            "QPushButton:hover { background-color: rgba(255, 80, 80, 60); color: #fff; border-radius: 4px; }"
        )
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        # Make window draggable via title bar
        bar.mousePressEvent = self._title_bar_mouse_press
        bar.mouseMoveEvent = self._title_bar_mouse_move
        self._drag_pos = None
        return bar

    def _create_toolbar_row1(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setObjectName("toolbarRow")
        bar.setStyleSheet(EDITOR_TOOLBAR_ROW_STYLE)
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(3)

        # ── Tool groups (separated by thin vertical rules) ────────────────

        def _add_sep():
            s = QtWidgets.QFrame()
            s.setFrameShape(QtWidgets.QFrame.Shape.VLine)
            s.setStyleSheet("background-color: rgba(255,255,255,12); border: none;")
            s.setFixedWidth(1)
            s.setFixedHeight(20)
            layout.addSpacing(6)
            layout.addWidget(s)
            layout.addSpacing(6)

        def _add_tools(tools):
            for tid, label_key, icon_name in tools:
                btn = QtWidgets.QToolButton()
                btn.setIcon(_load_editor_icon(icon_name))
                btn.setIconSize(QtCore.QSize(20, 20))
                btn.setToolTip(self._tr(label_key))
                btn.setCheckable(True)
                btn.setStyleSheet(EDITOR_TOOL_BUTTON_STYLE)
                btn.clicked.connect(lambda checked, t=tid: self._activate_tool(t))
                layout.addWidget(btn)
                self._tool_buttons[tid] = btn

        # Group 1 — Draw
        _add_tools([
            ("brush", "tool_brush", "brush"),
            ("highlighter", "tool_highlighter", "highlighter"),
            ("eraser", "tool_eraser", "eraser"),
        ])

        _add_sep()

        # Group 2 — Shapes & Annotate
        _add_tools([
            ("rectangle", "tool_rectangle", "rectangle"),
            ("ellipse", "tool_ellipse", "ellipse"),
            ("arrow", "tool_arrow", "arrow"),
            ("text", "tool_text", "text"),
        ])

        _add_sep()

        # Group 3 — Modify
        _add_tools([
            ("mosaic", "tool_mosaic", "mosaic"),
            ("crop", "tool_crop", "crop"),
        ])

        layout.addStretch()

        # Undo / Redo
        self._undo_btn = QtWidgets.QPushButton()
        self._undo_btn.setIcon(_load_editor_icon("undo"))
        self._undo_btn.setIconSize(QtCore.QSize(20, 20))
        self._undo_btn.setToolTip(self._tr("editor_undo"))
        self._undo_btn.setShortcut("Ctrl+Z")
        self._undo_btn.setFixedSize(32, 28)
        self._undo_btn.setStyleSheet(EDITOR_PUSH_BUTTON_STYLE)
        self._undo_btn.clicked.connect(self._undo)
        self._undo_btn.setEnabled(False)
        layout.addWidget(self._undo_btn)

        self._redo_btn = QtWidgets.QPushButton()
        self._redo_btn.setIcon(_load_editor_icon("redo"))
        self._redo_btn.setIconSize(QtCore.QSize(20, 20))
        self._redo_btn.setToolTip(self._tr("editor_redo"))
        self._redo_btn.setShortcut("Ctrl+Y")
        self._redo_btn.setFixedSize(32, 28)
        self._redo_btn.setStyleSheet(EDITOR_PUSH_BUTTON_STYLE)
        self._redo_btn.clicked.connect(self._redo)
        self._redo_btn.setEnabled(False)
        layout.addWidget(self._redo_btn)

        _add_sep()

        # Pan (navigation utility)
        _add_tools([
            ("pan", "tool_pan", "pan"),
        ])

        return bar

    def _setup_option_pages(self) -> None:
        """Create tool-specific option pages in the stacked widget."""
        # Page 0: Brush options (color + size)
        page_brush = self._make_options_page(["color", "size"], "brush")
        self._options_stack.addWidget(page_brush)

        # Page 1: Highlighter options (color + size + opacity)
        page_hl = self._make_options_page(["color", "size", "opacity"], "highlighter")
        self._options_stack.addWidget(page_hl)

        # Page 2: Eraser options (size)
        page_eraser = self._make_options_page(["size"], "eraser")
        self._options_stack.addWidget(page_eraser)

        # Page 3: Mosaic options (block size)
        page_mosaic = self._make_options_page(["size"], "mosaic")
        self._options_stack.addWidget(page_mosaic)

        # Page 4: Crop tool — just a hint (buttons float below the image)
        page_crop = QtWidgets.QWidget()
        page_crop.setStyleSheet(EDITOR_OPTIONS_STYLE)
        crop_layout = QtWidgets.QHBoxLayout(page_crop)
        crop_layout.setContentsMargins(10, 2, 10, 2)
        crop_instruction = QtWidgets.QLabel(self._tr("editor_crop_instruction"))
        crop_instruction.setStyleSheet("color: #aaa; font-size: 11px; background: transparent;")
        crop_layout.addWidget(crop_instruction)
        crop_layout.addStretch()
        self._options_stack.addWidget(page_crop)

        # Page 5: Text options (font + size + color)
        page_text = self._make_options_page(["font", "font_size", "color"], "text")
        self._options_stack.addWidget(page_text)

        # Page 6: Pan tool (no options)
        page_pan = QtWidgets.QWidget()
        self._options_stack.addWidget(page_pan)

        # Page 7: Rectangle options (color + size)
        page_rect = self._make_options_page(["color", "size"], "rectangle")
        self._options_stack.addWidget(page_rect)

        # Page 8: Ellipse options (color + size)
        page_ellipse = self._make_options_page(["color", "size"], "ellipse")
        self._options_stack.addWidget(page_ellipse)

        # Page 9: Arrow options (color + size)
        page_arrow = self._make_options_page(["color", "size"], "arrow")
        self._options_stack.addWidget(page_arrow)

    PAGE_INDEX = {"brush": 0, "highlighter": 1, "eraser": 2, "mosaic": 3, "crop": 4, "text": 5,
                  "pan": 6, "rectangle": 7, "ellipse": 8, "arrow": 9}

    def _make_options_page(
        self, option_keys: list[str], tool_id: str
    ) -> QtWidgets.QWidget:
        """Create a horizontal bar of option widgets for a tool."""
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(8)

        for key in option_keys:
            if key == "color":
                lbl = QtWidgets.QLabel(self._tr("editor_color") + ":")
                lbl.setObjectName("optionLabel")
                layout.addWidget(lbl)
                btn = _ColorButton()
                btn.setToolTip(self._tr("editor_color"))
                btn.setObjectName(f"colorBtn_{tool_id}")
                btn.clicked.connect(lambda: self._pick_color(tool_id))
                layout.addWidget(btn)
                self._option_widgets[(tool_id, "colorBtn")] = btn

            elif key == "opacity":
                lbl = QtWidgets.QLabel(self._tr("editor_opacity") + ":")
                lbl.setObjectName("optionLabel")
                layout.addWidget(lbl)
                slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
                slider.setRange(10, 255)
                slider.setValue(80)
                slider.setFixedWidth(100)
                slider.setToolTip(self._tr("editor_opacity"))
                slider.setObjectName(f"opacitySlider_{tool_id}")
                slider.valueChanged.connect(
                    lambda v, t=tool_id: self._on_opacity_changed(t, v)
                )
                layout.addWidget(slider)
                self._option_widgets[(tool_id, "opacitySlider")] = slider
                val_lbl = QtWidgets.QLabel("80")
                val_lbl.setObjectName(f"opacityLabel_{tool_id}")
                val_lbl.setStyleSheet("color: #aaa; font-size: 11px; background: transparent; min-width: 24px;")
                slider.valueChanged.connect(lambda v, l=val_lbl: l.setText(str(v)))
                layout.addWidget(val_lbl)

            elif key == "size":
                lbl = QtWidgets.QLabel(self._tr("editor_size") + ":")
                lbl.setObjectName("optionLabel")
                layout.addWidget(lbl)
                slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
                slider.setFixedWidth(120)
                slider.setToolTip(self._tr("editor_size"))
                slider.setObjectName(f"sizeSlider_{tool_id}")
                # Configure range based on tool
                if tool_id == "mosaic":
                    slider.setRange(2, 40)
                    slider.setValue(10)
                elif tool_id == "eraser":
                    slider.setRange(3, 60)
                    slider.setValue(20)
                elif tool_id == "highlighter":
                    slider.setRange(5, 80)
                    slider.setValue(20)
                else:
                    slider.setRange(1, 50)
                    slider.setValue(3)
                slider.valueChanged.connect(
                    lambda v, t=tool_id: self._on_size_changed(t, v)
                )
                layout.addWidget(slider)
                self._option_widgets[(tool_id, "sizeSlider")] = slider
                val_lbl = QtWidgets.QLabel(str(slider.value()))
                val_lbl.setObjectName(f"sizeLabel_{tool_id}")
                val_lbl.setStyleSheet("color: #aaa; font-size: 11px; background: transparent; min-width: 20px;")
                slider.valueChanged.connect(lambda v, l=val_lbl: l.setText(str(v)))
                layout.addWidget(val_lbl)

            elif key == "font":
                lbl = QtWidgets.QLabel(self._tr("editor_font") + ":")
                lbl.setObjectName("optionLabel")
                layout.addWidget(lbl)
                combo = _EditorFontComboBox()
                combo.setWritingSystem(QtGui.QFontDatabase.WritingSystem.Any)
                # Use the system default — safe everywhere
                sys_family = QtGui.QFontDatabase.systemFont(
                    QtGui.QFontDatabase.SystemFont.GeneralFont
                ).family()
                idx = combo.findText(sys_family)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                combo.setObjectName(f"fontCombo_{tool_id}")
                combo.currentTextChanged.connect(
                    lambda t, tool=tool_id: self._on_font_changed(tool, t)
                )
                layout.addWidget(combo)
                self._option_widgets[(tool_id, "fontCombo")] = combo

            elif key == "font_size":
                lbl = QtWidgets.QLabel(self._tr("editor_font_size") + ":")
                lbl.setObjectName("optionLabel")
                layout.addWidget(lbl)
                # Word-style preset sizes
                _FONT_SIZES = ["8", "9", "10", "11", "12", "14", "16", "18",
                               "20", "24", "28", "36", "48", "72", "96", "144"]
                combo = _EditorComboBox()
                combo.addItems(_FONT_SIZES)
                combo.setEditable(True)  # allow typing custom sizes
                combo.setCurrentText("24")
                combo.setObjectName(f"fontSizeSpin_{tool_id}")
                combo.currentTextChanged.connect(
                    lambda t, tid=tool_id: self._on_font_size_text_changed(tid, t)
                )
                layout.addWidget(combo)
                self._option_widgets[(tool_id, "fontSizeSpin")] = combo

        layout.addStretch()
        return page

    def _create_status_bar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet("background-color: #1e1e1e; border-top: 1px solid rgba(255,255,255,8);")
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 10, 0)
        layout.setSpacing(6)

        self._status_label = QtWidgets.QLabel()
        self._status_label.setObjectName("statusLabel")
        self._status_label.setStyleSheet(EDITOR_STATUS_STYLE)
        layout.addWidget(self._status_label)
        layout.addStretch()

        # ── Copy button ──
        copy_label = self._tr("editor_copy")
        copy_btn = QtWidgets.QPushButton(f"  {copy_label}")
        copy_btn.setIcon(_load_editor_icon("copy"))
        copy_btn.setIconSize(QtCore.QSize(14, 14))
        copy_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(f"""
            QPushButton {{
                background: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 13px;
                padding: 4px 12px 4px 8px;
                color: #bbb;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: #383838;
                border-color: #4d4d4d;
                color: #e0e0e0;
            }}
            QPushButton:pressed {{
                background: #333;
            }}
        """)
        copy_btn.clicked.connect(self._copy_to_clipboard)
        layout.addWidget(copy_btn)

        # ── Save button — same dark-pill base, green accent on the icon ──
        save_label = self._tr("editor_save")
        save_btn = QtWidgets.QPushButton(f"  {save_label}")
        save_btn.setIcon(_load_editor_icon("save", QtGui.QColor("#5FC98A")))
        save_btn.setIconSize(QtCore.QSize(14, 14))
        save_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        save_btn.setShortcut("Ctrl+S")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 13px;
                padding: 5px 14px 5px 8px;
                color: #d0d0d0;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: #383838;
                border-color: #4d4d4d;
                color: #e8e8e8;
            }}
            QPushButton:pressed {{
                background: #333;
            }}
        """)
        save_btn.clicked.connect(self._save_as)
        layout.addWidget(save_btn)

        # Zoom indicator
        self._zoom_label = QtWidgets.QLabel()
        self._zoom_label.setObjectName("zoomLabel")
        self._zoom_label.setStyleSheet("color: #999; font-size: 11px; padding: 4px 10px; background: transparent;")
        layout.addWidget(self._zoom_label)
        return bar

    # ── Tools ─────────────────────────────────────────────────────────────

    def _setup_tools(self) -> None:
        self._tools = {
            "brush": BrushTool(self),
            "highlighter": HighlighterTool(self),
            "eraser": EraserTool(self),
            "mosaic": MosaicTool(self),
            "crop": CropTool(self),
            "text": TextTool(self),
            "rectangle": ShapeTool(self, "rectangle"),
            "ellipse": ShapeTool(self, "ellipse"),
            "arrow": ArrowTool(self),
            "pan": PanTool(self),
        }

    def _activate_tool(self, tool_id: str) -> None:
        if tool_id not in self._tools:
            return
        # Deactivate previous
        if self._active_tool:
            self._active_tool.on_deactivate()
        # Uncheck all tool buttons
        for tid, btn in self._tool_buttons.items():
            btn.setChecked(tid == tool_id)
        # Activate new
        self._active_tool = self._tools[tool_id]
        self._active_tool.on_activate()
        # Switch option page
        page_idx = self.PAGE_INDEX.get(tool_id, 0)
        self._options_stack.setCurrentIndex(page_idx)
        # Update option widget values from tool state
        self._sync_options_from_tool(tool_id)
        self._canvas.update()

    def _effective_scale(self) -> float:
        """Display scale that accounts for device pixel ratio.

        At 100 % zoom (_scale = 1.0) this returns 1 / dpr so the image
        occupies physical_px / dpr logical pixels — i.e. 1 image pixel
        maps to exactly 1 screen physical pixel, matching pinned-image
        behaviour on high-DPI displays.
        """
        return self._scale / self._dpr

    def _update_tool_cursor(self) -> None:
        """Update canvas cursor based on active tool and its size."""
        if self._active_tool and hasattr(self._active_tool, "size"):
            screen_size = int(self._active_tool.size * self._effective_scale())
            self._canvas.setCursor(_make_circle_cursor(screen_size))

    def _sync_options_from_tool(self, tool_id: str) -> None:
        """Sync option widget values from the tool's current state."""
        tool = self._tools.get(tool_id)
        if not tool:
            return
        ow = self._option_widgets
        # Color button
        if hasattr(tool, "color"):
            btn = ow.get((tool_id, "colorBtn"))
            if btn:
                btn.setColor(tool.color)
        # Size slider
        slider = ow.get((tool_id, "sizeSlider"))
        if slider and hasattr(tool, "size"):
            slider.blockSignals(True)
            slider.setValue(tool.size)
            slider.blockSignals(False)
        # Opacity slider
        op = ow.get((tool_id, "opacitySlider"))
        if op and hasattr(tool, "color"):
            op.blockSignals(True)
            op.setValue(tool.color.alpha())
            op.blockSignals(False)
        # Font combo
        fc = ow.get((tool_id, "fontCombo"))
        if fc and hasattr(tool, "font_family"):
            fc.blockSignals(True)
            idx = fc.findText(tool.font_family)
            if idx >= 0:
                fc.setCurrentIndex(idx)
            fc.blockSignals(False)
        # Font size combo
        fs = ow.get((tool_id, "fontSizeSpin"))
        if fs and hasattr(tool, "font_size"):
            fs.blockSignals(True)
            fs.setCurrentText(str(tool.font_size))
            fs.blockSignals(False)

    def _pick_color(self, tool_id: str) -> None:
        tool = self._tools.get(tool_id)
        if not tool or not hasattr(tool, "color"):
            return
        anchor = self._option_widgets.get((tool_id, "colorBtn"))
        popup = _SwatchPopup(self, alpha=tool.color.alpha())
        popup.color_selected.connect(
            lambda c, tid=tool_id: self._on_color_picked(tid, c)
        )
        popup.show_near(anchor or self)

    def _on_color_picked(self, tool_id: str, color: QtGui.QColor) -> None:
        tool = self._tools.get(tool_id)
        if not tool or not hasattr(tool, "color"):
            return
        # Preserve existing alpha (opacity slider) — swatch only sets RGB
        color.setAlpha(tool.color.alpha())
        tool.color = color
        self._sync_options_from_tool(tool_id)
        if tool_id == "text":
            tool._sync_widgets()

    def _on_size_changed(self, tool_id: str, value: int) -> None:
        tool = self._tools.get(tool_id)
        if tool and hasattr(tool, "size"):
            tool.size = value
        if tool and hasattr(tool, "block_size"):
            tool.block_size = value
        
        # Refresh cursor if this is the active tool
        if self._active_tool == tool:
            self._update_tool_cursor()
            
        if tool_id == "text":
            tool._sync_widgets()

    def _on_opacity_changed(self, tool_id: str, value: int) -> None:
        tool = self._tools.get(tool_id)
        if tool and hasattr(tool, "color"):
            c = tool.color
            c.setAlpha(value)
            tool.color = c
            self._sync_options_from_tool(tool_id)
            if tool_id == "text":
                tool._sync_widgets()

    def _on_font_changed(self, tool_id: str, family: str) -> None:
        tool = self._tools.get(tool_id)
        if tool and hasattr(tool, "font_family"):
            tool.font_family = family
            if tool_id == "text":
                tool._sync_widgets()

    def _on_font_size_text_changed(self, tool_id: str, text: str) -> None:
        try:
            value = int(text.strip())
        except ValueError:
            return
        tool = self._tools.get(tool_id)
        if tool and hasattr(tool, "font_size"):
            tool.font_size = max(1, min(value, 999))
            if tool_id == "text":
                tool._sync_widgets()

    # ── Image init & text items ─────────────────────────────────────────

    def _init_from_image(self) -> None:
        self._display_pixmap = _pil_to_qpixmap(self._pil_image)
        img_size = QtCore.QSize(*self._pil_image.size)
        self._annotations_pixmap = QtGui.QPixmap(img_size)
        self._annotations_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._overlay_pixmap = QtGui.QPixmap(img_size)
        self._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._update_status()
        self._resize_canvas()
        # Ensure image starts centered in the viewport
        QtCore.QTimer.singleShot(10, self._center_image_on_canvas)

    def _resize_canvas(self) -> None:
        """Size the canvas to be larger than the viewport to allow panning
        even when the image is smaller than the window.
        """
        if not self._display_pixmap:
            return
        vp = self._scroll_area.viewport()
        if not vp:
            return
        vw, vh = vp.width(), vp.height()
        pm = self._display_pixmap
        scale = self._effective_scale()
        iw = int(pm.width() * scale)
        ih = int(pm.height() * scale)

        # Large padding allows dragging the image anywhere
        pad_w = vw * 0.9
        pad_h = vh * 0.9
        cw = max(vw, iw + pad_w * 2)
        ch = max(vh, ih + pad_h * 2)

        self._canvas.setMinimumSize(int(cw), int(ch))
        self._canvas.resize(int(cw), int(ch))
        # Ensure text items are positioned correctly on the new canvas size
        text_tool = self._tools.get("text")
        if text_tool:
            text_tool._sync_widgets()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """Handle window resizing by updating canvas bounds."""
        super().resizeEvent(event)
        self._resize_canvas()

    def _center_image_on_canvas(self) -> None:
        """Scroll the viewport to center the canvas content."""
        h_bar = self._scroll_area.horizontalScrollBar()
        v_bar = self._scroll_area.verticalScrollBar()
        h_bar.setValue((h_bar.minimum() + h_bar.maximum()) // 2)
        v_bar.setValue((v_bar.minimum() + v_bar.maximum()) // 2)

    def _rebuild_display(self) -> None:
        """Recreate display pixmap from current PIL image.
        Annotation/overlay layers are only reset when the image size changes
        (e.g. after rotate/crop), preserving them across undo/redo.
        """
        self._display_pixmap = _pil_to_qpixmap(self._pil_image)
        new_size = QtCore.QSize(*self._pil_image.size)
        # Only recreate annotation/overlay pixmaps if the image dimensions changed
        if self._annotations_pixmap is None or self._annotations_pixmap.size() != new_size:
            self._annotations_pixmap = QtGui.QPixmap(new_size)
            self._annotations_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        if self._overlay_pixmap is None or self._overlay_pixmap.size() != new_size:
            self._overlay_pixmap = QtGui.QPixmap(new_size)
            self._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._update_status()
        self._canvas.update()

    def _clear_annotations(self) -> None:
        """Clear annotations and text items."""
        if self._annotations_pixmap:
            self._annotations_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        if self._overlay_pixmap:
            self._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._text_items.clear()

    # ── Undo / Redo ───────────────────────────────────────────────────────

    def _save_undo(
        self,
        change_type: UndoChangeType = UndoChangeType.FULL,
        region_bounds: Optional[QtCore.QRect] = None,
        region_pixels: Optional[bytes] = None,
    ) -> None:
        """Push current state to undo stack before a destructive operation.

        Args:
            change_type: What kind of change is about to happen.
            region_bounds: Required when change_type is REGION.
            region_pixels: Required when change_type is REGION.
        """
        if change_type == UndoChangeType.REGION:
            assert region_bounds is not None and region_pixels is not None
            entry = _UndoEntry(
                change_type, region_bounds=region_bounds, region_pixels=region_pixels,
            )
        elif change_type == UndoChangeType.TEXT:
            entry = _UndoEntry(change_type, text_items=self._text_items)
        elif change_type == UndoChangeType.ANNOTATIONS:
            annot_copy = (
                self._annotations_pixmap.copy() if self._annotations_pixmap else None
            )
            entry = _UndoEntry(change_type, annot_pxm=annot_copy, text_items=self._text_items)
        else:
            # FULL — full capture (preserves existing behavior)
            annot_copy = (
                self._annotations_pixmap.copy() if self._annotations_pixmap else None
            )
            entry = _UndoEntry(change_type, pil_img=self._pil_image,
                               annot_pxm=annot_copy, text_items=self._text_items)

        self._undo_stack.append(entry)
        if len(self._undo_stack) > self.MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_buttons()

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        entry = self._undo_stack.pop()
        self._capture_current_for_redo(entry)
        self._apply_undo_entry(entry)
        self._update_undo_buttons()

    def _redo(self) -> None:
        if not self._redo_stack:
            return
        entry = self._redo_stack.pop()
        self._capture_current_for_undo(entry)
        self._apply_undo_entry(entry)
        self._update_undo_buttons()

    def _capture_current_for_redo(self, undone_entry: _UndoEntry) -> None:
        """Capture current state into a redo entry matching the type being undone."""
        ct = undone_entry.change_type
        if ct == UndoChangeType.FULL:
            annot_copy = self._annotations_pixmap.copy() if self._annotations_pixmap else None
            self._redo_stack.append(_UndoEntry(
                UndoChangeType.FULL, pil_img=self._pil_image,
                annot_pxm=annot_copy, text_items=self._text_items,
            ))
        elif ct == UndoChangeType.ANNOTATIONS:
            annot_copy = self._annotations_pixmap.copy() if self._annotations_pixmap else None
            self._redo_stack.append(_UndoEntry(
                UndoChangeType.ANNOTATIONS, annot_pxm=annot_copy, text_items=self._text_items,
            ))
        elif ct == UndoChangeType.TEXT:
            self._redo_stack.append(_UndoEntry(
                UndoChangeType.TEXT, text_items=self._text_items,
            ))
        elif ct == UndoChangeType.REGION:
            b = undone_entry.region_bounds
            current = self._pil_image.crop(
                (b.x(), b.y(), b.x() + b.width(), b.y() + b.height())
            )
            self._redo_stack.append(_UndoEntry(
                UndoChangeType.REGION,
                region_bounds=QtCore.QRect(b),
                region_pixels=current.tobytes(),
            ))

    def _capture_current_for_undo(self, redone_entry: _UndoEntry) -> None:
        """Capture current state into an undo entry matching the type being redone."""
        ct = redone_entry.change_type
        if ct == UndoChangeType.FULL:
            annot_copy = self._annotations_pixmap.copy() if self._annotations_pixmap else None
            self._undo_stack.append(_UndoEntry(
                UndoChangeType.FULL, pil_img=self._pil_image,
                annot_pxm=annot_copy, text_items=self._text_items,
            ))
        elif ct == UndoChangeType.ANNOTATIONS:
            annot_copy = self._annotations_pixmap.copy() if self._annotations_pixmap else None
            self._undo_stack.append(_UndoEntry(
                UndoChangeType.ANNOTATIONS, annot_pxm=annot_copy, text_items=self._text_items,
            ))
        elif ct == UndoChangeType.TEXT:
            self._undo_stack.append(_UndoEntry(
                UndoChangeType.TEXT, text_items=self._text_items,
            ))
        elif ct == UndoChangeType.REGION:
            b = redone_entry.region_bounds
            current = self._pil_image.crop(
                (b.x(), b.y(), b.x() + b.width(), b.y() + b.height())
            )
            self._undo_stack.append(_UndoEntry(
                UndoChangeType.REGION,
                region_bounds=QtCore.QRect(b),
                region_pixels=current.tobytes(),
            ))

    def _apply_undo_entry(self, entry: _UndoEntry) -> None:
        """Restore editor state from an undo/redo entry."""
        if entry.change_type == UndoChangeType.FULL:
            if entry.pil_image is not None:
                self._pil_image = entry.pil_image
            if entry.annotations_pixmap is not None:
                self._annotations_pixmap = entry.annotations_pixmap
            self._text_items = entry.text_items[:] if entry.text_items is not None else []
            self._rebuild_display()

        elif entry.change_type == UndoChangeType.ANNOTATIONS:
            if entry.annotations_pixmap is not None:
                self._annotations_pixmap = entry.annotations_pixmap
            self._text_items = entry.text_items[:] if entry.text_items is not None else []
            self._canvas.update()

        elif entry.change_type == UndoChangeType.TEXT:
            self._text_items = entry.text_items[:] if entry.text_items is not None else []
            self._canvas.update()

        elif entry.change_type == UndoChangeType.REGION:
            b = entry.region_bounds
            if b is not None and entry.region_pixels is not None:
                restored = Image.frombytes(
                    self._pil_image.mode,
                    (b.width(), b.height()),
                    entry.region_pixels,
                )
                self._pil_image.paste(restored, (b.x(), b.y()))
                self._rebuild_display()

        self._modified = True

    def _update_undo_buttons(self) -> None:
        self._undo_btn.setEnabled(len(self._undo_stack) > 0)
        self._redo_btn.setEnabled(len(self._redo_stack) > 0)

    # ── Transform operations ──────────────────────────────────────────────

    # ── Copy to clipboard ─────────────────────────────────────────────────

    def _get_composite_pixmap(self) -> QtGui.QPixmap:
        """Build a QPixmap of the image with all annotations baked in."""
        save_img = self._pil_image.copy().convert("RGBA")
        # Composite annotations
        if self._annotations_pixmap and not self._annotations_pixmap.isNull():
            annot_pil = _qpixmap_to_pil(self._annotations_pixmap)
            save_img = Image.alpha_composite(save_img, annot_pil.convert("RGBA"))
        # Bake text items
        if self._text_items:
            draw = ImageDraw.Draw(save_img)
            for item in self._text_items:
                if not item.text.strip():
                    continue
                font_path = TextTool._resolve_font_path(item.font_family)
                try:
                    pil_font = ImageFont.truetype(font_path, item.font_size) if font_path else ImageFont.load_default()
                except Exception:
                    pil_font = ImageFont.load_default()
                fill = (item.color.red(), item.color.green(), item.color.blue(), item.color.alpha())
                draw.text((int(item.img_pos.x()), int(item.img_pos.y())), item.text, fill=fill, font=pil_font)
        return _pil_to_qpixmap(save_img)

    def _copy_to_clipboard(self) -> None:
        """Copy the composite image to the system clipboard."""
        try:
            pixmap = self._get_composite_pixmap()
            QtWidgets.QApplication.clipboard().setPixmap(pixmap)
            from .toast import show_toast
            show_toast(self._tr("editor_copied"))
        except Exception:
            logger.exception("Copy to clipboard failed")

    # ── Status ────────────────────────────────────────────────────────────

    def _update_status(self) -> None:
        if self._display_pixmap:
            w, h = self._display_pixmap.width(), self._display_pixmap.height()
            self._status_label.setText(f"{w} × {h}")
        self._update_zoom_label()

    def _update_zoom_label(self) -> None:
        pct = round(self._scale * 100)
        self._zoom_label.setText(self._tr("editor_zoom_label", zoom=pct))

    # ── Save / Close ──────────────────────────────────────────────────────

    def _save_as(self) -> None:
        import time
        from ..config import get_last_save_directory, update_last_save_directory

        default_dir = get_last_save_directory(get_config_path())
        default_name = f"HushSnap_{time.strftime('%Y%m%d_%H%M%S')}.png"
        file_path_str, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self._tr("editor_save_as"),
            str(Path(default_dir) / default_name),
            "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)",
        )
        if not file_path_str:
            return
        try:
            file_path = Path(file_path_str)
            update_last_save_directory(file_path.parent, get_config_path())

            # Build composite pixmap, convert to PIL for save
            composite = self._get_composite_pixmap()
            save_img = _qpixmap_to_pil(composite)
            if file_path_str.lower().endswith((".jpg", ".jpeg")):
                save_img = save_img.convert("RGB")
            save_img.save(file_path_str)
            self._modified = False

            try:
                from .toast import show_toast
                show_toast(self._tr("editor_saved"))
            except Exception:
                pass
        except Exception:
            logger.exception("Failed to save image")

    def _title_bar_mouse_press(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _title_bar_mouse_move(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_pos is not None and (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        event.accept()
        super().closeEvent(event)


# ── Public entry point ───────────────────────────────────────────────────────

def show_image_editor(
    pil_image: Image.Image,
    translate_fn: Callable[[str], str],
) -> ImageEditorWindow:
    """Create and show the image editor window for the given PIL image.

    Args:
        pil_image: The PIL Image to edit.
        translate_fn: Translation function (key -> localized string).

    Returns:
        The editor window instance.
    """
    win = ImageEditorWindow(pil_image, translate_fn)
    win.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    win.show()
    return win
