"""
Floating OCR text popup widget.
"""

from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import get_ocr_font_size, get_resource_dir
from ..constants import APP_ICON_FILENAME

# Minimum window size to prevent collapsing to zero
WINDOW_MIN_WIDTH = 280
WINDOW_MIN_HEIGHT = 180
RESIZE_HIT = 24


class OcrPopup(QtWidgets.QWidget):
    """Semi-transparent floating popup for recognized OCR text."""
    pin_toggled = QtCore.pyqtSignal(bool)

    def __init__(self, translate, parent=None):
        super().__init__(parent)
        self.translate = translate
        self._drag_pos = None
        self._last_pixmap = None
        self._is_refreshing = False
        self._pinned = False
        self._plain_text = ""
        self._anchor_pos = None  # (x, y) screen coords for thumbnail transition

        self.setWindowFlags(
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)

        self._app_icon = QtGui.QIcon(str(get_resource_dir() / APP_ICON_FILENAME))

        # ── outer shell ──────────────────────────────────────────────
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)  # shadow space + breathing room

        # ── pin & close buttons (absolutely positioned, overlay on content) ──
        self.pin_btn = QtWidgets.QPushButton(self)
        self.pin_btn.setObjectName("ocrPinBtn")
        self.pin_btn.setFixedSize(22, 22)
        self.pin_btn.setCheckable(True)
        self.pin_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.pin_btn.clicked.connect(self._on_pin_toggled)

        self.close_btn = QtWidgets.QPushButton(self)
        self.close_btn.setObjectName("ocrCloseBtn")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.close_btn.setIcon(self._make_close_icon())
        self.close_btn.setIconSize(QtCore.QSize(10, 10))
        self.close_btn.clicked.connect(self.hide)

        # Copy button — floating bottom-right
        self.copy_btn = QtWidgets.QPushButton(self)
        self.copy_btn.setObjectName("ocrCopyBtn")
        self.copy_btn.setFixedSize(28, 24)
        self.copy_btn.setIconSize(QtCore.QSize(14, 14))
        self.copy_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self._on_copy_clicked)

        # ── unified card (the only container) ────────────────────────
        self.text_block = QtWidgets.QFrame()
        self.text_block.setObjectName("ocrTextBlock")
        self.text_block.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        # Drop shadow directly on the card
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.text_block)
        shadow.setBlurRadius(20)
        shadow.setColor(QtGui.QColor(0, 0, 0, 60))
        shadow.setOffset(0, 4)
        self.text_block.setGraphicsEffect(shadow)

        outer.addWidget(self.text_block, 1)

        # Bring overlay buttons to front (text_block would otherwise cover them)
        self.pin_btn.raise_()
        self.close_btn.raise_()
        self.copy_btn.raise_()

        # Main layout for the card: text on top, copy button on bottom
        self.bubble_layout = QtWidgets.QVBoxLayout(self.text_block)
        self.bubble_layout.setContentsMargins(0, 0, 0, 0)
        self.bubble_layout.setSpacing(0)

        # Single QPlainTextEdit for both read and edit modes.
        # This guarantees perfect whitespace preservation and native scrolling.
        self.text_edit = QtWidgets.QPlainTextEdit()
        self.text_edit.setObjectName("ocrText")
        # Don't force IBeamCursor — let the parent's edge-detection
        # show resize cursors at window borders.
        self.text_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.text_edit.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        # Enable native scrolling within the bubble
        self.text_edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.text_edit.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Use viewport margins to simulate padding inside the card.
        # Bottom margin leaves room for the floating copy button.
        self.text_edit.setViewportMargins(16, 12, 16, 30)

        self.bubble_layout.addWidget(self.text_edit, 1)

        # Install event filter on text_edit viewport so edge cursor
        # detection works even when the mouse is over the child widget.
        self.text_edit.viewport().installEventFilter(self)
        self.text_block.installEventFilter(self)

        # ── caret colour ────────────────────────────────────────────
        pal = self.text_edit.palette()
        pal.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#5fc98a"))
        # Ensure viewport is transparent
        pal.setColor(QtGui.QPalette.ColorRole.Base, QtCore.Qt.GlobalColor.transparent)
        self.text_edit.setPalette(pal)

        self._apply_stylesheet()
        self.apply_font_size()
        self._refresh_labels()

        # Compact default; height auto-fits content on show_text.
        # User resizes freely — scroll area handles overflow.
        self.resize(420, 200)

        self._morph_anim = None
        self._anchor_pos = None
        self._anchor_geom = None

    # ── stylesheet ───────────────────────────────────────────────────
    def _apply_stylesheet(self):
        self.setStyleSheet(
            "/* Neutral dark theme — matches thumbnail card */"

            "OcrPopup {"
            " background-color: transparent;"
            " border: none;"
            "}"

            "/* ── unified card ── */"
            "#ocrTextBlock {"
            " background-color: #1e1e1e;"
            " border: 1px solid rgba(255, 255, 255, 35);"
            " border-radius: 12px;"
            "}"
            "#ocrTextBlock:hover {"
            " border-color: rgba(95, 201, 138, 120);"
            "}"

            "#ocrText {"
            " background-color: transparent;"
            " color: #e0e0e0;"
            " border: none;"
            " border-radius: 0;"
            " padding: 0px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            " line-height: 1.6;"
            " selection-background-color: rgba(95, 201, 138, 80);"
            " selection-color: #ffffff;"
            "}"
            "#ocrText:focus {"
            " background-color: transparent;"
            "}"

            "/* ── floating overlay buttons ── */"
            "#ocrCopyBtn {"
            " color: #5fc98a;"
            " border: 1px solid rgba(255, 255, 255, 20);"
            " border-radius: 6px;"
            " background: #2a2a2a;"
            " padding: 0;"
            " font-size: 12px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
            "#ocrCopyBtn:hover { background: #333333; border-color: #5fc98a; }"

            "#ocrPinBtn {"
            " color: #999999;"
            " border: none;"
            " border-radius: 11px;"
            " background: rgba(0, 0, 0, 100);"
            " font-size: 13px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
            "#ocrPinBtn:hover { background: rgba(95, 201, 138, 40); color: #5fc98a; }"
            "#ocrPinBtn[pin=\"true\"] { color: #5fc98a; background: rgba(95, 201, 138, 30); }"

            "#ocrCloseBtn {"
            " color: #999999;"
            " border: none;"
            " border-radius: 11px;"
            " background: rgba(0, 0, 0, 100);"
            " font-size: 14px;"
            " font-weight: bold;"
            "}"
            "#ocrCloseBtn:hover { background: #f44336; color: #FFF; }"

            "/* ── custom vertical scrollbar ── */"
            "QScrollBar:vertical {"
            " background: transparent;"
            " width: 4px;"
            " margin: 4px 0px 4px 0px;"
            "}"
            "QScrollBar::handle:vertical {"
            " background: #444444;"
            " min-height: 20px;"
            " border-radius: 2px;"
            "}"
            "QScrollBar::handle:vertical:hover {"
            " background: #5fc98a;"
            "}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
            " height: 0px;"
            "}"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
            " background: transparent;"
            "}"
        )

    # ── paint / window chrome ────────────────────────────────────────
    def paintEvent(self, event):
        """Enable stylesheet support."""
        opt = QtWidgets.QStyleOption()
        opt.initFrom(self)
        p = QtGui.QPainter(self)
        self.style().drawPrimitive(QtWidgets.QStyle.PrimitiveElement.PE_Widget, opt, p, self)

    # ── label refresh ────────────────────────────────────────────────
    def _refresh_labels(self):
        self.copy_btn.setIcon(self._make_copy_icon())
        self.pin_btn.setIcon(self._make_pin_icon(self._pinned))
        self.pin_btn.setIconSize(QtCore.QSize(16, 16))
        self.close_btn.setIcon(self._make_close_icon())
        self.close_btn.setIconSize(QtCore.QSize(10, 10))

        self.copy_btn.setToolTip(self.translate("ocr_copy_btn"))
        self.pin_btn.setToolTip(self.translate("ocr_pin_btn"))
        self.close_btn.setToolTip(self.translate("close_btn"))

    # ── show / hide text ─────────────────────────────────────────────
    def show_text(self, text, pixmap=None, lines=None):
        self._is_refreshing = True
        if pixmap is not None:
            self._last_pixmap = pixmap

        self._refresh_labels()
        self.apply_font_size()

        self._plain_text = text
        self.text_edit.setPlainText(text)

        self._is_refreshing = False

        # First adjust layout size synchronously (fit width to content)
        self._adjust_window_size(fit_width=True)

        # Ensure the bubble is always shown from the top
        self.text_edit.verticalScrollBar().setValue(0)

        # Align to the bottom-right corner of the active screen
        self._place_on_screen()
        self._update_button_positions()

        self.show()
        self.raise_()
        self.activateWindow()

    # ── properties ───────────────────────────────────────────────────
    @property
    def last_pixmap(self):
        return self._last_pixmap

    # ── copy ─────────────────────────────────────────────────────────
    def copy_text(self):
        text = self.text_edit.toPlainText()
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)

    def get_plain_text(self):
        return self.text_edit.toPlainText()

    # ── height auto-fit (width stays user-controlled) ────────────────
    def _adjust_window_size(self, fit_width=False):
        """Fit window size to bubble content.

        Height is always adjusted to prevent vertical overflow.
        Width is adjusted only on the initial ``show_text`` call
        (*fit_width*=True); subsequent resize events honour the user's
        chosen width.
        """
        font = self.text_edit.font()
        fm = QtGui.QFontMetrics(font)
        text = self.text_edit.toPlainText() or " "

        # ── width: fit to the longest unwrapped line ──
        if fit_width:
            max_line_px = 0.0
            for line in text.split("\n"):
                w = fm.horizontalAdvance(line)
                if w > max_line_px:
                    max_line_px = w
            max_line_px = max(max_line_px, 200.0)

            content_w = max_line_px

            # Window chrome: outer margins + borders +
            #                 viewport margins + scrollbar + safety buffer
            chrome_w = 100
            desired_w = int(content_w + chrome_w)
            desired_w = max(desired_w, WINDOW_MIN_WIDTH)

            screen = QtWidgets.QApplication.screenAt(self.pos())
            if not screen:
                screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos()) or QtWidgets.QApplication.primaryScreen()
            if screen:
                max_w = int(screen.availableGeometry().width() * 0.75)
                desired_w = min(desired_w, max_w)

            # Use immediate resize if not visible yet, otherwise it's handled by geometry animation
            if not self.isVisible():
                self.resize(desired_w, self.height())
            
            target_w = desired_w
        else:
            target_w = self.width()

        # ── height ─────────────────────────────────────────────────
        # Viewport width = window − outer_margins(24) − panel_padding(12)
        #                  − viewport_margins(32) − borders(4) ≈ 72
        vp_w = target_w - 72

        # padding (16+16)
        text_w = max(vp_w - 32, 200)

        # Use QTextDocument to calculate exact height of the plain text with wrapping
        td = QtGui.QTextDocument()
        td.setDocumentMargin(0)
        td.setDefaultFont(font)
        td.setTextWidth(text_w)
        # QPlainTextEdit uses plain text, so we use setPlainText for measurement
        td.setPlainText(text)

        # Add a small buffer for line spacing
        text_h = int(td.size().height()) + 8
        td.deleteLater()

        # Respect minimum height
        text_h = max(text_h, 40)

        # Accumulate: bubble internals + chrome
        # chrome_h: outer margins + text_block borders + safety
        chrome_h = 48

        # bubble_h: text + viewport padding already in text_edit
        bubble_h = text_h + 22

        total_h = chrome_h + bubble_h
        total_h = max(total_h, WINDOW_MIN_HEIGHT)

        screen = QtWidgets.QApplication.screenAt(self.pos())
        if not screen or not self.isVisible():
            screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos()) or QtWidgets.QApplication.primaryScreen()

        if screen:
            area = screen.availableGeometry()
            # Don't exceed screen dimensions
            max_w = area.width() - 40
            max_h = area.height() - 60
            total_h = min(total_h, max_h)
            new_w = min(target_w, max_w)
        else:
            new_w = target_w

        self.text_block.setMinimumHeight(0)
        self.text_block.setMaximumHeight(16777215)

        target_geom = self.geometry()
        target_geom.setWidth(new_w)
        target_geom.setHeight(int(total_h))

        # Re-clamp position — the window may now overflow a screen edge
        if screen:
            area = screen.availableGeometry()
            tx = max(area.left(), min(target_geom.x(), area.right() - target_geom.width()))
            ty = max(area.top(), min(target_geom.y(), area.bottom() - target_geom.height()))
            target_geom.moveTo(tx, ty)

        if self.isVisible():
            # Already visible? Smoothly animate to the new size to prevent "jumping"
            # when OCR results populate or user edits text.
            if hasattr(self, "_size_anim") and self._size_anim:
                self._size_anim.stop()
            
            # If we are already visible, we don't want the next showEvent to trigger
            # another anchor animation.
            self._anchor_pos = None
            self._anchor_geom = None

            self._size_anim = QtCore.QPropertyAnimation(self, b"geometry")
            self._size_anim.setDuration(250)
            self._size_anim.setStartValue(self.geometry())
            self._size_anim.setEndValue(target_geom)
            self._size_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            self._size_anim.start()
        else:
            # Not yet visible? Just set the geometry directly so showEvent's 
            # zoom animation has the correct target size.
            self.setGeometry(target_geom)

    # ── pin setter ──────────────────────────────────────────────────
    def set_pinned(self, pinned):
        if bool(pinned) == bool(self._pinned):
            return
        self.pin_btn.blockSignals(True)
        self.pin_btn.setChecked(bool(pinned))
        self.pin_btn.blockSignals(False)
        self._on_pin_toggled(bool(pinned))

    def apply_font_size(self):
        font = self.text_edit.font()
        font.setPointSizeF(get_ocr_font_size() * 0.75)
        self.text_edit.setFont(font)

    # ── custom icons ─────────────────────────────────────────────────
    @staticmethod
    def _make_close_icon():
        """X icon for the top-right close button."""
        def draw_close(color_str, width):
            pixmap = QtGui.QPixmap(24, 24)
            pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            p = QtGui.QPainter(pixmap)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(
                QtGui.QPen(
                    QtGui.QColor(color_str),
                    width,
                    QtCore.Qt.PenStyle.SolidLine,
                    QtCore.Qt.PenCapStyle.RoundCap,
                    QtCore.Qt.PenJoinStyle.RoundJoin,
                )
            )
            p.drawLine(QtCore.QPointF(8, 8), QtCore.QPointF(16, 16))
            p.drawLine(QtCore.QPointF(16, 8), QtCore.QPointF(8, 16))
            p.end()
            return pixmap

        icon = QtGui.QIcon()
        icon.addPixmap(draw_close("#d4f5e2", 2.2), QtGui.QIcon.Mode.Normal)
        icon.addPixmap(draw_close("#ffffff", 2.5), QtGui.QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_copy_icon():
        def draw_copy(color_str):
            pixmap = QtGui.QPixmap(24, 24)
            pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            p = QtGui.QPainter(pixmap)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(
                QtGui.QPen(
                    QtGui.QColor(color_str),
                    2,
                    QtCore.Qt.PenStyle.SolidLine,
                    QtCore.Qt.PenCapStyle.RoundCap,
                    QtCore.Qt.PenJoinStyle.RoundJoin,
                )
            )
            p.drawRect(7, 7, 10, 10)
            p.drawPolyline([QtCore.QPointF(14, 4), QtCore.QPointF(4, 4), QtCore.QPointF(4, 14)])
            p.end()
            return pixmap

        icon = QtGui.QIcon()
        icon.addPixmap(draw_copy("#5fc98a"), QtGui.QIcon.Mode.Normal)
        icon.addPixmap(draw_copy("#a3f2c2"), QtGui.QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_check_icon():
        """Checkmark icon for the Update button."""
        def draw_check(color_str):
            pixmap = QtGui.QPixmap(24, 24)
            pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            p = QtGui.QPainter(pixmap)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(
                QtGui.QPen(
                    QtGui.QColor(color_str),
                    2.2,
                    QtCore.Qt.PenStyle.SolidLine,
                    QtCore.Qt.PenCapStyle.RoundCap,
                    QtCore.Qt.PenJoinStyle.RoundJoin,
                )
            )
            p.drawPolyline([
                QtCore.QPointF(4, 13),
                QtCore.QPointF(9, 18),
                QtCore.QPointF(20, 7),
            ])
            p.end()
            return pixmap

        icon = QtGui.QIcon()
        icon.addPixmap(draw_check("#d4f5e2"), QtGui.QIcon.Mode.Normal)
        icon.addPixmap(draw_check("#ffffff"), QtGui.QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_success_check_icon():
        """Green checkmark for copy-success animation (matches theme #5fc98a)."""
        def draw_success(color_str):
            pixmap = QtGui.QPixmap(24, 24)
            pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            p = QtGui.QPainter(pixmap)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(
                QtGui.QPen(
                    QtGui.QColor(color_str),
                    2.2,
                    QtCore.Qt.PenStyle.SolidLine,
                    QtCore.Qt.PenCapStyle.RoundCap,
                    QtCore.Qt.PenJoinStyle.RoundJoin,
                )
            )
            p.drawPolyline([
                QtCore.QPointF(4, 13),
                QtCore.QPointF(9, 18),
                QtCore.QPointF(20, 7),
            ])
            p.end()
            return pixmap

        icon = QtGui.QIcon()
        icon.addPixmap(draw_success("#5fc98a"), QtGui.QIcon.Mode.Normal)
        icon.addPixmap(draw_success("#a3f2c2"), QtGui.QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_x_icon():
        """X icon for the Cancel button."""
        def draw_x(color_str):
            pixmap = QtGui.QPixmap(24, 24)
            pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            p = QtGui.QPainter(pixmap)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(
                QtGui.QPen(
                    QtGui.QColor(color_str),
                    1.8,
                    QtCore.Qt.PenStyle.SolidLine,
                    QtCore.Qt.PenCapStyle.RoundCap,
                    QtCore.Qt.PenJoinStyle.RoundJoin,
                )
            )
            p.drawLine(QtCore.QPointF(6, 6), QtCore.QPointF(18, 18))
            p.drawLine(QtCore.QPointF(18, 6), QtCore.QPointF(6, 18))
            p.end()
            return pixmap

        icon = QtGui.QIcon()
        icon.addPixmap(draw_x("#5fc98a"), QtGui.QIcon.Mode.Normal)
        icon.addPixmap(draw_x("#a3f2c2"), QtGui.QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_pin_icon(checked=False):
        def draw_pin(color_str):
            pixmap = QtGui.QPixmap(24, 24)
            pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            p = QtGui.QPainter(pixmap)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(
                QtGui.QPen(
                    QtGui.QColor(color_str),
                    2,
                    QtCore.Qt.PenStyle.SolidLine,
                    QtCore.Qt.PenCapStyle.RoundCap,
                    QtCore.Qt.PenJoinStyle.RoundJoin,
                )
            )
            if not checked:
                p.translate(12, 12)
                p.rotate(-45)
                p.translate(-12, -12)
            path = QtGui.QPainterPath()
            path.moveTo(12, 17)
            path.lineTo(12, 22)
            path.moveTo(9, 11)
            path.lineTo(6, 14)
            path.lineTo(6, 16)
            path.lineTo(18, 16)
            path.lineTo(18, 14)
            path.lineTo(15, 11)
            path.lineTo(15, 6)
            path.lineTo(9, 6)
            path.closeSubpath()
            path.addEllipse(QtCore.QRectF(8, 2, 8, 4))
            p.drawPath(path)
            p.end()
            return pixmap

        icon = QtGui.QIcon()
        icon.addPixmap(draw_pin("#5fc98a"), QtGui.QIcon.Mode.Normal)
        icon.addPixmap(draw_pin("#a3f2c2"), QtGui.QIcon.Mode.Active)
        return icon

    # ── pin ──────────────────────────────────────────────────────────
    def _on_pin_toggled(self, checked):
        self._pinned = checked
        self.pin_btn.setProperty("pin", checked)
        self.pin_btn.setIcon(self._make_pin_icon(checked))
        self.pin_btn.style().unpolish(self.pin_btn)
        self.pin_btn.style().polish(self.pin_btn)
        self.pin_btn.setToolTip(
            self.translate("ocr_unpin_btn" if checked else "ocr_pin_btn")
        )
        self.pin_toggled.emit(checked)

    # ── copy button ──────────────────────────────────────────────────
    def _on_copy_clicked(self):
        self.copy_text()
        self._animate_copy_success()

    def _animate_copy_success(self):
        """Phase 1: smooth color morph to success state, then swap to checkmark icon."""
        btn = self.copy_btn

        # Cancel any in-flight animation (prevents double-click glitches)
        if hasattr(self, "_copy_anim") and self._copy_anim is not None:
            self._copy_anim.stop()
            self._copy_anim = None
        btn.setEnabled(False)

        bg_normal = QtGui.QColor("#1e4a30")
        bg_success = QtGui.QColor("#2a5a3a")
        border_normal = QtGui.QColor("#1e4a30")
        border_success = QtGui.QColor("#5fc98a")

        def _interp(a, b, t):
            return QtGui.QColor(
                int(a.red() + (b.red() - a.red()) * t),
                int(a.green() + (b.green() - a.green()) * t),
                int(a.blue() + (b.blue() - a.blue()) * t),
            )

        def _apply(bg, bd):
            btn.setStyleSheet(
                f"#ocrCopyBtn {{ background: {bg.name()}; border-color: {bd.name()}; }}"
            )

        self._copy_anim = QtCore.QVariantAnimation()
        self._copy_anim.setDuration(250)
        self._copy_anim.setStartValue(0.0)
        self._copy_anim.setEndValue(1.0)
        self._copy_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._copy_anim.valueChanged.connect(
            lambda v: _apply(
                _interp(bg_normal, bg_success, v),
                _interp(border_normal, border_success, v),
            )
        )

        def _on_phase1_done():
            btn.setIcon(self._make_success_check_icon())
            btn.setIconSize(QtCore.QSize(14, 14))
            QtCore.QTimer.singleShot(900, self._animate_copy_reverse)

        self._copy_anim.finished.connect(_on_phase1_done)
        self._copy_anim.start()

    def _animate_copy_reverse(self):
        """Phase 2: smooth color morph back to normal, restore copy icon."""
        btn = self.copy_btn

        bg_success = QtGui.QColor("#2a5a3a")
        bg_normal = QtGui.QColor("#1e4a30")
        border_success = QtGui.QColor("#5fc98a")
        border_normal = QtGui.QColor("#1e4a30")

        def _interp(a, b, t):
            return QtGui.QColor(
                int(a.red() + (b.red() - a.red()) * t),
                int(a.green() + (b.green() - a.green()) * t),
                int(a.blue() + (b.blue() - a.blue()) * t),
            )

        def _apply(bg, bd):
            btn.setStyleSheet(
                f"#ocrCopyBtn {{ background: {bg.name()}; border-color: {bd.name()}; }}"
            )

        self._copy_anim = QtCore.QVariantAnimation()
        self._copy_anim.setDuration(350)
        self._copy_anim.setStartValue(0.0)
        self._copy_anim.setEndValue(1.0)
        self._copy_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._copy_anim.valueChanged.connect(
            lambda v: _apply(
                _interp(bg_success, bg_normal, v),
                _interp(border_success, border_normal, v),
            )
        )

        def _on_done():
            btn.setIcon(self._make_copy_icon())
            btn.setIconSize(QtCore.QSize(14, 14))
            btn.setStyleSheet("")  # clear inline style → global QSS takes over
            btn.setEnabled(True)
            self._copy_anim = None

        self._copy_anim.finished.connect(_on_done)
        self._copy_anim.start()

    # ── window resize / drag ─────────────────────────────────────────
    def _get_edge(self, pos):
        edge = QtCore.Qt.Edge(0)
        hit = RESIZE_HIT
        w, h = self.width(), self.height()
        if pos.x() <= hit:
            edge |= QtCore.Qt.Edge.LeftEdge
        elif pos.x() >= w - hit:
            edge |= QtCore.Qt.Edge.RightEdge
        if pos.y() <= hit:
            edge |= QtCore.Qt.Edge.TopEdge
        elif pos.y() >= h - hit:
            edge |= QtCore.Qt.Edge.BottomEdge
        return edge

    def _update_cursor(self, edge, target=None):
        if edge in (
            QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.TopEdge,
            QtCore.Qt.Edge.RightEdge | QtCore.Qt.Edge.BottomEdge,
        ):
            cursor = QtCore.Qt.CursorShape.SizeFDiagCursor
        elif edge in (
            QtCore.Qt.Edge.RightEdge | QtCore.Qt.Edge.TopEdge,
            QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.BottomEdge,
        ):
            cursor = QtCore.Qt.CursorShape.SizeBDiagCursor
        elif edge & (QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.RightEdge):
            cursor = QtCore.Qt.CursorShape.SizeHorCursor
        elif edge & (QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.BottomEdge):
            cursor = QtCore.Qt.CursorShape.SizeVerCursor
        else:
            cursor = None

        res_cursor = cursor or QtCore.Qt.CursorShape.ArrowCursor
        self.setCursor(res_cursor)
        
        if target:
            if cursor:
                target.setCursor(cursor)
            else:
                # Restore default: IBeam for text viewport, Arrow for others
                if target == self.text_edit.viewport():
                    target.setCursor(QtCore.Qt.CursorShape.IBeamCursor)
                else:
                    target.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def eventFilter(self, obj, event):
        """Intercept HoverMove and MousePress on child widgets for edge resizing."""
        if event.type() in (QtCore.QEvent.Type.HoverMove, QtCore.QEvent.Type.MouseButtonPress):
            # Map child coords to window coords
            if hasattr(event, "position"):
                pos = event.position()
            else:
                # Fallback for older event types if any
                pos = event.pos() if hasattr(event, "pos") else QtCore.QPointF(0, 0)
            
            global_pos = obj.mapToGlobal(pos.toPoint() if hasattr(pos, "toPoint") else pos)
            local_pos = self.mapFromGlobal(global_pos)
            edge = self._get_edge(local_pos)
            
            if event.type() == QtCore.QEvent.Type.HoverMove:
                self._update_cursor(edge, obj)
            elif event.type() == QtCore.QEvent.Type.MouseButtonPress:
                if edge and event.button() == QtCore.Qt.MouseButton.LeftButton:
                    if self.windowHandle():
                        self.windowHandle().startSystemResize(edge)
                        return True # Eat the event so child doesn't start selection
        return super().eventFilter(obj, event)

    def enterEvent(self, event):
        self._update_button_positions()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def event(self, event):
        if event.type() == QtCore.QEvent.Type.HoverMove:
            pos = event.position().toPoint()
            self._update_cursor(self._get_edge(pos), self)
        return super().event(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            edge = self._get_edge(event.position().toPoint())
            if edge and self.windowHandle():
                self.windowHandle().startSystemResize(edge)
            else:
                # Use startSystemMove for smoother native-feeling drag
                if self.windowHandle():
                    self.windowHandle().startSystemMove()
                else:
                    self._drag_pos = (
                        event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    )
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == QtCore.Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

    def set_anchor_pos(self, x, y, width=None, height=None):
        """Set preferred screen position for the next show.
        If width/height are provided, a morph animation will be triggered."""
        if width and height:
            self._anchor_geom = QtCore.QRect(int(x - width/2), int(y - height/2), width, height)
        else:
            self._anchor_pos = (x, y)
            self._anchor_geom = None

    def _place_on_screen(self):
        """Position the popup, preferring the anchor point from a thumbnail click."""
        screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos()) or QtWidgets.QApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        margin = 20

        if self._anchor_geom is not None:
            # Use morph logic: we need the target size first
            target_w, target_h = self.width(), self.height()
            
            # Align centre of target with centre of anchor
            ax, ay = self._anchor_geom.center().x(), self._anchor_geom.center().y()
            x = int(ax - target_w / 2)
            y = int(ay - target_h / 2)
        elif self._anchor_pos is not None:
            ax, ay = self._anchor_pos
            self._anchor_pos = None
            # Place popup so its centre aligns with the anchor (the thumbnail centre)
            x = int(ax - self.width() / 2)
            y = int(ay - self.height() / 2)
        else:
            # Default: bottom-right corner
            x = area.right() - self.width() - margin
            y = area.bottom() - self.height() - margin

        # Clamp so the window never overflows any screen edge
        x = max(x, area.left())
        y = max(y, area.top())
        x = min(x, area.right() - self.width())
        y = min(y, area.bottom() - self.height())

        self.move(x, y)

    def _update_button_positions(self):
        """Position floating overlay buttons on the card."""
        ox, oy = 18, 18  # outer margin
        btn_margin = 6
        # Pin — top-left
        self.pin_btn.move(ox + btn_margin, oy + btn_margin)
        # Close — top-right
        close_x = self.width() - ox - self.close_btn.width() - btn_margin
        self.close_btn.move(close_x, oy + btn_margin)
        # Copy — bottom-right
        copy_x = self.width() - ox - self.copy_btn.width() - btn_margin
        copy_y = self.height() - oy - self.copy_btn.height() - btn_margin
        self.copy_btn.move(copy_x, copy_y)
        # Ensure overlay buttons stay on top of text_block
        self.pin_btn.raise_()
        self.close_btn.raise_()
        self.copy_btn.raise_()

    # ── window events ────────────────────────────────────────────────
    def resizeEvent(self, event):
        """Handle window resize and update overlay button positions."""
        super().resizeEvent(event)
        self._update_button_positions()

    def showEvent(self, event):
        # Fade-in and optional Morph from thumbnail
        self.setWindowOpacity(0.0)
        
        target_geom = self.geometry()
        
        if hasattr(self, "_anchor_geom") and self._anchor_geom:
            start_geom = self._anchor_geom
            self._anchor_geom = None
            
            # Combine Opacity and Geometry into a ParallelAnimationGroup
            self._show_anim = QtCore.QParallelAnimationGroup(self)
            
            # 1. Opacity
            fade = QtCore.QPropertyAnimation(self, b"windowOpacity")
            fade.setDuration(250)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            
            # 2. Geometry (Morph)
            morph = QtCore.QPropertyAnimation(self, b"geometry")
            morph.setDuration(300)
            morph.setStartValue(start_geom)
            morph.setEndValue(target_geom)
            morph.setEasingCurve(QtCore.QEasingCurve.Type.OutBack) # subtle bounce
            
            self._show_anim.addAnimation(fade)
            self._show_anim.addAnimation(morph)
            self._show_anim.start()
        else:
            self._fade_in = QtCore.QPropertyAnimation(self, b"windowOpacity")
            self._fade_in.setDuration(150)
            self._fade_in.setStartValue(0.0)
            self._fade_in.setEndValue(1.0)
            self._fade_in.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            self._fade_in.start()

        super().showEvent(event)
        if self.windowHandle() and not self._app_icon.isNull():
            self.windowHandle().setIcon(self._app_icon)

    def changeEvent(self, event):
        if (
            event.type() == QtCore.QEvent.Type.ActivationChange
            and not self._pinned
            and not self.isActiveWindow()
        ):
            self.hide()
        super().changeEvent(event)

    def hideEvent(self, event):
        self._last_pixmap = None
        super().hideEvent(event)
