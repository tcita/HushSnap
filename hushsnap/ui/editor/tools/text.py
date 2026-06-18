import os
from typing import Optional
from PyQt6 import QtCore, QtGui
from .base import BaseTool
from ..models import UndoChangeType, TextItem
from ..widgets.inline_editor import _InlineTextEditor

class TextTool(BaseTool):
    """Text annotation tool."""

    _FONT_DIR = None
    _FONT_CACHE: dict[str, str] = {}

    def __init__(self, editor):
        super().__init__(editor)
        self.font_family = QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.SystemFont.GeneralFont
        ).family()
        self.font_size = 24
        self.color = QtGui.QColor("#FFFFFF")
        
        self._dragging_item: Optional[TextItem] = None
        self._drag_offset = QtCore.QPointF()
        self._drag_undo_saved = False
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
        
        canvas.setFocus()

        if self._editing_widget:
            self._editing_widget.commit_edit()

        pos = event.position()
        hit_idx = self._hit_test(canvas, pos)
        
        if hit_idx != -1:
            self._dragging_item = self._editor._text_items[hit_idx]
            self._drag_undo_saved = False
            img_pos = self._to_image_coords(canvas, pos.toPoint())
            self._drag_offset = QtCore.QPointF(img_pos[0], img_pos[1]) - self._dragging_item.img_pos
            canvas.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
            return True
        
        img_pt = self._to_image_coords(canvas, pos.toPoint())
        new_item = TextItem("", QtCore.QPointF(img_pt[0], img_pt[1]),
                            QtGui.QColor(self.color), self.font_family, self.font_size)
        self._editor._save_undo(UndoChangeType.TEXT)
        self._editor._text_items.append(new_item)
        self._spawn_editor(canvas, new_item)
        return True

    def on_mouse_move(self, canvas, event) -> bool:
        if self._dragging_item and (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            if not self._drag_undo_saved:
                self._editor._save_undo(UndoChangeType.TEXT)
                self._drag_undo_saved = True
            img_pt = self._to_image_coords(canvas, event.position().toPoint())
            self._dragging_item.img_pos = QtCore.QPointF(img_pt[0], img_pt[1]) - self._drag_offset
            self._editor._modified = True
            canvas.update()
            return True
        return False

    def on_mouse_release(self, canvas, event) -> bool:
        if self._dragging_item:
            self._dragging_item = None
            self._drag_undo_saved = False
            canvas.setCursor(QtCore.Qt.CursorShape.IBeamCursor)
            return True
        return True

    def on_mouse_double_click(self, canvas, event) -> bool:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return False

        hit_idx = self._hit_test(canvas, event.position())
        if hit_idx != -1:
            item = self._editor._text_items[hit_idx]
            self._dragging_item = None
            self._drag_undo_saved = False
            canvas.setCursor(QtCore.Qt.CursorShape.IBeamCursor)
            self._editor._save_undo(UndoChangeType.TEXT)
            self._spawn_editor(canvas, item)
            return True
        return False

    def _hit_test(self, canvas, screen_pos) -> int:
        """Return index of item at screen_pos, or -1."""
        scale = self._editor._effective_scale()
        offset = canvas._image_offset()
        
        for i in range(len(self._editor._text_items)-1, -1, -1):
            item = self._editor._text_items[i]
            if not item.text:
                continue
            
            fs = max(1, int(item.font_size * scale))
            font = QtGui.QFont(item.font_family)
            font.setPixelSize(fs)
            metrics = QtGui.QFontMetrics(font)
            
            tw = metrics.horizontalAdvance(item.text)
            th = metrics.height()
            
            screen_x = item.img_pos.x() * scale + offset.x()
            screen_y = item.img_pos.y() * scale + offset.y()
            
            hit_rect = QtCore.QRect(int(screen_x), int(screen_y), int(tw), int(th))
            hit_rect.adjust(-5, -5, 5, 5)
            
            if hit_rect.contains(screen_pos.toPoint()):
                return i
        return -1

    def _spawn_editor(self, canvas, item: TextItem) -> None:
        """Pop up the temporary QLineEdit for editing."""
        if self._editing_widget:
            self._editing_widget.commit_edit()

        item.color = QtGui.QColor(self.color)
        item.font_family = self.font_family
        item.font_size = self.font_size

        self._editing_widget = _InlineTextEditor(canvas, self, item)
        self._editing_widget.show()
        QtCore.QTimer.singleShot(0, self._editing_widget.setFocus)
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
