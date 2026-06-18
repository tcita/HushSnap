import math
from typing import Optional
from PyQt6 import QtCore, QtGui
from .base import BaseTool
from ..models import UndoChangeType

class ShapeTool(BaseTool):
    """Draw rectangle or ellipse outlines on the annotations layer."""

    def __init__(self, editor, shape: str = "rectangle"):
        super().__init__(editor)
        self._shape = shape  # "rectangle", "ellipse", or "line"
        self._color = QtGui.QColor("#4488FF")
        self._size = 3
        self._fill = False
        # Line-only: optional arrowhead(s) at the end(s)
        self._arrow_end = False
        self._double_arrow = False
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
    @property
    def fill(self) -> bool: return self._fill
    @fill.setter
    def fill(self, v: bool): self._fill = v
    @property
    def arrow_end(self) -> bool: return self._arrow_end
    @arrow_end.setter
    def arrow_end(self, v: bool): self._arrow_end = v
    @property
    def double_arrow(self) -> bool: return self._double_arrow
    @double_arrow.setter
    def double_arrow(self, v: bool): self._double_arrow = v

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
        
        if self._fill:
            painter.setBrush(QtGui.QBrush(self._color))
        else:
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            
        if self._shape == "rectangle":
            painter.drawRect(rect)
        elif self._shape == "ellipse":
            painter.drawEllipse(rect)
        elif self._shape == "line":
            # Lines look better with round caps; arrows are optional
            line_pen = QtGui.QPen(self._color, self._size,
                                  QtCore.Qt.PenStyle.SolidLine,
                                  QtCore.Qt.PenCapStyle.RoundCap,
                                  QtCore.Qt.PenJoinStyle.RoundJoin)
            painter.setPen(line_pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))
            if self._arrow_end or self._double_arrow:
                self._draw_arrowheads(painter, x1, y1, x2, y2)
        painter.end()

    def _draw_arrowheads(
        self, painter: QtGui.QPainter,
        x1: float, y1: float, x2: float, y2: float,
    ) -> None:
        """Draw arrowhead triangle(s) at the line end(s)."""
        dx, dy = x2 - x1, y2 - y1
        line_len = math.hypot(dx, dy)
        if line_len < 1:
            return
        angle = math.atan2(dy, dx)
        arrow_len = max(10.0, self._size * 4.0)
        spread = 0.45  # radians (~26°)

        def _head(px2: float, py2: float, angle_dir: float) -> None:
            px = px2 - arrow_len * math.cos(angle_dir - spread)
            py = py2 - arrow_len * math.sin(angle_dir - spread)
            qx = px2 - arrow_len * math.cos(angle_dir + spread)
            qy = py2 - arrow_len * math.sin(angle_dir + spread)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QBrush(self._color))
            painter.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(px2, py2),
                QtCore.QPointF(px, py),
                QtCore.QPointF(qx, qy),
            ]))

        _head(x2, y2, angle)
        if self._double_arrow:
            _head(x1, y1, angle + math.pi)
