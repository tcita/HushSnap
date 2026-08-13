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

    ``variant="subtle"`` is the quiet style for HIGH-FREQUENCY messages
    (e.g. the auto-OCR completion toast that fires on every capture):
    a small brand-green pill — high contrast on both light and dark
    backgrounds, instantly recognizable, but compact and briefly visible.

    Toasts at the default position (bottom-center of the cursor's screen)
    STACK upward: a new toast places itself above any live bottom-center
    toasts, so the auto-OCR toast and the thumbnail's Save-to-Desktop toast
    can never cover each other.
    """
    def __init__(self, text, parent=None, duration_ms=2000, is_error=False,
                 position=None, variant="normal"):
        super().__init__(parent)
        self.duration_ms = duration_ms
        self.is_error = is_error
        self.variant = variant
        self.position = position
        _active_toasts.append(self)

        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)

        subtle = variant == "subtle"

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

        # Left accent bar (normal variant only)
        if not subtle:
            accent_bar = QtWidgets.QFrame()
            accent_color = "#FF5252" if is_error else BRAND_GREEN
            accent_bar.setStyleSheet(
                f"background-color: {accent_color}; border-top-left-radius: 8px; border-bottom-left-radius: 8px;"
            )
            accent_bar.setFixedWidth(4)
            layout.addWidget(accent_bar)

        # Content area
        self.label = QtWidgets.QLabel(text)
        if subtle:
            # Neutral dark pill + thin light border + small green check:
            # reads on light backgrounds by its darkness, on dark
            # backgrounds by the border; the green check carries the
            # success/identity signal without a wall of color.
            import html as _html
            self.label.setText(
                f"<span style='color: {BRAND_GREEN};'>✓</span> "
                f"{_html.escape(text)}"
            )
            self.label.setStyleSheet(
                "QLabel {"
                " background-color: rgba(28, 28, 28, 0.96);"
                " color: #FFFFFF;"
                " border: 1px solid rgba(255, 255, 255, 0.28);"
                " border-radius: 8px;"
                " padding: 7px 16px;"
                " font-size: 13px;"
                " font-weight: 400;"
                " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", \"Segoe UI\", sans-serif;"
                "}"
            )
        else:
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
            # Stack upward above any live bottom-center toasts so
            # simultaneous toasts (auto-OCR + Save-to-Desktop) never cover
            # each other.  Toasts with an explicit position= anchor
            # elsewhere and do not participate.
            for other in _active_toasts:
                if (other is not self and other.isVisible()
                        and other.position is None):
                    target_y -= other.height() + 8

        # Slide-up animation start position
        self.move(target_x, target_y + 20)
        self.setWindowOpacity(0.0)

        # Animations
        self.group = QtCore.QParallelAnimationGroup(self)

        # Fade and Slide in — subtle variant enters gently (no bounce)
        fade_in = QtCore.QPropertyAnimation(self, b"windowOpacity")
        fade_in.setDuration(180 if subtle else 300)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        slide_in = QtCore.QPropertyAnimation(self, b"pos")
        slide_in.setDuration(220 if subtle else 400)
        slide_in.setStartValue(QtCore.QPoint(target_x, target_y + 20))
        slide_in.setEndValue(QtCore.QPoint(target_x, target_y))
        slide_in.setEasingCurve(
            QtCore.QEasingCurve.Type.OutCubic if subtle else QtCore.QEasingCurve.Type.OutBack
        )

        self.group.addAnimation(fade_in)
        self.group.addAnimation(slide_in)

        # Drop shadow on the container widget to render inside the top-level window
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.container)
        if subtle:
            shadow.setBlurRadius(14)
            shadow.setColor(QtGui.QColor(0, 0, 0, 80))
            shadow.setOffset(0, 3)
        else:
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

def show_toast(text, duration_ms=2000, is_error=False, position=None,
               variant="normal"):
    """Helper function to show a toast globally.

    ``variant="subtle"`` is the quiet brand-green pill for high-frequency
    automatic messages (auto-OCR completion); default-position toasts stack
    upward so they never cover each other.  User-action toasts keep the
    normal variant.
    """
    return Toast(text, duration_ms=duration_ms, is_error=is_error,
                 position=position, variant=variant)
