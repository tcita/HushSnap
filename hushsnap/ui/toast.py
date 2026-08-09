import logging
from PyQt6 import QtCore, QtGui, QtWidgets

from .styles import BRAND_GREEN

logger = logging.getLogger(__name__)

# Global list to keep Toast instances alive (prevent GC) until they close themselves
_active_toasts = []

class Toast(QtWidgets.QFrame):
    """
    A sleek, non-intrusive floating notification (Toast).
    Fades and slides out automatically.
    """
    def __init__(self, text, parent=None, duration_ms=2000, is_error=False, position=None):
        super().__init__(parent)
        self.duration_ms = duration_ms
        self.is_error = is_error
        _active_toasts.append(self)
        
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)

        # Main layout for the top-level Toast widget (transparent wrapper)
        outer_layout = QtWidgets.QVBoxLayout(self)
        outer_layout.setContentsMargins(28, 28, 28, 34)
        outer_layout.setSpacing(0)

        # Container widget for actual content
        self.container = QtWidgets.QFrame()
        self.container.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        outer_layout.addWidget(self.container)

        # Main container layout inside container
        layout = QtWidgets.QHBoxLayout(self.container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Left accent bar
        accent_bar = QtWidgets.QFrame()
        accent_color = "#FF5252" if is_error else BRAND_GREEN
        accent_bar.setStyleSheet(
            f"background-color: {accent_color}; border-top-left-radius: 8px; border-bottom-left-radius: 8px;"
        )
        accent_bar.setFixedWidth(4)
        layout.addWidget(accent_bar)

        # Content area
        self.label = QtWidgets.QLabel(text)
        self.label.setStyleSheet(
            "QLabel {"
            " background-color: rgba(28, 28, 28, 0.96);"
            " color: #FFFFFF;"
            " border-top-right-radius: 8px;"
            " border-bottom-right-radius: 8px;"
            " padding: 12px 20px;"
            " font-size: 14px;"
            " font-weight: 500;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", \"Segoe UI\", sans-serif;"
            "}"
        )
        layout.addWidget(self.label)
        
        self.adjustSize()

        # Position: follow the cursor's screen, not the primary screen.
        from ..dpi import cursor_screen
        active = cursor_screen() or QtWidgets.QApplication.primaryScreen()
        screen = active.availableGeometry() if active else QtWidgets.QApplication.primaryScreen().availableGeometry()
        if position:
            target_x = position.x() - self.width() // 2
            target_y = position.y() - self.height() // 2
        else:
            target_x = screen.center().x() - self.width() // 2
            target_y = screen.y() + screen.height() - self.height() - 20

        # Slide-up animation start position
        self.move(target_x, target_y + 20)
        self.setWindowOpacity(0.0)

        # Animations
        self.group = QtCore.QParallelAnimationGroup(self)
        
        # Fade and Slide in
        fade_in = QtCore.QPropertyAnimation(self, b"windowOpacity")
        fade_in.setDuration(300)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        slide_in = QtCore.QPropertyAnimation(self, b"pos")
        slide_in.setDuration(400)
        slide_in.setStartValue(QtCore.QPoint(target_x, target_y + 20))
        slide_in.setEndValue(QtCore.QPoint(target_x, target_y))
        slide_in.setEasingCurve(QtCore.QEasingCurve.Type.OutBack)

        self.group.addAnimation(fade_in)
        self.group.addAnimation(slide_in)

        # Drop shadow on the container widget to render inside the top-level window
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.container)
        shadow.setBlurRadius(25)
        shadow.setColor(QtGui.QColor(0, 0, 0, 120))
        shadow.setOffset(0, 6)
        self.container.setGraphicsEffect(shadow)

        self.show()
        self.group.start()

        # Fade out timer
        self.fade_timer = QtCore.QTimer(self)
        self.fade_step_ms = 30
        self.fade_total_ms = 400
        self.fade_steps = self.fade_total_ms // self.fade_step_ms
        self._current_fade_step = 0

        self.fade_timer.timeout.connect(self._perform_fade)
        QtCore.QTimer.singleShot(duration_ms, self.fade_timer.start)
        self.fade_timer.setInterval(self.fade_step_ms)

    def _perform_fade(self):
        self._current_fade_step += 1
        if self._current_fade_step > self.fade_steps:
            self.fade_timer.stop()
            if self in _active_toasts:
                _active_toasts.remove(self)
            self.deleteLater()
            return
        
        t = self._current_fade_step / self.fade_steps
        opacity = 1.0 - (t * t)
        self.setWindowOpacity(opacity)

def show_toast(text, duration_ms=2000, is_error=False, position=None):
    """Helper function to show a toast globally."""
    return Toast(text, duration_ms=duration_ms, is_error=is_error, position=position)


