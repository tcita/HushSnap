"""
Floating OCR text popup widget.
"""

from PyQt6 import QtCore, QtWidgets


class OcrPopup(QtWidgets.QWidget):
    """Semi-transparent floating popup for recognized OCR text."""
    language_changed = QtCore.pyqtSignal(str)
    engine_changed = QtCore.pyqtSignal(str)
    switch_language_requested = QtCore.pyqtSignal(str)
    open_language_settings_requested = QtCore.pyqtSignal()
    recapture_requested = QtCore.pyqtSignal()

    def __init__(self, translate, parent=None):
        super().__init__(parent)
        self.translate = translate
        self._drag_pos = None
        self._last_pixmap = None
        self._is_refreshing = False

        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self.resize(560, 360)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        panel = QtWidgets.QFrame()
        panel.setObjectName("ocrPanel")
        outer.addWidget(panel)

        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel(self.translate("ocr_popup_title"))
        self.title_label.setObjectName("ocrTitle")
        header.addWidget(self.title_label)
        header.addStretch(1)

        # Engine Selector
        self.engine_combo = QtWidgets.QComboBox()
        self.engine_combo.setObjectName("ocrEngineCombo")
        self.engine_combo.setFixedWidth(120)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed_idx)
        header.addWidget(self.engine_combo)

        # Language Selector
        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.setObjectName("ocrLangCombo")
        self.lang_combo.addItem("", "en-US")
        self.lang_combo.addItem("", "zh-CN")
        self.lang_combo.addItem("", "zh-TW")
        self.lang_combo.setFixedWidth(140)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed_idx)
        header.addWidget(self.lang_combo)

        self.recapture_btn = QtWidgets.QPushButton("↻")
        self.recapture_btn.setObjectName("ocrRecaptureBtn")
        self.recapture_btn.setFixedSize(28, 24)
        self.recapture_btn.clicked.connect(self.recapture_requested.emit)
        header.addWidget(self.recapture_btn)

        self.copy_btn = QtWidgets.QPushButton(self.translate("ocr_copy_btn"))
        self.copy_btn.setObjectName("ocrCopyBtn")
        self.copy_btn.setFixedHeight(24)
        self.copy_btn.clicked.connect(self.copy_text)
        header.addWidget(self.copy_btn)

        close_btn = QtWidgets.QPushButton("x")
        close_btn.setObjectName("ocrCloseBtn")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.hide)
        header.addWidget(close_btn)
        layout.addLayout(header)

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

        self.text_edit = QtWidgets.QPlainTextEdit()
        self.text_edit.setReadOnly(False)
        self.text_edit.setObjectName("ocrText")
        layout.addWidget(self.text_edit)

        # Add size grip for resizing frameless window
        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        sizegrip = QtWidgets.QSizeGrip(self)
        footer.addWidget(sizegrip)
        layout.addLayout(footer)

        self.setStyleSheet(
            "#ocrPanel {"
            " background-color: rgba(8, 44, 28, 220);"
            " border: 1px solid rgba(170, 220, 185, 64);"
            " border-radius: 14px;"
            "}"
            "#ocrTitle {"
            " color: #F3FFF6;"
            " font-size: 18px;"
            " font-weight: 600;"
            "}"
            "#ocrEngineCombo, #ocrLangCombo {"
            " background: rgba(190, 255, 212, 22);"
            " border: 1px solid rgba(190, 255, 212, 42);"
            " border-radius: 6px;"
            " color: #F3FFF6;"
            " padding: 2px 5px;"
            "}"
            "#ocrCopyBtn, #ocrRecaptureBtn {"
            " color: #E1F7E7;"
            " border: none;"
            " border-radius: 8px;"
            " background: rgba(190, 255, 212, 22);"
            " padding: 0 10px;"
            "}"
            "#ocrRecaptureBtn {"
            " padding: 0;"
            " font-size: 15px;"
            " font-weight: 600;"
            "}"
            "#ocrCopyBtn:hover, #ocrRecaptureBtn:hover {"
            " background: rgba(190, 255, 212, 34);"
            "}"
            "#ocrText {"
            " background: rgba(3, 25, 16, 118);"
            " border: 1px solid rgba(190, 255, 212, 28);"
            " border-radius: 10px;"
            " color: #F5FFF7;"
            " font-size: 16px;"
            " padding: 10px;"
            "}"
            "#ocrNotice {"
            " background: rgba(255, 197, 61, 26);"
            " border: 1px solid rgba(255, 197, 61, 90);"
            " border-radius: 10px;"
            "}"
            "#ocrNoticeLabel {"
            " color: #FFF4CC;"
            " font-size: 13px;"
            "}"
            "#ocrNoticeSwitchBtn, #ocrNoticeSettingsBtn {"
            " color: #FFF7DD;"
            " border: 1px solid rgba(255, 214, 122, 120);"
            " border-radius: 8px;"
            " background: rgba(255, 214, 122, 20);"
            " padding: 4px 10px;"
            "}"
            "#ocrNoticeSwitchBtn:hover, #ocrNoticeSettingsBtn:hover {"
            " background: rgba(255, 214, 122, 32);"
            "}"
            "#ocrCloseBtn {"
            " color: #E1F7E7;"
            " border: none;"
            " border-radius: 12px;"
            " background: rgba(190, 255, 212, 22);"
            "}"
            "#ocrCloseBtn:hover {"
            " background: rgba(190, 255, 212, 34);"
            "}"
        )
        self._refresh_labels()

    def _refresh_labels(self):
        # Only populate combos if they are empty to avoid object churn
        from ..constants import OCR_ENGINE_WINDOWS, OCR_ENGINE_RAPID
        
        if self.engine_combo.count() == 0:
            self.engine_combo.blockSignals(True)
            self.engine_combo.addItem(self.translate("ocr_engine_windows"), OCR_ENGINE_WINDOWS)
            self.engine_combo.addItem(self.translate("ocr_engine_rapid"), OCR_ENGINE_RAPID)
            self.engine_combo.blockSignals(False)
        else:
            # Update text for existing items in case language changed
            self.engine_combo.setItemText(0, self.translate("ocr_engine_windows"))
            self.engine_combo.setItemText(1, self.translate("ocr_engine_rapid"))

        # Refresh Language Combo labels
        english_index = self.lang_combo.findData("en-US")
        if english_index >= 0:
            self.lang_combo.setItemText(
                english_index,
                self.translate("ocr_lang_english"),
            )

        simplified_index = self.lang_combo.findData("zh-CN")
        if simplified_index >= 0:
            self.lang_combo.setItemText(
                simplified_index,
                self.translate("ocr_lang_chinese_simplified"),
            )

        traditional_index = self.lang_combo.findData("zh-TW")
        if traditional_index >= 0:
            self.lang_combo.setItemText(
                traditional_index,
                self.translate("ocr_lang_chinese_traditional"),
            )

        self.lang_combo.setToolTip(self.translate("ocr_lang_selector_tooltip"))
        self.recapture_btn.setToolTip(self.translate("ocr_recapture_tooltip"))
        self.recapture_btn.setAccessibleName(self.translate("ocr_recapture_tooltip"))

    def _on_lang_changed_idx(self, index):
        if not self._is_refreshing:
            lang_data = self.lang_combo.itemData(index)
            if lang_data:
                self.language_changed.emit(lang_data)

    def _on_engine_changed_idx(self, index):
        if not self._is_refreshing:
            engine_data = self.engine_combo.itemData(index)
            if engine_data:
                from ..constants import OCR_ENGINE_RAPID
                self.lang_combo.setVisible(engine_data != OCR_ENGINE_RAPID)
                self.engine_changed.emit(engine_data)

    def _emit_switch_language_requested(self):
        target_lang = self.notice_switch_btn.property("target_lang") or ""
        if target_lang:
            self.switch_language_requested.emit(str(target_lang))

    def show_text(self, text, pixmap=None, lang=None, engine=None):
        """Display OCR text and show popup near bottom-right corner."""
        self._is_refreshing = True
        if pixmap is not None:
            self._last_pixmap = pixmap
        
        if engine:
            idx = self.engine_combo.findData(engine)
            if idx >= 0:
                self.engine_combo.setCurrentIndex(idx)
                from ..constants import OCR_ENGINE_RAPID
                self.lang_combo.setVisible(engine != OCR_ENGINE_RAPID)

        if lang:
            idx = self.lang_combo.findData(lang)
            if idx >= 0:
                self.lang_combo.setCurrentIndex(idx)
            else:
                lowered = (lang or "").lower()
                if lowered.startswith(("zh-tw", "zh-hk", "zh-mo", "zh-hant")):
                    traditional_idx = self.lang_combo.findData("zh-TW")
                    if traditional_idx >= 0:
                        self.lang_combo.setCurrentIndex(traditional_idx)
                elif lowered.startswith("zh"):
                    simplified_idx = self.lang_combo.findData("zh-CN")
                    if simplified_idx >= 0:
                        self.lang_combo.setCurrentIndex(simplified_idx)
        self.title_label.setText(self.translate("ocr_popup_title"))
        self.copy_btn.setText(self.translate("ocr_copy_btn"))
        self._refresh_labels()
        self.text_edit.setPlainText(text)
        self._is_refreshing = False
        
        if not self.isVisible():
            self._place_bottom_right()
            self.show()
        
        self.raise_()
        self.activateWindow()

    def show_language_notice(self, message, available_lang=""):
        self.notice_label.setText(message)
        switch_label = self.translate(
            "ocr_lang_missing_switch_btn",
            available_lang=available_lang or self.translate("ocr_lang_installed_fallback"),
        )
        self.notice_switch_btn.setText(switch_label)
        self.notice_switch_btn.setProperty("target_lang", available_lang)
        self.notice_switch_btn.setEnabled(bool(available_lang))
        self.notice_settings_btn.setText(self.translate("ocr_lang_missing_open_settings_btn"))
        self.notice_frame.show()

    def hide_language_notice(self):
        self.notice_label.clear()
        self.notice_switch_btn.setProperty("target_lang", "")
        self.notice_frame.hide()

    @property
    def last_pixmap(self):
        return self._last_pixmap

    def copy_text(self):
        """Copy the current text editor content to the system clipboard."""
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is not None:
            text = self.text_edit.toPlainText()
            for _ in range(5):
                clipboard.setText(text)
                QtWidgets.QApplication.processEvents()
                if clipboard.text() == text:
                    break
                QtCore.QThread.msleep(10)

    def mousePressEvent(self, event):
        """Enable dragging the window."""
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """Update window position during drag."""
        if event.buttons() == QtCore.Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        """End drag operation."""
        self._drag_pos = None
        event.accept()

    def _place_bottom_right(self):
        screen = QtWidgets.QApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        margin = 24
        self.move(
            area.right() - self.width() - margin,
            area.bottom() - self.height() - margin,
        )
