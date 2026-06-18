from typing import TYPE_CHECKING
from PyQt6 import QtCore, QtGui, QtWidgets
from ..constants import BRAND_GREEN, TEXT_OUTLINE_WIDTH
from ..utils import _draw_outlined_text

if TYPE_CHECKING:
    from ..tools.text import TextTool
    from ..models import TextItem

class _HiddenLineEdit(QtWidgets.QLineEdit):
    """A QLineEdit that paints nothing."""
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        return

class _InlineTextEditor(QtWidgets.QWidget):
    """Temporary stroked-text editor that appears during text entry."""

    def __init__(self, parent: QtWidgets.QWidget, tool: "TextTool", item: "TextItem"):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        
        self._tool = tool
        self._item = item
        self._before_edit_text = item.text
        self._birth_time = QtCore.QElapsedTimer()
        self._birth_time.start()

        self._input = _HiddenLineEdit(self)
        self._input.setText(item.text)
        self._input.setFrame(False)
        self._input.setStyleSheet(
            "QLineEdit { background: transparent; border: none; padding: 0; }"
        )
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self.commit_edit)
        self._input.resize(20, 20)
        self._input.lower()

        self._cursor_visible = True
        self._cursor_timer = QtCore.QTimer(self)
        self._cursor_timer.setInterval(530)
        self._cursor_timer.timeout.connect(self._blink_cursor)
        self._cursor_timer.start()

        self._committed = False
        self._apply_style()
        self._update_geometry()

    @property
    def _text(self) -> str:
        return self._input.text()

    def text(self) -> str:
        return self._input.text()

    def setText(self, t: str) -> None:
        self._input.setText(t)

    def _apply_style(self) -> None:
        self.update()

    def _update_geometry(self) -> None:
        canvas = self.parentWidget()
        scale = self._tool._editor._effective_scale()
        offset = canvas._image_offset()

        screen_x = self._item.img_pos.x() * scale + offset.x()
        screen_y = self._item.img_pos.y() * scale + offset.y()

        fs = max(1, int(self._item.font_size * scale))
        mfont = QtGui.QFont(self._item.font_family)
        mfont.setPixelSize(fs)
        fm = QtGui.QFontMetrics(mfont)

        outline_w = max(1.0, fs * TEXT_OUTLINE_WIDTH)
        
        min_w = int(100 * scale)
        pad_w = max(8, int(20 * scale))
        pad_top = int(outline_w / 2) + 2
        pad_h = fm.height() + int(outline_w) + 4
        
        w = max(min_w, fm.horizontalAdvance(self._input.text()) + pad_w)
        h = pad_h
        
        self.setGeometry(int(screen_x), int(screen_y - pad_top), int(w), int(h))

    def _on_text_changed(self) -> None:
        self._update_geometry()
        self.update()

    def _blink_cursor(self) -> None:
        self._cursor_visible = not self._cursor_visible
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)

        painter.setPen(QtGui.QPen(QtGui.QColor(BRAND_GREEN), 1.0))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 30)))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 3, 3)

        scale = self._tool._editor._effective_scale()
        fs = max(1, int(self._item.font_size * scale))
        font = QtGui.QFont(self._item.font_family)
        font.setPixelSize(fs)
        fm = QtGui.QFontMetrics(font)

        outline_w = max(1.0, fs * TEXT_OUTLINE_WIDTH)
        pad_top = int(outline_w / 2) + 2
        
        text = self._input.text()
        baseline = QtCore.QPointF(0, fm.ascent() + pad_top)
        if text:
            _draw_outlined_text(painter, baseline, text, font)

        if self._cursor_visible:
            pos = self._input.cursorPosition()
            prefix = text[:pos]
            cx = fm.horizontalAdvance(prefix)
            caret_h = fm.ascent() + fm.descent()
            caret_w = max(2.0, fs * 0.05)
            
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 150), 0.5))
            painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255)))
            painter.drawRect(QtCore.QRectF(cx, pad_top, caret_w, caret_h))

    def focusInEvent(self, e: QtGui.QFocusEvent) -> None:
        self._input.setFocus()
        super().focusInEvent(e)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if self._committed:
            return
        if e.key() == QtCore.Qt.Key.Key_Escape:
            self._input.setText(self._before_edit_text)
            self.commit_edit()
            e.accept()
            return
        self._input.keyPressEvent(e)
        if self._committed:
            return
        self._cursor_visible = True
        self._cursor_timer.start()
        self.update()

    def inputMethodEvent(self, e: QtGui.QInputMethodEvent) -> None:
        self._input.inputMethodEvent(e)

    def inputMethodQuery(self, query: QtCore.Qt.InputMethodQuery) -> object:
        return self._input.inputMethodQuery(query)

    def focusOutEvent(self, e: QtGui.QFocusEvent) -> None:
        if self._birth_time.elapsed() < 300:
            QtCore.QTimer.singleShot(10, self._input.setFocus)
            return

        self.commit_edit()
        super().focusOutEvent(e)

    def commit_edit(self) -> None:
        if self._committed:
            return
        self._committed = True
        if self._cursor_timer.isActive():
            self._cursor_timer.stop()
        txt = self._input.text().strip()
        if txt:
            self._item.text = txt
            self._tool._mark_modified()
        elif not self._before_edit_text:
            if self._item in self._tool._editor._text_items:
                self._tool._editor._text_items.remove(self._item)
        else:
            self._item.text = self._before_edit_text
        self._tool._editing_widget = None
        self.parentWidget().update()
        self.deleteLater()
