"""
Floating OCR text popup widget.
"""

from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import get_ocr_font_size, get_resource_dir
from ..constants import APP_ICON_FILENAME
from ..dpi import cursor_screen
from ..ocr.text import _iter_url_spans, find_url_at_position
from .styles import BRAND_GREEN

# Minimum window size to prevent collapsing to zero
WINDOW_MIN_WIDTH = 280
WINDOW_MIN_HEIGHT = 180
OUTER_MARGIN = 28  # Matches RESIZE_HIT — creates a clear "window chrome" ring
RESIZE_HIT = 28
CORNER_HIT = 52  # Wide corner zone — corners are point targets, need the extra room

# ── Size-adjustment layout constants ─────────────────────────────────────────
# Chrome estimates: outer margins + borders + viewport paddings + scrollbar.
_CHROME_WIDTH = 100
_CHROME_HEIGHT = 48
# Viewport reduction = outer_margins + panel_padding + viewport_margins + borders.
_VP_WIDTH_REDUCTION = 72
# Text width padding inside the viewport (16+16).
_TEXT_WIDTH_PAD = 32
# Minimum unwrapped line width before wrapping kicks in.
_MIN_LINE_WIDTH = 200
# Minimum text height (prevents collapsing to an invisible sliver).
_MIN_TEXT_HEIGHT = 40
# Line-spacing buffer added to the measured QTextDocument height.
_LINE_SPACING_BUFFER = 8
# Bubble padding: space between the text block and the bubble frame.
_BUBBLE_PAD = 22
# Screen-edge margins for clamping the final geometry.
_SCREEN_MARGIN_W = 40
_SCREEN_MARGIN_H = 60
# Screen-area fraction cap for max popup width.
_MAX_WIDTH_SCREEN_FRAC = 0.75
# Edge-attach threshold: if the window edge is within this many px of a screen
# edge, it stays anchored so the popup grows inward rather than pushing off-screen.
_EDGE_THRESHOLD = 80

# ── Loading-card layout constants ────────────────────────────────────────────
_LOADING_MAX_IMG_W = 480
_LOADING_MAX_IMG_H = 360
_LOADING_FALLBACK_W = 280
_LOADING_FALLBACK_H = 180
_LOADING_PROGRESS_BAR_H = 2
_LOADING_CARD_MIN_W = 280
_LOADING_CARD_MAX_W = 480
_LOADING_CARD_MAX_H = 400
_LOADING_SCREEN_FRAC = 0.55
# Default compact size before any text is loaded.
_DEFAULT_W = 420
_DEFAULT_H = 200

# ── Link styling (http(s) URLs in recognised text) ───────────────────────────
_LINK_COLOR = "#7ab7ff"          # light blue — reads on the dark card
_LINK_UNDERLINE = QtGui.QTextCharFormat.UnderlineStyle.SingleUnderline


class UrlHighlighter(QtGui.QSyntaxHighlighter):
    """Highlights http(s) URLs in the OCR text so they read as clickable links.

    Shares the URL span logic with the click/hover handling via
    ``ocr.text._iter_url_spans`` — whatever is coloured is exactly what
    Ctrl+Click will open.  Re-runs automatically whenever the document text
    changes (including user edits), so edited URLs stay highlighted.
    """

    def __init__(self, parent: QtGui.QTextDocument):
        super().__init__(parent)
        self._fmt = QtGui.QTextCharFormat()
        self._fmt.setForeground(QtGui.QColor(_LINK_COLOR))
        self._fmt.setUnderlineStyle(_LINK_UNDERLINE)
        self._fmt.setToolTip(None)

    def highlightBlock(self, text: str) -> None:
        for start, end, _ in _iter_url_spans(text):
            self.setFormat(start, end - start, self._fmt)


