from typing import Optional
from PyQt6 import QtCore, QtGui
from .base import BaseTool
from ..models import UndoChangeType

class BrushTool(BaseTool):
    """Freehand brush: Pinta-style path-accumulated stroke on annotations layer."""

    def __init__(self, editor):
        super().__init__(editor)
        self._stroke_color = QtGui.QColor("#4488FF")
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

    def __init__(self, editor):
        super().__init__(editor)
        self._stroke_color = QtGui.QColor(255, 255, 0, 80)
        self._stroke_size = 24
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
    """Erase annotations directly on the annotations layer."""

    def __init__(self, editor):
        super().__init__(editor)
        self._stroke_size = 24
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
