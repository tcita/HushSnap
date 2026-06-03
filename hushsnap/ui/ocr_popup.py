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

        self.resize(560, 360)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1) # Thin window border

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

        # Header bar container
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

        # Hidden engine combo
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
        self.text_edit.setCursor(QtCore.Qt.CursorShape.IBeamCursor)
        self.text_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(self.text_edit)

        # Copy button
        self.copy_btn = QtWidgets.QPushButton()
        self.copy_btn.setObjectName("ocrCopyBtn")
        self.copy_btn.setFixedSize(26, 22)
        self.copy_btn.setIconSize(QtCore.QSize(14, 14))
        self.copy_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setParent(self.text_edit)
        self.copy_btn.clicked.connect(self._on_copy_clicked)
        self.copy_btn.hide()
        self.text_edit.installEventFilter(self)

        # Status bar
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

        # Set Placeholder and Caret/Placeholder colors via Palette
        self.text_edit.setPlaceholderText(self.translate("ocr_result_placeholder") if hasattr(self, "translate") else "OCR 结果将显示在此，可直接编辑…")
        pal = self.text_edit.palette()
        pal.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#5fc98a")) # Caret color
        pal.setColor(QtGui.QPalette.ColorRole.PlaceholderText, QtGui.QColor("#2e7d4f")) # Placeholder color
        self.text_edit.setPalette(pal)

        self.setStyleSheet(
            "/* Midnight Forest v2 Theme - Terminal Style */"
            "/* Outer Shell: #0a1910, Header: #0d1f17, Content: #12261b, Text: #d4f5e2, Highlight: #5fc98a */"
            
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
            "#ocrText {"
            " background-color: #12261b;"
            " color: #d4f5e2;"
            " border: none;"
            " border-radius: 0;"
            " padding: 12px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
            "#ocrStatusBar {"
            " background-color: #12261b;"
            " border-top: 1px solid rgba(94, 201, 138, 0.1);"
            " border-bottom-left-radius: 8px;"
            " border-bottom-right-radius: 8px;"
            "}"
            "#ocrCopyBtn {"
            " color: #5fc98a;"
            " border: 1px solid #1e4a30;"
            " border-radius: 6px;"
            " background: #1e4a30;"
            " padding: 0;"
            " font-size: 12px;"
            "}"
            "#ocrCopyBtn:hover { background: #2e7d4f; border-color: #2e7d4f; }"
            "#ocrCopyBtn[copied=\"true\"] { background: #5fc98a; color: #0a1910; }"
            
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
        self.apply_font_size()
        self._refresh_labels()

    def paintEvent(self, event):
        """Enable stylesheet support."""
        opt = QtWidgets.QStyleOption()
        opt.initFrom(self)
        p = QtGui.QPainter(self)
        self.style().drawPrimitive(QtWidgets.QStyle.PrimitiveElement.PE_Widget, opt, p, self)

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
        self.pin_btn.setIcon(self._make_pin_icon(self._pinned))
        self.pin_btn.setIconSize(QtCore.QSize(16, 16))

        for combo in (self.lang_combo, self.lang_combo_inline):
            for code, key in [("en-US", "ocr_lang_english"), ("zh-CN", "ocr_lang_chinese_simplified"), ("zh-TW", "ocr_lang_chinese_traditional")]:
                idx = combo.findData(code)
                if idx >= 0: combo.setItemText(idx, self.translate(key))

        self.lang_combo_inline.setToolTip(self.translate("ocr_lang_selector_tooltip"))
        self.copy_btn.setToolTip(self.translate("ocr_copy_btn"))
        self.pin_btn.setToolTip(self.translate("ocr_pin_btn"))
        self.close_btn.setToolTip(self.translate("close_btn"))

    def _on_lang_changed_idx(self, index):
        if not self._is_refreshing:
            lang_data = self.lang_combo.itemData(index)
            if lang_data:
                # Sync back to inline combo if needed
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
        if target: self.switch_language_requested.emit(str(target))

    def show_text(self, text, pixmap=None, lang=None, engine=None):
        self._is_refreshing = True
        if pixmap is not None: self._last_pixmap = pixmap
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
                if lowered.startswith(("zh-tw", "zh-hk", "zh-mo", "zh-hant")): idx = self.lang_combo.findData("zh-TW")
                elif lowered.startswith("zh"): idx = self.lang_combo.findData("zh-CN")
            if idx >= 0:
                self.lang_combo.setCurrentIndex(idx)
                inline_idx = self.lang_combo_inline.findData(self.lang_combo.itemData(idx))
                if inline_idx >= 0:
                    self.lang_combo_inline.blockSignals(True)
                    self.lang_combo_inline.setCurrentIndex(inline_idx)
                    self.lang_combo_inline.blockSignals(False)

        self._refresh_labels()
        self.apply_font_size()
        self.text_edit.setPlainText(text)
        self._is_refreshing = False
        if not self.isVisible(): self._place_near_cursor()
        self.show()
        self.raise_()
        self.activateWindow()
        self.copy_btn.show()
        self._position_copy_button()

    def show_language_notice(self, message, available_lang=""):
        self.notice_label.setText(message)
        self.notice_switch_btn.setText(self.translate("ocr_lang_missing_switch_btn", available_lang=available_lang or self.translate("ocr_lang_installed_fallback")))
        self.notice_switch_btn.setProperty("target_lang", available_lang)
        self.notice_switch_btn.setEnabled(bool(available_lang))
        self.notice_settings_btn.setText(self.translate("ocr_lang_missing_open_settings_btn"))
        self.notice_frame.show()

    def hide_language_notice(self):
        self.notice_frame.hide()

    @property
    def last_pixmap(self): return self._last_pixmap

    def copy_text(self):
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard: clipboard.setText(self.text_edit.toPlainText())

    def set_engine(self, engine):
        if self._is_refreshing: return
        idx = self.engine_combo.findData(engine)
        if idx >= 0: self.engine_combo.setCurrentIndex(idx)

    def set_pinned(self, pinned):
        if bool(pinned) == bool(self._pinned): return
        self.pin_btn.blockSignals(True)
        self.pin_btn.setChecked(bool(pinned))
        self.pin_btn.blockSignals(False)
        self._on_pin_toggled(bool(pinned))

    def apply_font_size(self):
        font = self.text_edit.font()
        font.setPointSizeF(get_ocr_font_size() * 0.75)
        self.text_edit.setFont(font)

    @staticmethod
    def _make_copy_icon():
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pixmap)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(QtGui.QPen(QtGui.QColor("#5fc98a"), 2, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin))
        p.drawRect(7, 7, 10, 10)
        p.drawPolyline([QtCore.QPointF(14, 4), QtCore.QPointF(4, 4), QtCore.QPointF(4, 14)])
        p.end()
        return QtGui.QIcon(pixmap)

    def _make_pin_icon(self, checked=False):
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pixmap)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(QtGui.QPen(QtGui.QColor("#5fc98a"), 2, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin))
        if not checked:
            p.translate(12, 12); p.rotate(-45); p.translate(-12, -12)
        # Simplified Lucide Pin Path for 24x24
        path = QtGui.QPainterPath()
        path.moveTo(12, 17); path.lineTo(12, 22) # Needle
        path.moveTo(9, 11); path.lineTo(6, 14); path.lineTo(6, 16); path.lineTo(18, 16); path.lineTo(18, 14); path.lineTo(15, 11); path.lineTo(15, 6); path.lineTo(9, 6); path.closeSubpath()
        path.addEllipse(QtCore.QRectF(8, 2, 8, 4))
        p.drawPath(path); p.end()
        return QtGui.QIcon(pixmap)

    def _on_pin_toggled(self, checked):
        self._pinned = checked
        self.pin_btn.setProperty("pin", checked)
        self.pin_btn.setIcon(self._make_pin_icon(checked))
        self.pin_btn.style().unpolish(self.pin_btn); self.pin_btn.style().polish(self.pin_btn)
        self.pin_btn.setToolTip(self.translate("ocr_unpin_btn" if checked else "ocr_pin_btn"))
        self.pin_toggled.emit(checked)

    def _on_copy_clicked(self):
        self.copy_text()
        self.copy_btn.setProperty("copied", True)
        self.copy_btn.style().unpolish(self.copy_btn); self.copy_btn.style().polish(self.copy_btn)
        QtCore.QTimer.singleShot(1200, lambda: [self.copy_btn.setProperty("copied", False), self.copy_btn.style().unpolish(self.copy_btn), self.copy_btn.style().polish(self.copy_btn)])

    def eventFilter(self, obj, event):
        if obj is self.text_edit and event.type() == QtCore.QEvent.Type.Resize: self._position_copy_button()
        return super().eventFilter(obj, event)

    def _position_copy_button(self):
        self.copy_btn.move(self.text_edit.width() - self.copy_btn.width() - 8, self.text_edit.height() - self.copy_btn.height() - 8)

    def _get_edge(self, pos):
        edge = QtCore.Qt.Edge(0); hit = 8; w, h = self.width(), self.height()
        if pos.x() <= hit: edge |= QtCore.Qt.Edge.LeftEdge
        elif pos.x() >= w - hit: edge |= QtCore.Qt.Edge.RightEdge
        if pos.y() <= hit: edge |= QtCore.Qt.Edge.TopEdge
        elif pos.y() >= h - hit: edge |= QtCore.Qt.Edge.BottomEdge
        return edge

    def _update_cursor(self, edge):
        if edge in (QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.TopEdge, QtCore.Qt.Edge.RightEdge | QtCore.Qt.Edge.BottomEdge): self.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor)
        elif edge in (QtCore.Qt.Edge.RightEdge | QtCore.Qt.Edge.TopEdge, QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.BottomEdge): self.setCursor(QtCore.Qt.CursorShape.SizeBDiagCursor)
        elif edge & (QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.RightEdge): self.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
        elif edge & (QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.BottomEdge): self.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
        else: self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, event): self.setCursor(QtCore.Qt.CursorShape.ArrowCursor); super().leaveEvent(event)

    def event(self, event):
        if event.type() == QtCore.QEvent.Type.HoverMove and QtWidgets.QApplication.mouseButtons() == QtCore.Qt.MouseButton.NoButton: self._update_cursor(self._get_edge(event.position().toPoint()))
        return super().event(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            edge = self._get_edge(event.position().toPoint())
            if edge and self.windowHandle(): self.windowHandle().startSystemResize(edge)
            else: self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == QtCore.Qt.MouseButton.LeftButton and self._drag_pos: self.move(event.globalPosition().toPoint() - self._drag_pos); event.accept()

    def mouseReleaseEvent(self, event): self._drag_pos = None; event.accept()

    def _place_near_cursor(self):
        screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos()) or QtWidgets.QApplication.primaryScreen()
        if not screen: return
        area = screen.availableGeometry(); cursor = QtGui.QCursor.pos(); gap = 20
        self.move(min(max(cursor.x() + gap, area.left()), area.right() - self.width()), min(max(cursor.y() + gap, area.top()), area.bottom() - self.height()))

    def showEvent(self, event):
        super().showEvent(event)
        if self.windowHandle() and not self._app_icon.isNull(): self.windowHandle().setIcon(self._app_icon)

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.Type.ActivationChange and not self._pinned and not self.isActiveWindow(): self.hide()
        super().changeEvent(event)

    def hideEvent(self, event): self._last_pixmap = None; super().hideEvent(event)