class OcrPopup(QtWidgets.QWidget):
    """Semi-transparent floating popup for recognized OCR text."""
    pin_toggled = QtCore.pyqtSignal(bool)

    def __init__(self, translate, parent=None):
        super().__init__(parent)
        self.translate = translate
        self._drag_pos = None
        self._last_pixmap = None
        self._pinned = False
        self._plain_text = ""
        self._anchor_pos = None  # (x, y) screen coords for thumbnail transition
        self._intended_rect = QtCore.QRect() # Track where we WANT to be, bypassing DPI/shadow offsets

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
        outer.setContentsMargins(
            OUTER_MARGIN, OUTER_MARGIN, OUTER_MARGIN, OUTER_MARGIN
        )

        # ── pin & close buttons (absolutely positioned, overlay on content) ──
        self.pin_btn = QtWidgets.QPushButton(self)
        self.pin_btn.setObjectName("ocrPinBtn")
        self.pin_btn.setFixedSize(24, 24)
        self.pin_btn.setCheckable(True)
        self.pin_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.pin_btn.clicked.connect(self._on_pin_toggled)

        self.close_btn = QtWidgets.QPushButton(self)
        self.close_btn.setObjectName("ocrCloseBtn")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.close_btn.setIcon(self._make_close_icon())
        self.close_btn.setIconSize(QtCore.QSize(14, 14))
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

        # ── loading state ──
        self.loading_container = QtWidgets.QWidget()
        self.loading_layout = QtWidgets.QVBoxLayout(self.loading_container)
        self.loading_layout.setContentsMargins(0, 0, 0, 0)
        self.loading_layout.setSpacing(0)

        self.loading_bar = QtWidgets.QProgressBar()
        self.loading_bar.setFixedHeight(2)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setStyleSheet(
            f"QProgressBar {{ background: transparent; border: none; }}"
            f"QProgressBar::chunk {{ background-color: {BRAND_GREEN}; }}"
        )
        
        self.loading_img_label = QtWidgets.QLabel()
        self.loading_img_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.loading_img_label.setStyleSheet("background: transparent; border-radius: 12px;")
        
        self.loading_layout.addWidget(self.loading_bar)
        self.loading_layout.addWidget(self.loading_img_label, 1)
        self.loading_container.hide()
        self.bubble_layout.addWidget(self.loading_container, 1)

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
        pal.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(BRAND_GREEN))
        # Ensure viewport is transparent
        pal.setColor(QtGui.QPalette.ColorRole.Base, QtCore.Qt.GlobalColor.transparent)
        self.text_edit.setPalette(pal)

        self._apply_stylesheet()
        self.apply_font_size()
        self._refresh_labels()

        # Highlight http(s) URLs as clickable links.  Attached to the document
        # so it re-runs on every edit (including user corrections).
        self._url_highlighter = UrlHighlighter(self.text_edit.document())

        # Compact default; height auto-fits content on show_text.
        # User resizes freely — scroll area handles overflow.
        self.resize(_DEFAULT_W, _DEFAULT_H)

        self._morph_anim = None
        self._anchor_pos = None
        self._anchor_geom = None
        self._is_loading = False

    # ── stylesheet ───────────────────────────────────────────────────
    def _apply_stylesheet(self):
        self.setStyleSheet((
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
            " font-size: 14px;"
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
            " border-radius: 12px;"
            " background: rgba(0, 0, 0, 100);"
            " font-size: 13px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
            "#ocrPinBtn:hover { background: rgba(95, 201, 138, 40); color: #5fc98a; }"
            "#ocrPinBtn[pin=\"true\"] { color: #5fc98a; background: rgba(95, 201, 138, 30); }"

            "#ocrCloseBtn {"
            " color: #999999;"
            " border: none;"
            " border-radius: 12px;"
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
        ).replace("#5fc98a", BRAND_GREEN))

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
        self.close_btn.setIconSize(QtCore.QSize(14, 14))

        self.copy_btn.setToolTip(self.translate("ocr_copy_btn"))
        self.pin_btn.setToolTip(self.translate("ocr_pin_btn"))
        self.close_btn.setToolTip(self.translate("close_btn"))

    # ── show / hide text ─────────────────────────────────────────────
    def show_loading(self, pixmap=None):
        """Show the popup in a loading state at the thumbnail's position."""
        self._is_loading = True
        self._last_pixmap = pixmap

        self.text_edit.hide()
        self.copy_btn.hide()
        self.loading_container.show()

        # ── determine the actual content size ──────────────────────────
        # Keep the image preview moderate so the loading card stays
        # close in width to the eventual text card — avoids a
        # jarring "expand then shrink" when OCR finishes.
        if pixmap:
            img_w, img_h = pixmap.width(), pixmap.height()
            scale = min(
                _LOADING_MAX_IMG_W / img_w,
                _LOADING_MAX_IMG_H / img_h,
                1.0,
            )
            content_w = int(img_w * scale)
            content_h = int(img_h * scale)
            self.loading_img_label.setPixmap(pixmap.scaled(
                content_w, content_h,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            content_w, content_h = _LOADING_FALLBACK_W, _LOADING_FALLBACK_H

        if self._anchor_geom:
            m = OUTER_MARGIN

            # Loading card width: bounded to a narrow band so the
            # transition to the text card is a 1D height change.
            card_w = max(content_w, _LOADING_CARD_MIN_W)
            card_w = min(card_w, _LOADING_CARD_MAX_W)
            card_h = min(content_h + _LOADING_PROGRESS_BAR_H, _LOADING_CARD_MAX_H)

            # Ensure it fits on screen
            screen = cursor_screen()
            if screen:
                area = screen.availableGeometry()
                card_w = min(card_w, int(area.width() * _LOADING_SCREEN_FRAC))
                card_h = min(card_h, int(area.height() * _LOADING_SCREEN_FRAC))

            target_w = card_w + 2 * m
            target_h = card_h + 2 * m

            # Centre on the thumbnail anchor …
            cx = self._anchor_geom.center().x()
            cy = self._anchor_geom.center().y()
            x = int(cx - target_w / 2)
            y = int(cy - target_h / 2)

            # … then clamp so the *full* target sits inside the screen
            if screen:
                x = max(area.left(), min(x, area.right() - target_w))
                y = max(area.top(), min(y, area.bottom() - target_h))

            self.setGeometry(x, y, target_w, target_h)
        else:
            self.resize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
            self._place_on_screen()

        self._refresh_labels()
        self.show()
        self.raise_()
        self.activateWindow()

    def show_text(self, text, pixmap=None, lines=None):
        self._is_loading = False
        if pixmap is not None:
            self._last_pixmap = pixmap

        self.loading_container.hide()
        self.text_edit.show()
        self.copy_btn.show()

        self._refresh_labels()
        self.apply_font_size()

        self._plain_text = text
        self.text_edit.setPlainText(text)

        was_visible = self.isVisible()
        self._adjust_window_size(fit_width=True)
        self.text_edit.verticalScrollBar().setValue(0)

        # Only position on first appearance; _adjust_window_size already
        # clamps to the screen for the target size.
        if not was_visible:
            self._place_on_screen()

        self._update_button_positions()
        self.show()
        self.raise_()
        self.activateWindow()

    def set_intended_geom(self, geom):
        """Update the target geometry that this window is moving towards."""
        self._intended_rect = QtCore.QRect(geom)

    def intended_pos(self):
        """Return the logical intended top-left, preferring _intended_rect over pos()."""
        if self._intended_rect.isValid():
            return self._intended_rect.topLeft()
        return self.pos()

    def move(self, *args):
        if len(args) == 1:
            self.set_intended_geom(QtCore.QRect(args[0], self.size()))
        else:
            self.set_intended_geom(QtCore.QRect(args[0], args[1], self.width(), self.height()))
        super().move(*args)

    def setGeometry(self, *args):
        if len(args) == 1:
            self.set_intended_geom(args[0])
        else:
            self.set_intended_geom(QtCore.QRect(*args))
        super().setGeometry(*args)

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
        if self._is_loading:
            # During loading, we don't adjust size based on text
            return

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
            max_line_px = max(max_line_px, float(_MIN_LINE_WIDTH))

            content_w = max_line_px

            # Window chrome: outer margins + borders +
            #                 viewport margins + scrollbar + safety buffer
            desired_w = int(content_w + _CHROME_WIDTH)

            # During the transition from loading, we might want to be smaller than WINDOW_MIN_WIDTH
            # if the text is very sparse, but usually text popups should have a baseline.
            desired_w = max(desired_w, WINDOW_MIN_WIDTH)

            screen = QtWidgets.QApplication.screenAt(self.pos())
            if not screen:
                screen = cursor_screen()
            if screen:
                max_w = int(screen.availableGeometry().width() * _MAX_WIDTH_SCREEN_FRAC)
                desired_w = min(desired_w, max_w)

            # Use immediate resize if not visible yet, otherwise it's handled by geometry animation
            if not self.isVisible():
                self.resize(desired_w, self.height())
            
            target_w = desired_w
        else:
            target_w = self.width()

        # ── height ─────────────────────────────────────────────────
        vp_w = target_w - _VP_WIDTH_REDUCTION

        text_w = max(vp_w - _TEXT_WIDTH_PAD, _MIN_LINE_WIDTH)

        # Use QTextDocument to calculate exact height of the plain text with wrapping
        td = QtGui.QTextDocument()
        td.setDocumentMargin(0)
        td.setDefaultFont(font)
        td.setTextWidth(text_w)
        # QPlainTextEdit uses plain text, so we use setPlainText for measurement
        td.setPlainText(text)

        # Add a small buffer for line spacing
        text_h = int(td.size().height()) + _LINE_SPACING_BUFFER
        td.deleteLater()

        # Respect minimum height
        text_h = max(text_h, _MIN_TEXT_HEIGHT)

        # Accumulate: bubble internals + chrome
        bubble_h = text_h + _BUBBLE_PAD

        total_h = _CHROME_HEIGHT + bubble_h
        total_h = max(total_h, WINDOW_MIN_HEIGHT)

        screen = QtWidgets.QApplication.screenAt(self.pos())
        if not screen or not self.isVisible():
            screen = cursor_screen()

        if screen:
            area = screen.availableGeometry()
            # Don't exceed screen dimensions
            max_w = area.width() - _SCREEN_MARGIN_W
            max_h = area.height() - _SCREEN_MARGIN_H
            total_h = min(total_h, max_h)
            new_w = min(target_w, max_w)
        else:
            new_w = target_w

        self.text_block.setMinimumHeight(0)
        self.text_block.setMaximumHeight(16777215)

        target_geom = self.geometry()
        old_right = target_geom.right()
        old_bottom = target_geom.bottom()

        target_geom.setWidth(new_w)
        target_geom.setHeight(int(total_h))

        if screen:
            area = screen.availableGeometry()

            # Anchor edges that already sit close to a screen border so
            # the popup feels "attached" — it grows inward instead of
            # pushing off-screen every time the text changes.
            if old_right >= area.right() - _EDGE_THRESHOLD:
                target_geom.moveRight(old_right)
            if old_bottom >= area.bottom() - _EDGE_THRESHOLD:
                target_geom.moveBottom(old_bottom)

            # Safety clamp — keeps the window on screen
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
        from .icon_utils import load_svg_icon
        return load_svg_icon("close", "#d4f5e2", "#ffffff", size=24)

    @staticmethod
    def _make_copy_icon():
        from .icon_utils import load_svg_icon
        return load_svg_icon("copy_simple", BRAND_GREEN, "#a3f2c2", size=24)

    @staticmethod
    def _make_check_icon():
        from .icon_utils import load_svg_icon
        return load_svg_icon("check", "#d4f5e2", "#ffffff", size=24)

    @staticmethod
    def _make_success_check_icon():
        from .icon_utils import load_svg_icon
        return load_svg_icon("check", BRAND_GREEN, "#a3f2c2", size=24)

    @staticmethod
    def _make_x_icon():
        from .icon_utils import load_svg_icon
        return load_svg_icon("close", BRAND_GREEN, "#a3f2c2", size=24)

    @staticmethod
    def _make_pin_icon(checked=False):
        from .icon_utils import load_svg_icon
        name = "pin" if checked else "pin_unlocked"
        return load_svg_icon(name, BRAND_GREEN, "#a3f2c2", size=24)

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
        border_success = QtGui.QColor(BRAND_GREEN)

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
        border_success = QtGui.QColor(BRAND_GREEN)
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
        corner = CORNER_HIT
        w, h = self.width(), self.height()

        # ── expanded corner detection ──────────────────────────────
        # Use a wider zone for corners so diagonal resize is easier to grab.
        near_left_c = pos.x() <= corner
        near_right_c = pos.x() >= w - corner
        near_top_c = pos.y() <= corner
        near_bottom_c = pos.y() >= h - corner

        if (near_left_c or near_right_c) and (near_top_c or near_bottom_c):
            if near_left_c:
                edge |= QtCore.Qt.Edge.LeftEdge
            if near_right_c:
                edge |= QtCore.Qt.Edge.RightEdge
            if near_top_c:
                edge |= QtCore.Qt.Edge.TopEdge
            if near_bottom_c:
                edge |= QtCore.Qt.Edge.BottomEdge
            return edge

        # ── standard edge detection ─────────────────────────────────
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
        """Intercept HoverMove and MousePress on child widgets for edge resizing
        and Ctrl+Click link opening."""
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
                if not edge:
                    self._apply_url_hover(obj, pos)
            elif event.type() == QtCore.QEvent.Type.MouseButtonPress:
                if edge and event.button() == QtCore.Qt.MouseButton.LeftButton:
                    if self.windowHandle():
                        self.windowHandle().startSystemResize(edge)
                        return True # Eat the event so child doesn't start selection
                elif event.button() == QtCore.Qt.MouseButton.LeftButton:
                    # Ctrl+Click on a URL opens it in the default browser.
                    ctrl = bool(
                        QtWidgets.QApplication.keyboardModifiers()
                        & QtCore.Qt.KeyboardModifier.ControlModifier
                    )
                    if ctrl:
                        url = self._url_under(obj, pos)
                        if url:
                            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
                            return True
        return super().eventFilter(obj, event)

    # ── link hover / open ─────────────────────────────────────────────
    def _url_under(self, obj, pos) -> str | None:
        """Return the URL under *pos* (relative to *obj*), or None.

        Only meaningful over the text viewport; returns None elsewhere so the
        surrounding edge-resize / drag logic is unaffected.
        """
        if obj is not self.text_edit.viewport():
            return None
        point = pos.toPoint() if hasattr(pos, "toPoint") else pos
        cursor = self.text_edit.cursorForPosition(point)
        block = cursor.block()
        return find_url_at_position(block.text(), cursor.position() - block.position())

    def _apply_url_hover(self, obj, pos):
        """On hover over a URL, show a "Ctrl+Click to open" tooltip; with Ctrl
        held also switch to the hand cursor.  No-op (default cursor preserved)
        when not over a URL."""
        url = self._url_under(obj, pos)
        if url is not None:
            ctrl = bool(
                QtWidgets.QApplication.keyboardModifiers()
                & QtCore.Qt.KeyboardModifier.ControlModifier
            )
            if ctrl:
                obj.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            QtWidgets.QToolTip.showText(
                obj.mapToGlobal(pos.toPoint() if hasattr(pos, "toPoint") else pos),
                self.translate("ocr_link_open_hint"),
                self.text_edit,
            )
        else:
            QtWidgets.QToolTip.hideText()

    def enterEvent(self, event):
        self._update_button_positions()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        QtWidgets.QToolTip.hideText()
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

    def clear_anchor(self):
        """Clear any stored morph anchor so the next show does not animate."""
        self._anchor_geom = None
        self._anchor_pos = None

    def _place_on_screen(self):
        """Position the popup, preferring the anchor point from a thumbnail click."""
        screen = cursor_screen()
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
        elif self.isVisible():
            # If already visible, just preserve current position but ensure it's on screen
            x, y = self.x(), self.y()
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
        ox, oy = OUTER_MARGIN, OUTER_MARGIN
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
            morph.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)  # smooth landing, no bounce
            
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

    def is_pinned(self) -> bool:
        """Return True if the popup is pinned."""
        return self._pinned

    def hideEvent(self, event):
        self._last_pixmap = None
        super().hideEvent(event)
        if self.testAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose):
            self.deleteLater()

