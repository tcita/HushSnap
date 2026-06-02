"""
Floating OCR text popup widget.
"""

from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import get_ocr_font_size, get_resource_dir
from ..constants import APP_ICON_FILENAME


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

        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._app_icon = QtGui.QIcon(str(get_resource_dir() / APP_ICON_FILENAME))

        self.resize(560, 360)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        panel = QtWidgets.QFrame()
        panel.setObjectName("ocrPanel")
        panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        outer.addWidget(panel, 1)

        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        header.setContentsMargins(0, 0, 0, 0)

        self.title_label = QtWidgets.QLabel(self.translate("ocr_popup_title"))
        self.title_label.setObjectName("ocrTitle")
        header.addWidget(self.title_label)

        header.addStretch(1)

        self.pin_btn = QtWidgets.QPushButton("📌")
        self.pin_btn.setObjectName("ocrPinBtn")
        self.pin_btn.setFixedSize(28, 24)
        self.pin_btn.setCheckable(True)
        self.pin_btn.clicked.connect(self._on_pin_toggled)
        header.addWidget(self.pin_btn)

        self.close_btn = QtWidgets.QPushButton("✕")
        self.close_btn.setObjectName("ocrCloseBtn")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.clicked.connect(self.hide)
        header.addWidget(self.close_btn)
        layout.addLayout(header)

        # Hidden engine combo — holds engine state, driven by settings dialog
        self.engine_combo = QtWidgets.QComboBox()

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
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.text_edit.setContentsMargins(0, 0, 0, 0)
        self.text_edit.setViewportMargins(0, 0, 0, 0)
        self.text_edit.document().setDocumentMargin(0)
        self.text_edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self.text_edit)

        # Copy button overlaid inside the text area (bottom-right corner)
        self.copy_btn = QtWidgets.QPushButton()
        self.copy_btn.setObjectName("ocrCopyBtn")
        self.copy_btn.setFixedSize(26, 22)
        self.copy_btn.setIconSize(QtCore.QSize(14, 14))
        self.copy_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setParent(self.text_edit)
        self.copy_btn.clicked.connect(self._on_copy_clicked)
        self.copy_btn.hide()
        self.text_edit.installEventFilter(self)

        # Status bar: language selector + resize grip
        self.status_bar = QtWidgets.QFrame()
        self.status_bar.setObjectName("ocrStatusBar")
        status_layout = QtWidgets.QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(10, 4, 4, 4)
        status_layout.setSpacing(6)

        self.editable_badge = QtWidgets.QLabel("")
        self.editable_badge.setObjectName("ocrEditableBadge")
        status_layout.addWidget(self.editable_badge)

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
            " background-color: rgba(8, 44, 28, 235);"
            " border: 1px solid rgba(170, 220, 185, 64);"
            " border-radius: 14px;"
            "}"
            "#ocrTitle {"
            " color: #F3FFF6;"
            " font-size: 18px;"
            " font-weight: 600;"
            "}"
            "#ocrEditableBadge {"
            " color: rgba(220, 240, 225, 160);"
            " background: rgba(190, 255, 212, 18);"
            " border-radius: 8px;"
            " padding: 2px 7px;"
            " font-size: 10px;"
            " font-weight: 500;"
            " margin-left: 4px;"
            "}"
            "#ocrCopyBtn {"
            " color: rgba(220, 245, 225, 180);"
            " border: 1px solid rgba(100, 200, 130, 40);"
            " border-radius: 6px;"
            " background: rgba(10, 55, 30, 210);"
            " padding: 0;"
            " font-size: 12px;"
            "}"
            "#ocrCopyBtn:hover {"
            " background: rgba(30, 80, 50, 230);"
            " border-color: rgba(120, 220, 155, 80);"
            "}"
            "#ocrCopyBtn[copied=\"true\"] {"
            " background: rgba(80, 200, 120, 45);"
            " color: #B0FFC0;"
            "}"
            "#ocrPinBtn {"
            " color: rgba(225, 247, 231, 80);"
            " border: none;"
            " border-radius: 12px;"
            " background: transparent;"
            " font-size: 15px;"
            "}"
            "#ocrPinBtn:hover {"
            " background: rgba(190, 255, 212, 25);"
            " color: #E1F7E7;"
            "}"
            "#ocrPinBtn[pin=\"true\"] {"
            " color: #FFD966;"
            " background: rgba(255, 220, 100, 28);"
            "}"
            "#ocrPinBtn[pin=\"true\"]:hover {"
            " color: #FFE8A0;"
            " background: rgba(255, 220, 100, 42);"
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
            " background: rgba(3, 25, 16, 220);"
            " border: 1px solid rgba(190, 255, 212, 60);"
            " border-radius: 10px;"
            " color: #F5FFF7;"
            " padding: 10px 10px 10px 10px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
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
            " background: rgba(8, 44, 28, 235);"
            " border-top: 1px solid rgba(170, 220, 185, 40);"
            " border-radius: 0 0 14px 14px;"
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
        self.apply_font_size()
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
        self.copy_btn.setIcon(self._make_copy_icon())

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
        self.copy_btn.setToolTip(self.translate("ocr_copy_btn"))
        self.pin_btn.setToolTip(self.translate("ocr_pin_btn"))
        self.pin_btn.setAccessibleName(self.translate("ocr_pin_btn"))
        self.close_btn.setToolTip(self.translate("close_btn"))
        self.close_btn.setAccessibleName(self.translate("close_btn"))
        self.editable_badge.setText(self.translate("ocr_editable_hint"))

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
        self.apply_font_size()
        self.text_edit.setPlainText(text)
        self._is_refreshing = False

        if not self.isVisible():
            self._place_near_cursor()

        self.show()
        self.raise_()
        self.activateWindow()
        self.copy_btn.show()
        self.copy_btn.raise_()
        self._position_copy_button()

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

    def set_engine(self, engine):
        """Update the hidden engine combo (called from settings)."""
        if self._is_refreshing:
            return
        idx = self.engine_combo.findData(engine)
        if idx >= 0 and idx != self.engine_combo.currentIndex():
            self.engine_combo.setCurrentIndex(idx)

    def apply_font_size(self):
        font_size = get_ocr_font_size()
        font = self.text_edit.font()
        font.setPointSizeF(font_size * 0.75)
        self.text_edit.setFont(font)
        doc = self.text_edit.document()
        doc.setDefaultFont(font)

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

    def _on_pin_toggled(self, checked):
        """Toggle pinned state: when pinned the window ignores focus loss."""
        self._pinned = checked
        self.pin_btn.setProperty("pin", checked)
        self.pin_btn.style().unpolish(self.pin_btn)
        self.pin_btn.style().polish(self.pin_btn)
        # Update tooltip based on state
        key = "ocr_unpin_btn" if checked else "ocr_pin_btn"
        self.pin_btn.setToolTip(self.translate(key))
        self.pin_toggled.emit(checked)

    def set_pinned(self, pinned):
        """Programmatically set pin state (e.g. loaded from config)."""
        pinned = bool(pinned)
        if pinned == bool(self._pinned):
            return
        self.pin_btn.blockSignals(True)
        self.pin_btn.setChecked(pinned)
        self.pin_btn.blockSignals(False)
        self._on_pin_toggled(pinned)

    def _on_copy_clicked(self):
        self.copy_text()
        self.copy_btn.setProperty("copied", True)
        self.copy_btn.style().unpolish(self.copy_btn)
        self.copy_btn.style().polish(self.copy_btn)
        QtCore.QTimer.singleShot(1200, self._restore_copy_button)

    def _restore_copy_button(self):
        self.copy_btn.setProperty("copied", False)
        self.copy_btn.style().unpolish(self.copy_btn)
        self.copy_btn.style().polish(self.copy_btn)

    def eventFilter(self, obj, event):
        """Reposition the floating copy button when the text edit is resized."""
        if obj is self.text_edit and event.type() == QtCore.QEvent.Type.Resize:
            self._position_copy_button()
        return super().eventFilter(obj, event)

    def _position_copy_button(self):
        """Place the copy button at the bottom-right of the text_edit."""
        m = 8
        self.copy_btn.move(
            self.text_edit.width() - self.copy_btn.width() - m,
            self.text_edit.height() - self.copy_btn.height() - m,
        )

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

    def _place_near_cursor(self):
        """Position the popup at the bottom-right of the mouse cursor, clamped to screen."""
        screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
        if not screen:
            screen = QtWidgets.QApplication.primaryScreen()
        if not screen:
            return

        area = screen.availableGeometry()
        cursor = QtGui.QCursor.pos()
        gap = 20

        x = cursor.x() + gap
        y = cursor.y() + gap

        # Clamp to keep the entire window on screen
        if x + self.width() > area.right():
            x = area.right() - self.width()
        if y + self.height() > area.bottom():
            y = area.bottom() - self.height()
        if x < area.left():
            x = area.left()
        if y < area.top():
            y = area.top()

        self.move(x, y)

    def showEvent(self, event):
        """Set window icon once native handle is ready."""
        super().showEvent(event)
        if self.windowHandle() and not self._app_icon.isNull():
            self.windowHandle().setIcon(self._app_icon)

    def changeEvent(self, event):
        """Hide popup on focus loss unless pinned."""
        if event.type() == QtCore.QEvent.Type.ActivationChange and not self._pinned:
            if not self.isActiveWindow():
                self.hide()
        super().changeEvent(event)

    def hideEvent(self, event):
        """Release cached screenshot pixmap to free RAM while hidden."""
        self._last_pixmap = None
        super().hideEvent(event)

