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
    language_changed = QtCore.pyqtSignal(str)
    engine_changed = QtCore.pyqtSignal(str)
    switch_language_requested = QtCore.pyqtSignal(str)
    open_language_settings_requested = QtCore.pyqtSignal()
    pin_toggled = QtCore.pyqtSignal(bool)

    def __init__(self, translate, parent=None):
        super().__init__(parent)
        self.translate = translate
        self._drag_pos = None
        self._last_pixmap = None
        self._is_refreshing = False
        self._pinned = False
        self._editing = False

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
        outer.setContentsMargins(1, 1, 1, 1)  # Thin window border

        panel = QtWidgets.QFrame()
        panel.setObjectName("ocrPanel")
        panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        outer.addWidget(panel, 1)

        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── header ───────────────────────────────────────────────────
        self.header_container = QtWidgets.QFrame()
        self.header_container.setObjectName("ocrHeader")
        header_layout = QtWidgets.QHBoxLayout(self.header_container)
        header_layout.setSpacing(8)
        header_layout.setContentsMargins(10, 6, 10, 6)

        header_layout.addStretch(1)

        self.pin_btn = QtWidgets.QPushButton()
        self.pin_btn.setObjectName("ocrPinBtn")
        self.pin_btn.setFixedSize(28, 24)
        self.pin_btn.setCheckable(True)
        self.pin_btn.clicked.connect(self._on_pin_toggled)
        header_layout.addWidget(self.pin_btn)

        self.close_btn = QtWidgets.QPushButton("✕")
        self.close_btn.setObjectName("ocrCloseBtn")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.clicked.connect(self.hide)
        header_layout.addWidget(self.close_btn)
        layout.addWidget(self.header_container)

        # ── hidden engine combo ──────────────────────────────────────
        self.engine_combo = QtWidgets.QComboBox()
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed_idx)

        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.addItem("", "en-US")
        self.lang_combo.addItem("", "zh-CN")
        self.lang_combo.addItem("", "zh-TW")
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed_idx)

        # ── language notice ──────────────────────────────────────────
        self.notice_frame = QtWidgets.QFrame()
        self.notice_frame.setObjectName("ocrNotice")
        self.notice_frame.hide()
        notice_layout = QtWidgets.QVBoxLayout(self.notice_frame)
        notice_layout.setContentsMargins(12, 10, 12, 10)
        notice_layout.setSpacing(8)

        self.notice_label = QtWidgets.QLabel("")
        self.notice_label.setObjectName("ocrNoticeLabel")
        self.notice_label.setWordWrap(True)
        notice_layout.addWidget(self.notice_label)

        notice_actions = QtWidgets.QHBoxLayout()
        notice_actions.setSpacing(8)
        self.notice_switch_btn = QtWidgets.QPushButton("")
        self.notice_switch_btn.setObjectName("ocrNoticeSwitchBtn")
        self.notice_switch_btn.clicked.connect(self._emit_switch_language_requested)
        notice_actions.addWidget(self.notice_switch_btn)

        self.notice_settings_btn = QtWidgets.QPushButton("")
        self.notice_settings_btn.setObjectName("ocrNoticeSettingsBtn")
        self.notice_settings_btn.clicked.connect(self.open_language_settings_requested.emit)
        notice_actions.addWidget(self.notice_settings_btn)
        notice_actions.addStretch(1)
        notice_layout.addLayout(notice_actions)
        layout.addWidget(self.notice_frame)

        # ── scroll area (text block list container) ──────────────────
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setObjectName("ocrScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        # ── text block (bubble) ──────────────────────────────────────
        self.text_block = QtWidgets.QFrame()
        self.text_block.setObjectName("ocrTextBlock")
        self.text_block.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        text_block_layout = QtWidgets.QVBoxLayout(self.text_block)
        text_block_layout.setContentsMargins(16, 14, 16, 14)
        text_block_layout.setSpacing(6)

        # Read-only display label (visible by default)
        self.text_label = QtWidgets.QLabel()
        self.text_label.setObjectName("ocrTextLabel")
        self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.text_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.text_label.setCursor(QtCore.Qt.CursorShape.IBeamCursor)
        self.text_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        text_block_layout.addWidget(self.text_label)

        # Editable text edit (hidden until pencil is clicked)
        self.text_edit = QtWidgets.QPlainTextEdit()
        self.text_edit.setReadOnly(False)
        self.text_edit.setObjectName("ocrText")
        self.text_edit.setCursor(QtCore.Qt.CursorShape.IBeamCursor)
        self.text_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.text_edit.setMinimumHeight(80)
        self.text_edit.hide()
        text_block_layout.addWidget(self.text_edit)

        # Button row — read group (Copy + Edit) and edit group (Update + Cancel)
        self.btn_row = QtWidgets.QHBoxLayout()
        self.btn_row.setContentsMargins(0, 0, 0, 0)
        self.btn_row.setSpacing(4)

        self.btn_row.addStretch(1)

        # ── read-mode buttons ─────────────────────────────────────
        self.copy_btn = QtWidgets.QPushButton()
        self.copy_btn.setObjectName("ocrCopyBtn")
        self.copy_btn.setFixedSize(28, 24)
        self.copy_btn.setIconSize(QtCore.QSize(14, 14))
        self.copy_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self._on_copy_clicked)
        self.btn_row.addWidget(self.copy_btn)

        self.edit_btn = QtWidgets.QPushButton()
        self.edit_btn.setObjectName("ocrEditBtn")
        self.edit_btn.setFixedSize(28, 24)
        self.edit_btn.setIconSize(QtCore.QSize(15, 15))
        self.edit_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.edit_btn.clicked.connect(self._on_edit_clicked)
        self.btn_row.addWidget(self.edit_btn)

        # ── edit-mode buttons (hidden until pencil clicked) ───────
        self.update_btn = QtWidgets.QPushButton()
        self.update_btn.setObjectName("ocrUpdateBtn")
        self.update_btn.setFixedSize(28, 24)
        self.update_btn.setIconSize(QtCore.QSize(14, 14))
        self.update_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.update_btn.clicked.connect(self._on_update_clicked)
        self.update_btn.hide()
        self.btn_row.addWidget(self.update_btn)

        self.cancel_btn = QtWidgets.QPushButton()
        self.cancel_btn.setObjectName("ocrCancelBtn")
        self.cancel_btn.setFixedSize(28, 24)
        self.cancel_btn.setIconSize(QtCore.QSize(14, 14))
        self.cancel_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.cancel_btn.hide()
        self.btn_row.addWidget(self.cancel_btn)

        text_block_layout.addLayout(self.btn_row)

        self.scroll_area.setWidget(self.text_block)
        # Left/right spacing per spec §II — container padding controls
        # distance from bubble to window edge, not max-width on bubble.
        self.scroll_area.setViewportMargins(16, 10, 16, 10)
        layout.addWidget(self.scroll_area, 1)  # stretch=1, fills remaining space

        # ── status bar ───────────────────────────────────────────────
        self.status_bar = QtWidgets.QFrame()
        self.status_bar.setObjectName("ocrStatusBar")
        status_layout = QtWidgets.QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(10, 4, 10, 8)
        status_layout.setSpacing(6)

        self.lang_combo_inline = QtWidgets.QComboBox()
        self.lang_combo_inline.setObjectName("ocrLangComboInline")
        self.lang_combo_inline.addItem("", "en-US")
        self.lang_combo_inline.addItem("", "zh-CN")
        self.lang_combo_inline.addItem("", "zh-TW")
        self.lang_combo_inline.setFixedWidth(130)
        self.lang_combo_inline.currentIndexChanged.connect(self._sync_lang_inline_to_combo)
        status_layout.addWidget(self.lang_combo_inline)

        status_layout.addStretch(1)
        layout.addWidget(self.status_bar)

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
            " background-color: #0a1910;"
            " border: 1px solid #1a3a22;"
            " border-radius: 10px;"
            "}"

            "#ocrPanel {"
            " background-color: transparent;"
            " border: none;"
            " border-radius: 8px;"
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

            "#ocrTextLabel {"
            " color: #d4f5e2;"
            " background: transparent;"
            " border: none;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            " padding: 0px;"
            " line-height: 1.8;"
            " selection-background-color: rgba(255,255,255,0.12);"
            " selection-color: #ffffff;"
            "}"

            "#ocrText {"
            " background-color: #162e20;"
            " color: #d4f5e2;"
            " border: none;"
            " border-radius: 0;"
            " padding: 0px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            " line-height: 1.8;"
            " selection-background-color: rgba(255,255,255,0.12);"
            " selection-color: #ffffff;"
            "}"

            "#ocrStatusBar {"
            " background-color: #12261b;"
            " border-top: 1px solid rgba(94, 201, 138, 0.1);"
            " border-bottom-left-radius: 8px;"
            " border-bottom-right-radius: 8px;"
            "}"

            "/* ── buttons in the bubble ── */"
            "#ocrCopyBtn {"
            " color: #5fc98a;"
            " border: 1px solid #1e4a30;"
            " border-radius: 6px;"
            " background: #1e4a30;"
            " padding: 0;"
            " font-size: 12px;"
            "}"
            "#ocrCopyBtn:hover { background: #2e7d4f; border-color: #2e7d4f; }"
            "#ocrCopyBtn[copied=\"true\"] { background: #2a5a3a; border-color: #5fc98a; }"

            "#ocrEditBtn {"
            " color: #5fc98a;"
            " border: 1px solid #1e4a30;"
            " border-radius: 6px;"
            " background: #1e4a30;"
            " padding: 0;"
            " font-size: 12px;"
            "}"
            "#ocrEditBtn:hover { background: #2e7d4f; border-color: #2e7d4f; }"

            "#ocrUpdateBtn {"
            " color: #d4f5e2;"
            " border: 1px solid #2e7d4f;"
            " border-radius: 6px;"
            " background: #2e7d4f;"
            " padding: 0;"
            " font-size: 12px;"
            "}"
            "#ocrUpdateBtn:hover { background: #3a9d62; border-color: #3a9d62; }"

            "#ocrCancelBtn {"
            " color: #5fc98a;"
            " border: 1px solid #1e4a30;"
            " border-radius: 6px;"
            " background: transparent;"
            " padding: 0;"
            " font-size: 12px;"
            "}"
            "#ocrCancelBtn:hover { background: rgba(46, 125, 79, 64); border-color: #2e7d4f; }"

            "#ocrPinBtn {"
            " color: #5fc98a;"
            " border: none;"
            " border-radius: 12px;"
            " background: transparent;"
            " font-size: 15px;"
            "}"
            "#ocrPinBtn:hover { background: rgba(46, 125, 79, 64); color: #d4f5e2; }"
            "#ocrPinBtn[pin=\"true\"] { color: #5fc98a; background: rgba(30, 74, 48, 120); }"

            "#ocrCloseBtn {"
            " color: #d4f5e2;"
            " border: none;"
            " border-radius: 12px;"
            " background: rgba(30, 74, 48, 64);"
            " font-size: 15px;"
            "}"
            "#ocrCloseBtn:hover { background: #f44336; color: #FFF; }"

            "#ocrLangComboInline {"
            " background: #1e4a30;"
            " border: 1px solid #1e4a30;"
            " border-radius: 6px;"
            " color: #d4f5e2;"
            " padding: 2px 5px;"
            " font-size: 12px;"
            "}"
            "#ocrNotice {"
            " background-color: #0d1f17;"
            " border-bottom: 1px solid #1e4a30;"
            "}"
            "#ocrNoticeLabel { color: #5fc98a; }"
            "#ocrNoticeSwitchBtn, #ocrNoticeSettingsBtn {"
            " background: #1e4a30;"
            " color: #5fc98a;"
            " border-radius: 6px;"
            " padding: 4px 8px;"
            "}"
            "#ocrNoticeSwitchBtn:hover, #ocrNoticeSettingsBtn:hover { background: #2e7d4f; }"
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
        from ..constants import OCR_ENGINE_WINDOWS, OCR_ENGINE_RAPID
        if self.engine_combo.count() == 0:
            self.engine_combo.blockSignals(True)
            self.engine_combo.addItem(self.translate("ocr_engine_windows"), OCR_ENGINE_WINDOWS)
            self.engine_combo.addItem(self.translate("ocr_engine_rapid"), OCR_ENGINE_RAPID)
            self.engine_combo.blockSignals(False)
        else:
            self.engine_combo.setItemText(0, self.translate("ocr_engine_windows"))
            self.engine_combo.setItemText(1, self.translate("ocr_engine_rapid"))

        self.copy_btn.setIcon(self._make_copy_icon())
        self.edit_btn.setIcon(self._make_edit_icon())
        self.update_btn.setIcon(self._make_check_icon())
        self.cancel_btn.setIcon(self._make_x_icon())
        self.pin_btn.setIcon(self._make_pin_icon(self._pinned))
        self.pin_btn.setIconSize(QtCore.QSize(16, 16))

        for combo in (self.lang_combo, self.lang_combo_inline):
            for code, key in [("en-US", "ocr_lang_english"), ("zh-CN", "ocr_lang_chinese_simplified"), ("zh-TW", "ocr_lang_chinese_traditional")]:
                idx = combo.findData(code)
                if idx >= 0:
                    combo.setItemText(idx, self.translate(key))

        self.lang_combo_inline.setToolTip(self.translate("ocr_lang_selector_tooltip"))
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

    # ── language / engine slots ──────────────────────────────────────
    def _on_lang_changed_idx(self, index):
        if not self._is_refreshing:
            lang_data = self.lang_combo.itemData(index)
            if lang_data:
                inline_idx = self.lang_combo_inline.findData(lang_data)
                if inline_idx >= 0 and inline_idx != self.lang_combo_inline.currentIndex():
                    self.lang_combo_inline.blockSignals(True)
                    self.lang_combo_inline.setCurrentIndex(inline_idx)
                    self.lang_combo_inline.blockSignals(False)
                self.language_changed.emit(lang_data)

    def _sync_lang_inline_to_combo(self, index):
        if not self._is_refreshing:
            lang_data = self.lang_combo_inline.itemData(index)
            if lang_data:
                idx = self.lang_combo.findData(lang_data)
                if idx >= 0:
                    self.lang_combo.setCurrentIndex(idx)

    def _on_engine_changed_idx(self, index):
        if not self._is_refreshing:
            engine_data = self.engine_combo.itemData(index)
            if engine_data:
                from ..constants import OCR_ENGINE_RAPID
                self.lang_combo_inline.setVisible(engine_data != OCR_ENGINE_RAPID)
                self.engine_changed.emit(engine_data)

    def _emit_switch_language_requested(self):
        target = self.notice_switch_btn.property("target_lang")
        if target:
            self.switch_language_requested.emit(str(target))

    # ── show / hide text ─────────────────────────────────────────────
    def show_text(self, text, pixmap=None, lang=None, engine=None):
        self._is_refreshing = True
        if pixmap is not None:
            self._last_pixmap = pixmap
        if engine:
            idx = self.engine_combo.findData(engine)
            if idx >= 0:
                self.engine_combo.setCurrentIndex(idx)
                from ..constants import OCR_ENGINE_RAPID
                self.lang_combo_inline.setVisible(engine != OCR_ENGINE_RAPID)
        if lang:
            idx = self.lang_combo.findData(lang)
            if idx < 0:
                lowered = (lang or "").lower()
                if lowered.startswith(("zh-tw", "zh-hk", "zh-mo", "zh-hant")):
                    idx = self.lang_combo.findData("zh-TW")
                elif lowered.startswith("zh"):
                    idx = self.lang_combo.findData("zh-CN")
            if idx >= 0:
                self.lang_combo.setCurrentIndex(idx)
                inline_idx = self.lang_combo_inline.findData(self.lang_combo.itemData(idx))
                if inline_idx >= 0:
                    self.lang_combo_inline.blockSignals(True)
                    self.lang_combo_inline.setCurrentIndex(inline_idx)
                    self.lang_combo_inline.blockSignals(False)

        self._refresh_labels()
        self.apply_font_size()

        # Ensure weʼre in read-only display mode
        self._exit_edit_mode(save=False)
        self.text_label.setText(text)
        self.text_edit.setPlainText(text)

        self._is_refreshing = False

        if not self.isVisible():
            self._place_near_cursor()

        self.show()
        self.raise_()
        self.activateWindow()
        # Fit window height to bubble content after layout settles
        QtCore.QTimer.singleShot(0, self._adjust_window_size)

    def show_language_notice(self, message, available_lang=""):
        self.notice_label.setText(message)
        self.notice_switch_btn.setText(
            self.translate(
                "ocr_lang_missing_switch_btn",
                available_lang=available_lang or self.translate("ocr_lang_installed_fallback"),
            )
        )
        self.notice_switch_btn.setProperty("target_lang", available_lang)
        self.notice_switch_btn.setEnabled(bool(available_lang))
        self.notice_settings_btn.setText(self.translate("ocr_lang_missing_open_settings_btn"))
        self.notice_frame.show()

    def hide_language_notice(self):
        self.notice_frame.hide()

    # ── properties ───────────────────────────────────────────────────
    @property
    def last_pixmap(self):
        return self._last_pixmap

    # ── copy ─────────────────────────────────────────────────────────
    def copy_text(self):
        text = self.text_edit.toPlainText() if self._editing else self.text_label.text()
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)

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
        text = self.text_label.text()
        self.text_edit.setPlainText(text)
        self.text_label.hide()
        self.text_edit.show()
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
            return
        self._editing = False
        if save:
            text = self.text_edit.toPlainText()
            self.text_label.setText(text)
        self.text_edit.hide()
        self.text_label.show()

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
    def _adjust_window_size(self):
        """Fit window height to bubble content; width is untouched."""
        # Force pending layout so widgets have their final widths
        QtWidgets.QApplication.processEvents()

        if self._editing:
            widget = self.text_edit
        else:
            widget = self.text_label

        # 16px internal padding refers to text_block_layout margins
        # widget.width() should be accurate after processEvents()
        text_w = max(widget.width(), 200)
        font = widget.font()
        text = self.text_edit.toPlainText() if self._editing else self.text_label.text()

        td = QtGui.QTextDocument()
        td.setDefaultFont(font)
        td.setTextWidth(text_w)
        td.setPlainText(text or " ")
        text_h = int(td.size().height()) + 4
        td.deleteLater()

        # Respect minimum height (especially important for text_edit which is 80px)
        text_h = max(text_h, widget.minimumHeight(), 20)

        # Accumulate: bubble internals + chrome
        header_h = self.header_container.sizeHint().height()
        status_h = self.status_bar.sizeHint().height()
        notice_h = (
            self.notice_frame.sizeHint().height()
            if not self.notice_frame.isHidden()
            else 0
        )
        
        # chrome_h: viewport 10+10, outer 1+1, window border 1+1
        chrome_h = header_h + notice_h + status_h + 24
        
        # bubble_h: text + block padding (14+14) + spacing(6) + buttons(24) + borders(1+1)
        # Using 62 instead of 60 to provide a tiny bit of extra breathing room
        bubble_h = text_h + 28 + 34
        
        total_h = chrome_h + bubble_h
        total_h = max(total_h, WINDOW_MIN_HEIGHT)

        screen = QtWidgets.QApplication.screenAt(self.pos()) or QtWidgets.QApplication.primaryScreen()
        if screen:
            # Don't exceed screen height
            max_allowed = screen.availableGeometry().height() - 60
            total_h = min(total_h, max_allowed)

        self.resize(self.width(), int(total_h))

    # ── engine / pin setter ──────────────────────────────────────────
    def set_engine(self, engine):
        if self._is_refreshing:
            return
        idx = self.engine_combo.findData(engine)
        if idx >= 0:
            self.engine_combo.setCurrentIndex(idx)

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
        label_font = QtGui.QFont(font)
        self.text_label.setFont(label_font)

    # ── custom icons ─────────────────────────────────────────────────
    @staticmethod
    def _make_copy_icon():
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pixmap)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(
            QtGui.QPen(
                QtGui.QColor("#5fc98a"),
                2,
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
                QtCore.Qt.PenJoinStyle.RoundJoin,
            )
        )
        p.drawRect(7, 7, 10, 10)
        p.drawPolyline([QtCore.QPointF(14, 4), QtCore.QPointF(4, 4), QtCore.QPointF(4, 14)])
        p.end()
        return QtGui.QIcon(pixmap)

    @staticmethod
    def _make_edit_icon():
        """Pen-tool icon matching Lucide pen-tool style."""
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pixmap)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        pen = QtGui.QPen(
            QtGui.QColor("#5fc98a"),
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
        return QtGui.QIcon(pixmap)

    @staticmethod
    def _make_check_icon():
        """Checkmark icon for the Update button."""
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pixmap)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(
            QtGui.QPen(
                QtGui.QColor("#d4f5e2"),
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
        return QtGui.QIcon(pixmap)

    @staticmethod
    def _make_x_icon():
        """X icon for the Cancel button."""
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pixmap)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(
            QtGui.QPen(
                QtGui.QColor("#5fc98a"),
                1.8,
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
                QtCore.Qt.PenJoinStyle.RoundJoin,
            )
        )
        p.drawLine(QtCore.QPointF(6, 6), QtCore.QPointF(18, 18))
        p.drawLine(QtCore.QPointF(18, 6), QtCore.QPointF(6, 18))
        p.end()
        return QtGui.QIcon(pixmap)

    def _make_pin_icon(self, checked=False):
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pixmap)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(
            QtGui.QPen(
                QtGui.QColor("#5fc98a"),
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
        return QtGui.QIcon(pixmap)

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
        self.copy_btn.setProperty("copied", True)
        self.copy_btn.style().unpolish(self.copy_btn)
        self.copy_btn.style().polish(self.copy_btn)
        QtCore.QTimer.singleShot(
            1200,
            lambda: [
                self.copy_btn.setProperty("copied", False),
                self.copy_btn.style().unpolish(self.copy_btn),
                self.copy_btn.style().polish(self.copy_btn),
            ],
        )

    # ── window resize / drag ─────────────────────────────────────────
    def _get_edge(self, pos):
        edge = QtCore.Qt.Edge(0)
        hit = 8
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

    def _place_near_cursor(self):
        screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos()) or QtWidgets.QApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        cursor = QtGui.QCursor.pos()
        gap = 20
        self.move(
            min(max(cursor.x() + gap, area.left()), area.right() - self.width()),
            min(max(cursor.y() + gap, area.top()), area.bottom() - self.height()),
        )

    # ── window events ────────────────────────────────────────────────
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
