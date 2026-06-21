import logging

from typing import TYPE_CHECKING
from PyQt6 import QtCore, QtGui, QtWidgets
from ..constants import BRAND_GREEN, TEXT_OUTLINE_WIDTH
from ..utils import _draw_outlined_text

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..tools.text import TextTool
    from ..models import TextItem

class _HiddenLineEdit(QtWidgets.QLineEdit):
    """A QLineEdit that paints nothing.

    Acts as the inline editor's input engine: it owns the real keyboard focus
    (so IME, clipboard, cursor movement all work for free) while the parent
    _InlineTextEditor draws the stroked text + caret itself. It is kept
    invisible (paintEvent is a no-op) and parked at the parent's top-left
    under the parent's own painting — it never participates in hit-testing or
    visual layout.
    """
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        return


class _InlineTextEditor(QtWidgets.QWidget):
    """Temporary stroked-text editor that appears during text entry.

    Two-layer design:
      - This widget (outer) self-paints the green box, stroked text, and the
        blinking caret. It is what the user sees and clicks.
      - A hidden _HiddenLineEdit (inner) holds the actual text and keyboard
        focus. The outer widget sets it as its focus proxy, so any setFocus()
        on the outer is forwarded to the inner — the outer never itself
        becomes the focus widget in steady state.

    Commit policy (explicit only — focus loss NEVER auto-commits):
      - Switching tools (TextTool.on_deactivate)
      - Clicking the canvas (TextTool.on_mouse_press on empty space)
      - Double-clicking elsewhere (TextTool.on_mouse_double_click)
      - Enter / Return (returnPressed)
      - Escape (reverts to the pre-edit text)
      - Save / copy / window close (ImageEditorWindow._commit_active_text_edit)
    This keeps the editor alive while the user adjusts font / size in the
    toolbar — those controls take focus from the inner line edit, and because
    focus loss is not a commit trigger, editing continues uninterrupted.
    """

    def __init__(self, parent: QtWidgets.QWidget, tool: "TextTool", item: "TextItem"):
        super().__init__(parent)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        self._tool = tool
        self._item = item
        self._before_edit_text = item.text

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
        # The outer widget delegates focus to the inner line edit. This means
        # setFocus() on the outer lands on the inner, and the outer never
        # becomes the QApplication focus widget in steady state — which is why
        # focus loss does not (and must not) drive commit logic here.
        self.setFocusProxy(self._input)

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

        # Use actual rendered pixel size so geometry stays consistent
        # with _draw_outlined_text — fonts like "Roman" have a fixed
        # QFontInfo pixel size that differs from the requested fs.
        actual = QtGui.QFontInfo(mfont).pixelSize()
        if actual <= 0:
            actual = fs
        if actual != fs:
            logger.debug(
                "Non-scalable font %r in _update_geometry: "
                "requested=%dpx  actual=%dpx  fm.height=%d",
                self._item.font_family, fs, actual, fm.height(),
            )
        outline_w = max(1.0, actual * TEXT_OUTLINE_WIDTH)

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

        actual = QtGui.QFontInfo(font).pixelSize()
        if actual <= 0:
            actual = fs
        if actual != fs:
            logger.debug(
                "Non-scalable font %r in paintEvent: "
                "requested=%dpx  actual=%dpx",
                self._item.font_family, fs, actual,
            )
        outline_w = max(1.0, actual * TEXT_OUTLINE_WIDTH)
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
        # setFocusProxy routes focus to the inner line edit automatically.
        # Start the caret blinking fresh so the caret is visible on entry.
        self._cursor_visible = True
        self._cursor_timer.start()
        self.update()
        super().focusInEvent(e)

    def _char_index_at(self, x: float) -> int:
        """Map a widget-local x to the nearest character index in the text.

        The text is drawn starting at x=0 (see paintEvent), so we walk the
        string and find the split point closest to the click. Clicking past
        the last glyph places the caret at the end.
        """
        text = self._input.text()
        if not text:
            return 0
        scale = self._tool._editor._effective_scale()
        font = QtGui.QFont(self._item.font_family)
        font.setPixelSize(max(1, int(self._item.font_size * scale)))
        fm = QtGui.QFontMetrics(font)
        best = 0
        best_dist = abs(x)
        for i in range(1, len(text) + 1):
            cx = fm.horizontalAdvance(text[:i])
            d = abs(x - cx)
            if d <= best_dist:
                best_dist = d
                best = i
            else:
                break
        return best

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        """Click inside the box moves the caret to the click — no commit.

        Standard text-control feel: clicking in the editor repositions the
        caret (and refocuses) rather than committing, so the user can adjust
        mid-edit. Only clicks OUTSIDE this widget reach the canvas and trigger
        the click-empty-to-commit path in TextTool.
        """
        if e.button() != QtCore.Qt.MouseButton.LeftButton:
            super().mousePressEvent(e)
            return
        self.setFocus()
        idx = self._char_index_at(e.position().x())
        self._input.setCursorPosition(idx)
        self._cursor_visible = True
        self._cursor_timer.start()
        self.update()
        e.accept()

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if self._committed:
            return
        if e.key() == QtCore.Qt.Key.Key_Escape:
            self._input.setText(self._before_edit_text)
            self.commit_edit()
            e.accept()
            return
        # The inner line edit is the focus widget and receives key events
        # directly; this branch only runs when the outer is reached instead
        # (e.g. during the brief focus handoff). Forward so input still works.
        self._input.keyPressEvent(e)
        if self._committed:
            return
        self._cursor_visible = True
        self._cursor_timer.start()
        self.update()

    def inputMethodEvent(self, e: QtGui.QInputMethodEvent) -> None:
        # Focus normally sits on the inner line edit (the focus proxy), so IME
        # events are delivered there directly. This override only runs in edge
        # cases where the outer widget itself is the focus target — forward to
        # the line edit so composition still works. We do NOT render the
        # preedit here: the OS input-method candidate window already shows the
        # in-progress composition, and drawing it in this widget would require
        # the preedit to be piped back from the line edit (which does not
        # expose it), so it would never update. Rely on the IME's own UI.
        self._input.inputMethodEvent(e)

    def inputMethodQuery(self, query: QtCore.Qt.InputMethodQuery) -> object:
        return self._input.inputMethodQuery(query)

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
