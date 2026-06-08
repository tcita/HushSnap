"""
Floating OCR text popup widget.
"""

from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import get_ocr_font_size, get_resource_dir
from ..constants import APP_ICON_FILENAME

# Minimum window size to prevent collapsing to zero
WINDOW_MIN_WIDTH = 280
WINDOW_MIN_HEIGHT = 180


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
        self._editing = False
        self._plain_text = ""

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
        outer.setContentsMargins(12, 12, 12, 12)  # Margin space for drop shadow

        panel = QtWidgets.QFrame()
        panel.setObjectName("ocrPanel")

        # Apply a premium soft drop shadow effect
        shadow = QtWidgets.QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(15)
        shadow.setColor(QtGui.QColor(0, 0, 0, 85))
        shadow.setOffset(0, 4)
        panel.setGraphicsEffect(shadow)
        panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        outer.addWidget(panel, 1)

        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ── header ───────────────────────────────────────────────────
        self.header_container = QtWidgets.QFrame()
        self.header_container.setObjectName("ocrHeader")
        header_layout = QtWidgets.QHBoxLayout(self.header_container)
        header_layout.setSpacing(8)
        header_layout.setContentsMargins(10, 6, 10, 6)

        self.pin_btn = QtWidgets.QPushButton()
        self.pin_btn.setObjectName("ocrPinBtn")
        self.pin_btn.setFixedSize(28, 24)
        self.pin_btn.setCheckable(True)
        self.pin_btn.clicked.connect(self._on_pin_toggled)
        header_layout.addWidget(self.pin_btn)

        header_layout.addStretch(1)

        self.close_btn = QtWidgets.QPushButton()
        self.close_btn.setObjectName("ocrCloseBtn")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setIcon(self._make_close_icon())
        self.close_btn.setIconSize(QtCore.QSize(10, 10))
        self.close_btn.clicked.connect(self.hide)
        header_layout.addWidget(self.close_btn)
        layout.addWidget(self.header_container)

        # ── text block (bubble container) ───────────────────────────
        self.text_block = QtWidgets.QFrame()
        self.text_block.setObjectName("ocrTextBlock")
        self.text_block.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        # Main layout for the bubble: text on top, buttons on bottom
        self.bubble_layout = QtWidgets.QVBoxLayout(self.text_block)
        self.bubble_layout.setContentsMargins(0, 0, 0, 0)
        self.bubble_layout.setSpacing(0)

        # Single QPlainTextEdit for both read and edit modes.
        # This guarantees perfect whitespace preservation and native scrolling.
        self.text_edit = QtWidgets.QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setObjectName("ocrText")
        self.text_edit.setCursor(QtCore.Qt.CursorShape.IBeamCursor)
        self.text_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.text_edit.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        # Enable native scrolling within the bubble
        self.text_edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.text_edit.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Use viewport margins to simulate padding inside the bubble
        self.text_edit.setViewportMargins(16, 12, 16, 4)
        
        self.bubble_layout.addWidget(self.text_edit, 1)

        # ── sticky footer (buttons) ─────────────────────────────────
        self.footer_container = QtWidgets.QFrame()
        self.footer_container.setObjectName("ocrFooter")
        footer_layout = QtWidgets.QHBoxLayout(self.footer_container)
        footer_layout.setContentsMargins(16, 4, 16, 12)
        footer_layout.setSpacing(4)
        footer_layout.addStretch(1)

        # ── read-mode buttons ─────────────────────────────────────
        self.copy_btn = QtWidgets.QPushButton()
        self.copy_btn.setObjectName("ocrCopyBtn")
        self.copy_btn.setFixedSize(28, 24)
        self.copy_btn.setIconSize(QtCore.QSize(14, 14))
        self.copy_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self._on_copy_clicked)
        footer_layout.addWidget(self.copy_btn)

        self.edit_btn = QtWidgets.QPushButton()
        self.edit_btn.setObjectName("ocrEditBtn")
        self.edit_btn.setFixedSize(28, 24)
        self.edit_btn.setIconSize(QtCore.QSize(15, 15))
        self.edit_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.edit_btn.clicked.connect(self._on_edit_clicked)
        footer_layout.addWidget(self.edit_btn)

        # ── edit-mode buttons ───────
        self.cancel_btn = QtWidgets.QPushButton()
        self.cancel_btn.setObjectName("ocrCancelBtn")
        self.cancel_btn.setFixedSize(28, 24)
        self.cancel_btn.setIconSize(QtCore.QSize(14, 14))
        self.cancel_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.cancel_btn.hide()
        footer_layout.addWidget(self.cancel_btn)

        self.update_btn = QtWidgets.QPushButton()
        self.update_btn.setObjectName("ocrUpdateBtn")
        self.update_btn.setFixedSize(28, 24)
        self.update_btn.setIconSize(QtCore.QSize(14, 14))
        self.update_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.update_btn.clicked.connect(self._on_update_clicked)
        self.update_btn.hide()
        footer_layout.addWidget(self.update_btn)

        self.bubble_layout.addWidget(self.footer_container)

        layout.addWidget(self.text_block, 1)

        # ── caret colour ────────────────────────────────────────────
        pal = self.text_edit.palette()
        pal.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#5fc98a"))
        self.text_edit.setPalette(pal)

        self._apply_stylesheet()
        self.apply_font_size()
        self._refresh_labels()

        # Compact default; height auto-fits content on show_text.
        # User resizes freely — scroll area handles overflow.
        self.resize(420, 200)

    # ── stylesheet ───────────────────────────────────────────────────
    def _apply_stylesheet(self):
        self.setStyleSheet(
            "/* Midnight Forest v2 Theme - Terminal Style with Chat Bubble */"

            "OcrPopup {"
            " background-color: transparent;"
            " border: none;"
            "}"

            "#ocrPanel {"
            " background-color: #0a1910;"
            " border: 1px solid #1a3a22;"
            " border-radius: 10px;"
            "}"

            "#ocrHeader {"
            " background-color: #0d1f17;"
            " border-bottom: 1px solid #1e4a30;"
            " border-top-left-radius: 8px;"
            " border-top-right-radius: 8px;"
            "}"

            "/* ── scroll area (text block list container) ── */"
            "#ocrScrollArea {"
            " background: transparent;"
            " border: none;"
            "}"

            "/* ── chat-bubble text block ── */"
            "#ocrTextBlock {"
            " background-color: #12261b;"
            " border: 1px solid #1e4a30;"
            " border-radius: 10px;"
            "}"
            "/* subtle glow when editing */"
            "#ocrTextBlock[editing=\"true\"] {"
            " border-color: #2e7d4f;"
            " background-color: #162e20;"
            "}"

            "#ocrText {"
            " background-color: transparent;"
            " color: #d4f5e2;"
            " border: none;"
            " border-radius: 0;"
            " padding: 0px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            " line-height: 1.8;"
            " selection-background-color: rgba(255,255,255,0.12);"
            " selection-color: #ffffff;"
            "}"
            "#ocrText:focus {"
            " background-color: #162e20;"
            "}"

            "/* ── buttons in the bubble ── */"
            "#ocrCopyBtn {"
            " color: #5fc98a;"
            " border: 1px solid #1e4a30;"
            " border-radius: 6px;"
            " background: #1e4a30;"
            " padding: 0;"
            " font-size: 12px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
            "#ocrCopyBtn:hover { background: #2e7d4f; border-color: #2e7d4f; }"

            "#ocrEditBtn {"
            " color: #5fc98a;"
            " border: 1px solid #1e4a30;"
            " border-radius: 6px;"
            " background: #1e4a30;"
            " padding: 0;"
            " font-size: 12px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
            "#ocrEditBtn:hover { background: #2e7d4f; border-color: #2e7d4f; }"

            "#ocrUpdateBtn {"
            " color: #d4f5e2;"
            " border: 1px solid #2e7d4f;"
            " border-radius: 6px;"
            " background: #2e7d4f;"
            " padding: 0;"
            " font-size: 12px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
            "#ocrUpdateBtn:hover { background: #3a9d62; border-color: #3a9d62; }"

            "#ocrCancelBtn {"
            " color: #5fc98a;"
            " border: 1px solid #1e4a30;"
            " border-radius: 6px;"
            " background: transparent;"
            " padding: 0;"
            " font-size: 12px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
            "#ocrCancelBtn:hover { background: rgba(46, 125, 79, 64); border-color: #2e7d4f; }"

            "#ocrPinBtn {"
            " color: #5fc98a;"
            " border: none;"
            " border-radius: 12px;"
            " background: transparent;"
            " font-size: 15px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
            "#ocrPinBtn:hover { background: rgba(46, 125, 79, 64); color: #d4f5e2; }"
            "#ocrPinBtn[pin=\"true\"] { color: #5fc98a; background: rgba(30, 74, 48, 120); }"

            "#ocrCloseBtn {"
            " border: none;"
            " border-radius: 6px;"
            " background: transparent;"
            "}"
            "#ocrCloseBtn:hover { background: #f44336; color: #FFF; }"

            "/* ── custom vertical scrollbar ── */"
            "QScrollBar:vertical {"
            " background: transparent;"
            " width: 6px;"
            " margin: 0px;"
            "}"
            "QScrollBar::handle:vertical {"
            " background: #1e4a30;"
            " min-height: 20px;"
            " border-radius: 3px;"
            "}"
            "QScrollBar::handle:vertical:hover {"
            " background: #2e7d4f;"
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
        self.edit_btn.setIcon(self._make_edit_icon())
        self.update_btn.setIcon(self._make_check_icon())
        self.cancel_btn.setIcon(self._make_x_icon())
        self.pin_btn.setIcon(self._make_pin_icon(self._pinned))
        self.pin_btn.setIconSize(QtCore.QSize(16, 16))
        self.close_btn.setIcon(self._make_close_icon())
        self.close_btn.setIconSize(QtCore.QSize(10, 10))

        self.copy_btn.setToolTip(self.translate("ocr_copy_btn"))
        for btn, key, fallback in [
            (self.edit_btn, "ocr_edit_btn", "Edit"),
            (self.update_btn, "ocr_update_btn", "Update"),
            (self.cancel_btn, "ocr_cancel_btn", "Cancel"),
        ]:
            tip = self.translate(key)
            btn.setToolTip(tip if tip != key else fallback)
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
        # Ensure weʼre in read-only display mode
        self._exit_edit_mode(save=False)
        
        # Use setPlainText to guarantee original indentation is preserved.
        # No more HTML conversion needed for Read mode.
        self.text_edit.setPlainText(text)

        self._is_refreshing = False

        # First adjust layout size synchronously (fit width to content)
        self._adjust_window_size(fit_width=True)

        # Ensure the bubble is always shown from the top
        self.text_edit.verticalScrollBar().setValue(0)

        # Align to the bottom-right corner of the active screen
        self._place_on_screen()

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
        return self._plain_text

    # ── edit mode ────────────────────────────────────────────────────
    def _on_edit_clicked(self):
        """Pencil button — enter edit mode."""
        self._enter_edit_mode()

    def _on_update_clicked(self):
        """Update button — save edits and return to read-only."""
        self._exit_edit_mode(save=True)

    def _on_cancel_clicked(self):
        """Cancel button — discard edits and return to read-only."""
        self._exit_edit_mode(save=False)

    def _enter_edit_mode(self):
        """Switch from read-only label to editable text edit."""
        self._editing = True
        self.text_edit.setReadOnly(False)
        self.text_edit.setFocus()
        self.text_edit.selectAll()

        # Swap button groups: hide Copy+Edit, show Update+Cancel
        self.copy_btn.hide()
        self.edit_btn.hide()
        self.update_btn.show()
        self.cancel_btn.show()

        # Visual feedback on the bubble
        self.text_block.setProperty("editing", True)
        self.text_block.style().unpolish(self.text_block)
        self.text_block.style().polish(self.text_block)

        # Fit window to new content height after layout
        QtCore.QTimer.singleShot(0, self._adjust_window_size)

    def _exit_edit_mode(self, save=True):
        """Switch from editable text edit back to read-only label."""
        if not self._editing:
            return  # nothing to do — avoids an unnecessary _adjust_window_size call
        self._editing = False
        if save:
            self._plain_text = self.text_edit.toPlainText()
            self.copy_text()
        else:
            # Restore original text if canceled
            self.text_edit.setPlainText(self._plain_text)
            
        self.text_edit.setReadOnly(True)

        # Swap button groups: hide Update+Cancel, show Copy+Edit
        self.update_btn.hide()
        self.cancel_btn.hide()
        self.copy_btn.show()
        self.edit_btn.show()

        # Remove visual feedback
        self.text_block.setProperty("editing", False)
        self.text_block.style().unpolish(self.text_block)
        self.text_block.style().polish(self.text_block)

        # Fit window back to read-only label height
        QtCore.QTimer.singleShot(0, self._adjust_window_size)

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

            # Window chrome: outer margins + panel padding + borders +
            #                 viewport margins + scrollbar + safety buffer
            chrome_w = 110
            desired_w = int(content_w + chrome_w)
            desired_w = max(desired_w, WINDOW_MIN_WIDTH)

            screen = QtWidgets.QApplication.screenAt(self.pos())
            if not screen:
                screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos()) or QtWidgets.QApplication.primaryScreen()
            if screen:
                max_w = int(screen.availableGeometry().width() * 0.75)
                desired_w = min(desired_w, max_w)

            self.resize(desired_w, self.height())

        # ── height ─────────────────────────────────────────────────
        # Viewport width = window − outer_margins(24) − panel_padding(12)
        #                  − viewport_margins(32) − borders(4) ≈ 72
        vp_w = self.width() - 72

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
        header_h = self.header_container.sizeHint().height()

        # chrome_h: panel margins(6+6) + spacing(6) + inner margins + panel borders(1+1) + header
        chrome_h = header_h + 70

        # bubble_h: text + block padding(8+14) + spacing(6) + buttons(24) + text_block borders(1+1)
        bubble_h = text_h + 22 + 34

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
            new_w = min(self.width(), max_w)
        else:
            new_w = self.width()

        self.text_block.setMinimumHeight(0)
        self.text_block.setMaximumHeight(16777215)

        self.resize(new_w, int(total_h))

        # Re-clamp position after resize — the window may now overflow
        # a screen edge (e.g. after growing taller than available space).
        if screen:
            x = max(area.left(), min(self.x(), area.right() - self.width()))
            y = max(area.top(), min(self.y(), area.bottom() - self.height()))
            self.move(x, y)

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
    def _make_edit_icon():
        """Pen-tool icon matching Lucide pen-tool style."""
        def draw_edit(color_str):
            pixmap = QtGui.QPixmap(24, 24)
            pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            p = QtGui.QPainter(pixmap)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            pen = QtGui.QPen(
                QtGui.QColor(color_str),
                1.5,
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
                QtCore.Qt.PenJoinStyle.RoundJoin,
            )
            p.setPen(pen)
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)

            # Pen nib — bottom-right diamond (path 1)
            nib = QtGui.QPainterPath()
            nib.moveTo(15.7, 21.3)
            nib.lineTo(14.3, 21.3)
            nib.lineTo(12.7, 19.7)
            nib.lineTo(12.7, 18.3)
            nib.lineTo(18.3, 12.7)
            nib.lineTo(19.7, 12.7)
            nib.lineTo(21.3, 14.3)
            nib.lineTo(21.3, 15.7)
            nib.closeSubpath()
            p.drawPath(nib)

            # Pen body — curved stroke from nib up-left (path 2)
            body = QtGui.QPainterPath()
            body.moveTo(18, 13)
            body.cubicTo(17, 10, 16.6, 6.1, 15.9, 5.4)
            body.cubicTo(10, 4, 3.2, 2.0, 2.0, 3.2)
            body.cubicTo(3, 8, 5.4, 15.9, 6.1, 16.6)
            body.cubicTo(8, 17, 13, 18, 13, 18)
            p.drawPath(body)

            # Rule line — diagonal (path 3)
            p.drawLine(QtCore.QPointF(2.3, 2.3), QtCore.QPointF(9.6, 9.6))

            # Pivot circle (path 4)
            p.drawEllipse(QtCore.QPointF(11, 11), 2, 2)
            p.end()
            return pixmap

        icon = QtGui.QIcon()
        icon.addPixmap(draw_edit("#5fc98a"), QtGui.QIcon.Mode.Normal)
        icon.addPixmap(draw_edit("#a3f2c2"), QtGui.QIcon.Mode.Active)
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

    def _make_pin_icon(self, checked=False):
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
        hit = 14
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

    def _update_cursor(self, edge):
        if edge in (
            QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.TopEdge,
            QtCore.Qt.Edge.RightEdge | QtCore.Qt.Edge.BottomEdge,
        ):
            self.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor)
        elif edge in (
            QtCore.Qt.Edge.RightEdge | QtCore.Qt.Edge.TopEdge,
            QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.BottomEdge,
        ):
            self.setCursor(QtCore.Qt.CursorShape.SizeBDiagCursor)
        elif edge & (QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.RightEdge):
            self.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
        elif edge & (QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.BottomEdge):
            self.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, event):
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def event(self, event):
        if (
            event.type() == QtCore.QEvent.Type.HoverMove
            and QtWidgets.QApplication.mouseButtons() == QtCore.Qt.MouseButton.NoButton
        ):
            self._update_cursor(self._get_edge(event.position().toPoint()))
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

    def _place_on_screen(self):
        """Position the popup at the bottom-right corner, clamped to screen bounds."""
        screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos()) or QtWidgets.QApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        margin = 20

        # Default: bottom-right corner (like the screenshot thumbnail)
        x = area.right() - self.width() - margin
        y = area.bottom() - self.height() - margin

        # Clamp so the window never overflows any screen edge
        x = max(x, area.left())
        y = max(y, area.top())
        x = min(x, area.right() - self.width())
        y = min(y, area.bottom() - self.height())

        self.move(x, y)

    # ── window events ────────────────────────────────────────────────
    def resizeEvent(self, event):
        """Handle window resize; QScrollArea.setWidgetResizable(True) manages internal width."""
        super().resizeEvent(event)

    def showEvent(self, event):
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
