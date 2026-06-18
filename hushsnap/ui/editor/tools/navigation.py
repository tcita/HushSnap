from typing import Optional
from PyQt6 import QtCore, QtGui
from .base import BaseTool

class PanTool(BaseTool):
    """Hand/grab tool: drag to pan the canvas (Photoshop-style)."""

    def __init__(self, editor):
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
