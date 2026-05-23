"""
Floating OCR text popup widget.
"""

from PyQt6 import QtCore, QtGui, QtWidgets


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
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        header.setContentsMargins(0, 0, 0, 0)

        self.title_label = QtWidgets.QLabel(self.translate("ocr_popup_title"))
        self.title_label.setObjectName("ocrTitle")
        header.addWidget(self.title_label)

        self.editable_hint_label = QtWidgets.QLabel("")
        self.editable_hint_label.setObjectName("ocrEditableHint")
        header.addWidget(self.editable_hint_label)

        header.addStretch(1)

        self.recapture_btn = QtWidgets.QPushButton()
        self.recapture_btn.setObjectName("ocrRecaptureBtn")
        self.recapture_btn.setFixedSize(28, 24)
        self.recapture_btn.setIconSize(QtCore.QSize(16, 16))
        self.recapture_btn.clicked.connect(self.recapture_requested.emit)
        header.addWidget(self.recapture_btn)

        self.copy_btn = QtWidgets.QPushButton()
        self.copy_btn.setObjectName("ocrCopyBtn")
        self.copy_btn.setFixedSize(28, 24)
        self.copy_btn.setIconSize(QtCore.QSize(16, 16))
        self.copy_btn.clicked.connect(self._on_copy_clicked)
        header.addWidget(self.copy_btn)

        self.close_btn = QtWidgets.QPushButton("✕")
        self.close_btn.setObjectName("ocrCloseBtn")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.clicked.connect(self.hide)
        header.addWidget(self.close_btn)
        layout.addLayout(header)

        # Engine / Language combos — kept off-layout, driven by status bar tabs
        self.engine_combo = QtWidgets.QComboBox()
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed_idx)

        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.addItem("", "en-US")
        self.lang_combo.addItem("", "zh-CN")
        self.lang_combo.addItem("", "zh-TW")
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed_idx)

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
        self.text_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.MinimumExpanding,
        )
        self.text_edit.setContentsMargins(0, 0, 0, 0)
        self.text_edit.setViewportMargins(0, 0, 0, 0)
        self.text_edit.document().setDocumentMargin(0)
        self.text_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.text_edit)

        # Status bar: engine tabs (left) + hint (right)
        from ..constants import OCR_ENGINE_WINDOWS, OCR_ENGINE_RAPID

        self.status_bar = QtWidgets.QFrame()
        self.status_bar.setObjectName("ocrStatusBar")
        status_layout = QtWidgets.QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(10, 4, 4, 4)
        status_layout.setSpacing(6)

        self.engine_tab_rapid = QtWidgets.QPushButton(self.translate("ocr_engine_rapid"))
        self.engine_tab_rapid.setObjectName("ocrEngineTab")
        self.engine_tab_rapid.setProperty("engine", OCR_ENGINE_RAPID)
        self.engine_tab_rapid.clicked.connect(self._on_engine_tab_clicked)
        status_layout.addWidget(self.engine_tab_rapid)

        self.engine_tab_windows = QtWidgets.QPushButton(self.translate("ocr_engine_windows"))
        self.engine_tab_windows.setObjectName("ocrEngineTab")
        self.engine_tab_windows.setProperty("engine", OCR_ENGINE_WINDOWS)
        self.engine_tab_windows.clicked.connect(self._on_engine_tab_clicked)
        status_layout.addWidget(self.engine_tab_windows)

        self.lang_combo_inline = QtWidgets.QComboBox()
        self.lang_combo_inline.setObjectName("ocrLangComboInline")
        self.lang_combo_inline.addItem("", "en-US")
        self.lang_combo_inline.addItem("", "zh-CN")
        self.lang_combo_inline.addItem("", "zh-TW")
        self.lang_combo_inline.setFixedWidth(130)
        self.lang_combo_inline.currentIndexChanged.connect(self._sync_lang_inline_to_combo)
        status_layout.addWidget(self.lang_combo_inline)

        status_layout.addStretch(1)

        sizegrip = QtWidgets.QSizeGrip(self)
        status_layout.addWidget(sizegrip, 0, QtCore.Qt.AlignmentFlag.AlignBottom | QtCore.Qt.AlignmentFlag.AlignRight)

        layout.addWidget(self.status_bar)

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
            "#ocrEditableHint {"
            " color: rgba(220, 240, 225, 130);"
            " font-size: 11px;"
            " margin-left: 4px;"
            "}"
            "#ocrRecaptureBtn {"
            " color: #E1F7E7;"
            " border: none;"
            " border-radius: 8px;"
            " background: rgba(190, 255, 212, 22);"
            " padding: 0;"
            " font-size: 15px;"
            " font-weight: 600;"
            "}"
            "#ocrRecaptureBtn:hover {"
            " background: rgba(190, 255, 212, 34);"
            "}"
            "#ocrCopyBtn {"
            " color: #C8FFD4;"
            " border: 1px solid rgba(100, 220, 140, 60);"
            " border-radius: 8px;"
            " background: rgba(80, 200, 120, 35);"
            " padding: 0;"
            " font-weight: 600;"
            "}"
            "#ocrCopyBtn:hover {"
            " background: rgba(80, 200, 120, 55);"
            "}"
            "#ocrCopyBtn[copied=\"true\"] {"
            " background: rgba(80, 200, 120, 45);"
            " color: #B0FFC0;"
            "}"
            "#ocrCloseBtn {"
            " color: #E1F7E7;"
            " border: none;"
            " border-radius: 12px;"
            " background: rgba(190, 255, 212, 22);"
            " font-size: 15px;"
            "}"
            "#ocrCloseBtn:hover {"
            " background: rgba(255, 80, 70, 180);"
            " color: #FFF;"
            "}"
            "#ocrText {"
            " background: rgba(3, 25, 16, 118);"
            " border: 1px solid rgba(190, 255, 212, 28);"
            " border-radius: 10px;"
            " color: #F5FFF7;"
            " font-size: 16px;"
            " padding: 10px 10px 10px 10px;"
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
            "#ocrStatusBar {"
            " background: rgba(8, 44, 28, 220);"
            " border-top: 1px solid rgba(170, 220, 185, 40);"
            " border-radius: 0 0 14px 14px;"
            "}"
            "#ocrEngineTab {"
            " color: rgba(200, 225, 210, 140);"
            " border: 1px solid transparent;"
            " border-radius: 6px;"
            " background: transparent;"
            " padding: 3px 10px;"
            " font-size: 12px;"
            "}"
            "#ocrEngineTab:hover {"
            " color: #E1F7E7;"
            "}"
            "#ocrEngineTab[selected=\"true\"] {"
            " color: #E8FFF0;"
            " background: rgba(190, 255, 212, 25);"
            " border: 1px solid rgba(190, 255, 212, 55);"
            "}"
            "#ocrLangComboInline {"
            " background: rgba(190, 255, 212, 18);"
            " border: 1px solid rgba(190, 255, 212, 35);"
            " border-radius: 6px;"
            " color: #E1F7E7;"
            " padding: 2px 5px;"
            " font-size: 12px;"
            "}"
        )
        self._refresh_labels()

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

        # Icons
        self.recapture_btn.setIcon(self._make_recapture_icon())
        self.copy_btn.setIcon(self._make_copy_icon())

        # Engine tab labels
        self.engine_tab_rapid.setText(self.translate("ocr_engine_rapid"))
        self.engine_tab_windows.setText(self.translate("ocr_engine_windows"))

        # Refresh Language Combo labels (hidden master)
        for combo in (self.lang_combo, self.lang_combo_inline):
            english_index = combo.findData("en-US")
            if english_index >= 0:
                combo.setItemText(english_index, self.translate("ocr_lang_english"))
            simplified_index = combo.findData("zh-CN")
            if simplified_index >= 0:
                combo.setItemText(simplified_index, self.translate("ocr_lang_chinese_simplified"))
            traditional_index = combo.findData("zh-TW")
            if traditional_index >= 0:
                combo.setItemText(traditional_index, self.translate("ocr_lang_chinese_traditional"))

        self.lang_combo_inline.setToolTip(self.translate("ocr_lang_selector_tooltip"))
        self.recapture_btn.setToolTip(self.translate("ocr_recapture_tooltip"))
        self.recapture_btn.setAccessibleName(self.translate("ocr_recapture_tooltip"))
        self.copy_btn.setToolTip(self.translate("ocr_copy_btn"))
        self.editable_hint_label.setText(f"({self.translate('ocr_editable_hint')})")

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
                self.lang_combo_inline.setVisible(engine_data != OCR_ENGINE_RAPID)
                self._update_engine_tab_selection()
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
                self._update_engine_tab_selection()
                from ..constants import OCR_ENGINE_RAPID
                self.lang_combo_inline.setVisible(engine != OCR_ENGINE_RAPID)

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
            inline_idx = self.lang_combo_inline.findData(
                self.lang_combo.itemData(self.lang_combo.currentIndex())
            )
            if inline_idx >= 0:
                self.lang_combo_inline.blockSignals(True)
                self.lang_combo_inline.setCurrentIndex(inline_idx)
                self.lang_combo_inline.blockSignals(False)

        self.title_label.setText(self.translate("ocr_popup_title"))
        self._refresh_labels()
        self.text_edit.setPlainText(text)
        self._fit_text_edit_to_content()
        self.resize(self.width(), self.sizeHint().height())
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

    def _on_engine_tab_clicked(self):
        engine = self.sender().property("engine") if self.sender() else None
        if engine and not self._is_refreshing:
            idx = self.engine_combo.findData(engine)
            if idx >= 0:
                self.engine_combo.setCurrentIndex(idx)

    def _update_engine_tab_selection(self):
        current = self.engine_combo.currentData()
        self.engine_tab_rapid.setProperty("selected", current == "rapidocr")
        self.engine_tab_rapid.style().unpolish(self.engine_tab_rapid)
        self.engine_tab_rapid.style().polish(self.engine_tab_rapid)
        self.engine_tab_windows.setProperty("selected", current == "windows")
        self.engine_tab_windows.style().unpolish(self.engine_tab_windows)
        self.engine_tab_windows.style().polish(self.engine_tab_windows)

    def _on_text_changed(self):
        QtCore.QTimer.singleShot(0, self._fit_text_edit_to_content)

    def _fit_text_edit_to_content(self):
        # Use block count and font metrics for reliable height estimation
        fm = self.text_edit.fontMetrics()
        line_height = fm.lineSpacing()
        document_height = self.text_edit.document().blockCount() * line_height
        
        frame = self.text_edit.frameWidth() * 2
        # Style has padding: 12px 10px 12px 10px
        v_padding = 24
        min_height = 120
        max_height = 500
        target_height = int(document_height + frame + v_padding + 4)
        self.text_edit.setFixedHeight(max(min_height, min(target_height, max_height)))
        # Also adjust window height to fit
        self.resize(self.width(), self.sizeHint().height())

    @staticmethod
    def _make_copy_icon():
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        
        # Color: #ffffffb0
        color = QtGui.QColor(255, 255, 255, 176)
        pen = QtGui.QPen(color, 2)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        # Path 1: Main rectangle
        path = QtGui.QPainterPath()
        # M 7 9.667 a 2.667 2.667 0 0 1 2.667 -2.667 h 8.666 a 2.667 2.667 0 0 1 2.667 2.667 v 8.666 a 2.667 2.667 0 0 1 -2.667 2.667 h -8.666 a 2.667 2.667 0 0 1 -2.667 -2.667 l 0 -8.666
        path.moveTo(7, 9.667)
        path.arcTo(QtCore.QRectF(7, 7, 5.334, 5.334), 180, 90)
        path.lineTo(18.333, 7)
        path.arcTo(QtCore.QRectF(15.666, 7, 5.334, 5.334), 90, -90)
        path.lineTo(21, 18.333)
        path.arcTo(QtCore.QRectF(15.666, 15.666, 5.334, 5.334), 0, -90)
        path.lineTo(9.667, 21)
        path.arcTo(QtCore.QRectF(7, 15.666, 5.334, 5.334), 270, -90)
        path.lineTo(7, 9.667)
        painter.drawPath(path)

        # Path 2: Back sheet
        # M 4.012 16.737 a 2.005 2.005 0 0 1 -1.012 -1.737 v -10 c 0 -1.1 .9 -2 2 -2 h 10 c .75 0 1.158 .385 1.5 1
        painter.drawPolyline([
            QtCore.QPointF(15.5, 4),
            QtCore.QPointF(15, 3),
            QtCore.QPointF(5, 3),
            QtCore.QPointF(3, 3),
            QtCore.QPointF(3, 5),
            QtCore.QPointF(3, 15),
            QtCore.QPointF(4, 16.7)
        ])
        
        painter.end()
        return QtGui.QIcon(pixmap)

    @staticmethod
    def _make_recapture_icon():
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        color = QtGui.QColor(255, 255, 255, 176) # #ffffffb0
        pen = QtGui.QPen(color, 2)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        # M 7 19 a 2 2 0 0 1 -2 -2
        painter.drawArc(QtCore.QRectF(5, 15, 4, 4), 90 * 16, 90 * 16)
        # M 5 13 v -2
        painter.drawLine(5, 13, 5, 11)
        # M 5 7 a 2 2 0 0 1 2 -2
        painter.drawArc(QtCore.QRectF(5, 5, 4, 4), 180 * 16, -90 * 16)
        # M 11 5 h 2
        painter.drawLine(11, 5, 13, 5)
        # M 17 5 a 2 2 0 0 1 2 2
        painter.drawArc(QtCore.QRectF(15, 5, 4, 4), 90 * 16, -90 * 16)
        # M 19 11 v 2
        painter.drawLine(19, 11, 19, 13)
        # M 19 17 v 4
        painter.drawLine(19, 17, 19, 21)
        # M 21 19 h -4
        painter.drawLine(21, 19, 17, 19)
        # M 13 19 h -2
        painter.drawLine(13, 19, 11, 19)

        painter.end()
        return QtGui.QIcon(pixmap)

    def _sync_lang_inline_to_combo(self, index):
        if self._is_refreshing:
            return
        lang_data = self.lang_combo_inline.itemData(index)
        if lang_data:
            idx = self.lang_combo.findData(lang_data)
            if idx >= 0:
                self.lang_combo.blockSignals(True)
                self.lang_combo.setCurrentIndex(idx)
                self.lang_combo.blockSignals(False)
                self.language_changed.emit(lang_data)

    def _on_copy_clicked(self):
        self.copy_text()
        self.copy_btn.setProperty("copied", True)
        self.copy_btn.style().unpolish(self.copy_btn)
        self.copy_btn.style().polish(self.copy_btn)
        QtCore.QTimer.singleShot(1500, self._restore_copy_button)

    def _restore_copy_button(self):
        self.copy_btn.setProperty("copied", False)
        self.copy_btn.style().unpolish(self.copy_btn)
        self.copy_btn.style().polish(self.copy_btn)

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
