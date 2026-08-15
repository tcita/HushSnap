import logging
from PyQt6 import QtCore, QtGui, QtWidgets

from .styles import BRAND_GREEN

logger = logging.getLogger(__name__)

# Global list to keep Toast instances alive (prevent GC) until they close themselves
_active_toasts = []


def _label_font(px: int) -> QtGui.QFont:
    """Font for toast text.

    ``PreferNoHinting``: full hinting snaps curved strokes to the pixel grid,
    which makes the corners of letters like e/a/d look jagged at small sizes.
    Disabling hinting lets antialiasing smooth those curves instead.
    """
    font = QtGui.QFont()
    font.setFamilies(["Microsoft YaHei", "Microsoft JhengHei", "Segoe UI"])
    font.setPixelSize(px)
    font.setHintingPreference(QtGui.QFont.HintingPreference.PreferNoHinting)
    return font


class Toast(QtWidgets.QFrame):
    """
    A sleek, non-intrusive floating notification (Toast).
    Fades and slides out automatically.

    ``variant="subtle"`` is the quiet style for HIGH-FREQUENCY messages
    (e.g. the auto-OCR completion toast that fires on every capture):
    a compact success card with a brand-green confirmation badge.

    Toasts at the default position (bottom-center of the cursor's screen)
    STACK upward: a new toast places itself above any live bottom-center
    toasts, so the auto-OCR toast and the thumbnail's Save-to-Desktop toast
    can never cover each other.
    """
    def __init__(self, text, parent=None, duration_ms=2000, is_error=False,
                 position=None, variant="normal", detail=None):
        super().__init__(parent)
        self.duration_ms = duration_ms
        self.is_error = is_error
        self.variant = variant
        self.position = position
        _active_toasts.append(self)

        subtle = variant == "subtle"
        self._radius = 12 if subtle else 8

        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if subtle:
            # Auto-OCR is frequent, so use a compact card rather than the
            # full toast. The badge gives it a clear success state while the
            # two-line hierarchy confirms exactly where the text went.
            layout.setContentsMargins(10, 9, 14, 9)
            layout.setSpacing(9)

            badge = QtWidgets.QLabel("✓")
            badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(24, 24)
            badge.setStyleSheet(
                f"QLabel {{ background-color: {BRAND_GREEN}; color: #09251E; "
                "border: none; border-radius: 12px; font-size: 15px; font-weight: 700; }}"
            )
            layout.addWidget(badge)

            text_layout = QtWidgets.QVBoxLayout()
            text_layout.setContentsMargins(0, 0, 0, 0)
            text_layout.setSpacing(1)
            self.label = QtWidgets.QLabel(text)
            self.label.setFont(_label_font(13))
            self.label.setStyleSheet(
                "QLabel { color: #FFFFFF; border: none; background: transparent; }"
            )
            text_layout.addWidget(self.label)
            if detail:
                detail_label = QtWidgets.QLabel(detail)
                detail_label.setFont(_label_font(11))
                detail_label.setStyleSheet(
                    "QLabel { color: rgba(255, 255, 255, 0.68); border: none; background: transparent; }"
                )
                text_layout.addWidget(detail_label)
            layout.addLayout(text_layout)
        else:
            # Left accent bar + title text.  The accent bar's own rounded-left
            # corners match the card silhouette (solid color, no border, so no
            # stylesheet corner artifact).
            accent_bar = QtWidgets.QFrame()
            accent_color = "#FF5252" if is_error else BRAND_GREEN
            accent_bar.setStyleSheet(
                f"background-color: {accent_color}; border-top-left-radius: 8px; border-bottom-left-radius: 8px;"
            )
            accent_bar.setFixedWidth(4)
            layout.addWidget(accent_bar)

            self.label = QtWidgets.QLabel(text)
            self.label.setFont(_label_font(14))
            self.label.setStyleSheet(
                "QLabel {"
                " background: transparent;"
                " color: #FFFFFF;"
                " padding: 12px 20px;"
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

    def paintEvent(self, event):
        # Self-drawn rounded card: dark background + thin light border.  Painted
        # with QPainter (not a stylesheet border) so the rounded corners stay
        # clean — a stylesheet ``border`` + ``border-radius`` anti-aliases the
        # semi-transparent border into bright 1px dots at the corners.
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 71), 1.0))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(28, 28, 28, 245)))
        painter.drawPath(path)

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
               variant="normal", detail=None):
    """Helper function to show a toast globally.

    ``variant="subtle"`` is the quiet brand-green pill for high-frequency
    automatic messages (auto-OCR completion); default-position toasts stack
    upward so they never cover each other.  User-action toasts keep the
    normal variant.
    """
    return Toast(text, duration_ms=duration_ms, is_error=is_error,
                 position=position, variant=variant, detail=detail)