class OcrCopyToast(QtWidgets.QFrame):
    """Clickable toast that copies OCR text to clipboard on click.

    Appears at the bottom-right of the current cursor position so the user
    doesn't need to move the mouse far.  Clicking anywhere on the toast copies
    the full recognized text; the screenshot on the clipboard is left untouched.
    """

    _active: "OcrCopyToast | None" = None  # one-at-a-time — later replaces earlier

    def __init__(self, full_text: str, duration_ms: int = 2000):
        super().__init__(None)
        self._full_text = full_text
        self._duration_ms = duration_ms

        # Replace any still-visible previous toast
        prev = OcrCopyToast._active
        if prev is not None:
            try:
                prev.close()
            except Exception:
                pass
        OcrCopyToast._active = self

        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        # ── Build layout ──────────────────────────────────────────────
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 34)
        outer.setSpacing(0)

        self._container = QtWidgets.QFrame()
        self._container.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        outer.addWidget(self._container)

        hbox = QtWidgets.QHBoxLayout(self._container)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)

        # Accent bar — cyan-green to distinguish from the regular green toast
        accent = QtWidgets.QFrame()
        accent.setStyleSheet(
            "background-color: #5fc98a;"
            "border-top-left-radius: 8px;"
            "border-bottom-left-radius: 8px;"
        )
        accent.setFixedWidth(4)
        hbox.addWidget(accent)

        display = "点击复制识别文字"

        self._label = QtWidgets.QLabel(display)
        self._label.setStyleSheet(
            "QLabel {"
            "  background-color: rgba(28, 28, 28, 0.96);"
            "  color: #FFFFFF;"
            "  border-top-right-radius: 8px;"
            "  border-bottom-right-radius: 8px;"
            "  padding: 12px 20px;"
            "  font-size: 13px;"
            "  font-weight: 500;"
            "  font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", \"Segoe UI\", sans-serif;"
            "}"
        )
        hbox.addWidget(self._label)
        self.adjustSize()

        # ── Position: bottom-right of cursor, clamped to screen ──────
        cursor = QtGui.QCursor.pos()
        from ..dpi import cursor_screen
        active_screen = cursor_screen() or QtWidgets.QApplication.primaryScreen()
        screen = active_screen.availableGeometry() if active_screen else QtWidgets.QApplication.primaryScreen().availableGeometry()

        offset = 8
        x = cursor.x() + offset
        y = cursor.y() + offset
        # Flip if it would overflow the right / bottom edge
        if x + self.width() > screen.right():
            x = cursor.x() - self.width() - offset
        if y + self.height() > screen.bottom():
            y = cursor.y() - self.height() - offset
        # Clamp to screen bounds
        x = max(screen.left(), min(x, screen.right() - self.width()))
        y = max(screen.top(), min(y, screen.bottom() - self.height()))

        self.move(x, y + 12)
        self.setWindowOpacity(0.0)

        # ── Animations ────────────────────────────────────────────────
        self._anim_group = QtCore.QParallelAnimationGroup(self)

        fade_in = QtCore.QPropertyAnimation(self, b"windowOpacity")
        fade_in.setDuration(250)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        slide_in = QtCore.QPropertyAnimation(self, b"pos")
        slide_in.setDuration(350)
        slide_in.setStartValue(QtCore.QPoint(x, y + 12))
        slide_in.setEndValue(QtCore.QPoint(x, y))
        slide_in.setEasingCurve(QtCore.QEasingCurve.Type.OutBack)

        self._anim_group.addAnimation(fade_in)
        self._anim_group.addAnimation(slide_in)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self._container)
        shadow.setBlurRadius(25)
        shadow.setColor(QtGui.QColor(0, 0, 0, 120))
        shadow.setOffset(0, 6)
        self._container.setGraphicsEffect(shadow)

        self.show()
        self._anim_group.start()

        # ── Auto-dismiss timer ────────────────────────────────────────
        self._fade_timer = QtCore.QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(self._begin_fade_out)
        self._fade_timer.start(duration_ms)

        # Per-tick fade-out
        self._fade_step_timer = QtCore.QTimer(self)
        self._fade_step_timer.setInterval(30)
        self._fade_step_timer.timeout.connect(self._tick_fade_out)
        self._fade_step = 0
        self._fade_total_steps = 400 // 30

    # ── Click → copy + dismiss ────────────────────────────────────
    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard:
                clipboard.setText(self._full_text)
            # Brief "Copied" feedback before dismissing
            self._label.setText("✓ 已复制")
            self._fade_timer.stop()
            QtCore.QTimer.singleShot(600, self._begin_fade_out)
        return super().mouseReleaseEvent(event)

    # ── Fade out ─────────────────────────────────────────────────
    def _begin_fade_out(self):
        self._fade_timer.stop()
        self._fade_step_timer.start()

    def _tick_fade_out(self):
        self._fade_step += 1
        if self._fade_step > self._fade_total_steps:
            self._fade_step_timer.stop()
            if OcrCopyToast._active is self:
                OcrCopyToast._active = None
            self.deleteLater()
            return
        t = self._fade_step / self._fade_total_steps
        self.setWindowOpacity(1.0 - t * t)


def show_ocr_copy_toast(full_text: str):
    """Show a clickable OCR-copy toast near the cursor."""
    return OcrCopyToast(full_text)
