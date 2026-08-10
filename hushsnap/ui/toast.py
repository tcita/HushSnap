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


class OcrCopyChip(QtWidgets.QFrame):
    """A floating action chip that rides beside the cursor.

    Rounded pill with icon + label, soft drop shadow, no accent bar.
    Click copies the full recognized text; the screenshot stays on the
    system clipboard untouched.
    """

    _active: "OcrCopyChip | None" = None

    # ── visual constants ────────────────────────────────────────────
    _BG = "rgba(38, 38, 42, 0.97)"       # near-opaque warm dark
    _BG_HOVER = "rgba(55, 55, 60, 0.98)"  # subtle lift on hover
    _FG = "#e8e8ec"
    _FONT = (
        "font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", "
        "\"Segoe UI\", \"Noto Sans SC\", sans-serif;"
    )
    _OFFSET_X = 12  # px right of cursor
    _FADE_IN_MS = 120
    _DURATION_MS = 3500
    _FADE_OUT_MS = 300

    # ── pill-stylesheet helper ─────────────────────────────────────
    def _pill_sheet(self, hover: bool = False) -> str:
        bg = self._BG_HOVER if hover else self._BG
        return (
            f"background-color: {bg};"
            "border-radius: 8px;"
        )

    # ── label-stylesheet helper ────────────────────────────────────
    def _label_sheet(self, hover: bool = False, size: int = 13) -> str:
        fg = "#ffffff" if hover else self._FG
        return (
            f"color: {fg};"
            f"font-size: {size}px;"
            "font-weight: 500;"
            "background: transparent;"
            f"{self._FONT}"
        )

    def __init__(self, full_text: str, *, label: str = "Copy text",
                 done_label: str = "Copied"):
        super().__init__(None)
        self._full_text = full_text
        self._done_label = done_label
        self._label: QtWidgets.QLabel | None = None

        prev = OcrCopyChip._active
        if prev is not None:
            try:
                prev.close()
            except Exception:
                pass
        OcrCopyChip._active = self

        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        # ── inner pill (carries background + shadow) ────────────────
        pill = QtWidgets.QWidget(self)
        pill.setStyleSheet(f"QWidget {{ {self._pill_sheet()} }}")

        shadow = QtWidgets.QGraphicsDropShadowEffect(pill)
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 2)
        shadow.setColor(QtGui.QColor(0, 0, 0, 50))
        pill.setGraphicsEffect(shadow)
        self._pill = pill

        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(pill)

        # ── label ────────────────────────────────────────────────
        hbox = QtWidgets.QHBoxLayout(pill)
        hbox.setContentsMargins(12, 6, 12, 6)
        hbox.setSpacing(0)

        self._label = QtWidgets.QLabel(label)
        self._label.setStyleSheet(self._label_sheet())
        hbox.addWidget(self._label)
        self.adjustSize()

        # ── position beside cursor ──────────────────────────────────
        cursor = QtGui.QCursor.pos()
        from ..dpi import cursor_screen
        active_screen = cursor_screen() or QtWidgets.QApplication.primaryScreen()
        screen = active_screen.availableGeometry() if active_screen else QtWidgets.QApplication.primaryScreen().availableGeometry()

        # Vertically centered on cursor hotspot (like a context-menu item)
        x = cursor.x() + self._OFFSET_X
        y = cursor.y() - self.height() // 2
        if x + self.width() > screen.right():
            x = cursor.x() - self.width() - self._OFFSET_X
        if y + self.height() > screen.bottom():
            y = screen.bottom() - self.height()
        if y < screen.top():
            y = screen.top()
        x = max(screen.left(), min(x, screen.right() - self.width()))
        y = max(screen.top(), min(y, screen.bottom() - self.height()))

        self.move(x, y)
        self.setWindowOpacity(0.0)
        self.show()

        # ── fade in (no slide — chip feels anchored to cursor) ─────
        self._fade_in = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self._fade_in.setDuration(self._FADE_IN_MS)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QtCore.QEasingCurve.Type.OutBack)
        self._fade_in.start()

        # ── auto-dismiss ────────────────────────────────────────────
        self._dismiss_timer = QtCore.QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._fade_out)
        self._dismiss_timer.start(self._DURATION_MS)

    # ── hover (pause / resume countdown) ──────────────────────────────
    def enterEvent(self, event):
        self._dismiss_timer.stop()
        self._pill.setStyleSheet(f"QWidget {{ {self._pill_sheet(hover=True)} }}")
        if self._label:
            self._label.setStyleSheet(self._label_sheet(hover=True))

    def leaveEvent(self, event):
        self._dismiss_timer.start(self._DURATION_MS)
        self._pill.setStyleSheet(f"QWidget {{ {self._pill_sheet()} }}")
        if self._label:
            self._label.setStyleSheet(self._label_sheet())

    # ── click → copy + flash + dismiss ─────────────────────────────
    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard:
                clipboard.setText(self._full_text)
            self._dismiss_timer.stop()
            if self._label:
                self._label.setText(self._done_label)
            QtCore.QTimer.singleShot(700, self._fade_out)
        return super().mouseReleaseEvent(event)

    # ── fade-out ───────────────────────────────────────────────────
    def _fade_out(self):
        self._fade_out_anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self._fade_out_anim.setDuration(self._FADE_OUT_MS)
        self._fade_out_anim.setStartValue(self.windowOpacity())
        self._fade_out_anim.setEndValue(0.0)
        self._fade_out_anim.setEasingCurve(QtCore.QEasingCurve.Type.InCubic)
        self._fade_out_anim.finished.connect(self._on_finished)
        self._fade_out_anim.start()

    def _on_finished(self):
        if OcrCopyChip._active is self:
            OcrCopyChip._active = None
        self.deleteLater()


def show_ocr_copy_toast(full_text: str, *, label: str = "Copy text",
                        done_label: str = "Copied"):
    """Show a clickable OCR-copy chip beside the cursor."""
    return OcrCopyChip(full_text, label=label, done_label=done_label)
