"""
Floating OCR text popup widget.
"""

from PyQt6 import QtCore, QtWidgets


class OcrPopup(QtWidgets.QWidget):
    """Semi-transparent floating popup for recognized OCR text."""
    language_changed = QtCore.pyqtSignal(str)

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

        # Language Selector
        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.setObjectName("ocrLangCombo")
        self.lang_combo.addItem("Lang: EN", "en-US")
        self.lang_combo.addItem("Lang: ZH", "zh-CN")
        self.lang_combo.setToolTip("Select OCR Language")
        self.lang_combo.setFixedWidth(85)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed_idx)
        header.addWidget(self.lang_combo)

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
            " background-color: rgba(18, 22, 28, 228);"
            " border: 1px solid rgba(255, 255, 255, 42);"
            " border-radius: 14px;"
            "}"
            "#ocrTitle {"
            " color: #F1F5F9;"
            " font-size: 18px;"
            " font-weight: 600;"
            "}"
            "#ocrLangCombo {"
            " background: rgba(255, 255, 255, 20);"
            " border: 1px solid rgba(255, 255, 255, 30);"
            " border-radius: 6px;"
            " color: #F1F5F9;"
            " padding: 2px 5px;"
            "}"
            "#ocrCopyBtn {"
            " color: #CFD8E3;"
            " border: none;"
            " border-radius: 8px;"
            " background: rgba(255, 255, 255, 20);"
            " padding: 0 10px;"
            "}"
            "#ocrCopyBtn:hover {"
            " background: rgba(255, 255, 255, 34);"
            "}"
            "#ocrText {"
            " background: rgba(255, 255, 255, 12);"
            " border: 1px solid rgba(255, 255, 255, 20);"
            " border-radius: 10px;"
            " color: #F8FAFC;"
            " font-size: 16px;"
            " padding: 10px;"
            "}"
            "#ocrCloseBtn {"
            " color: #CFD8E3;"
            " border: none;"
            " border-radius: 12px;"
            " background: rgba(255, 255, 255, 20);"
            "}"
            "#ocrCloseBtn:hover {"
            " background: rgba(255, 255, 255, 34);"
            "}"
        )

    def _on_lang_changed_idx(self, index):
        if not self._is_refreshing:
            lang_data = self.lang_combo.itemData(index)
            if lang_data:
                self.language_changed.emit(lang_data)

    def show_text(self, text, pixmap=None, lang=None):
        """Display OCR text and show popup near bottom-right corner."""
        self._is_refreshing = True
        if pixmap is not None:
            self._last_pixmap = pixmap
        if lang:
            idx = self.lang_combo.findData(lang)
            if idx >= 0:
                self.lang_combo.setCurrentIndex(idx)
        self.title_label.setText(self.translate("ocr_popup_title"))
        self.copy_btn.setText(self.translate("ocr_copy_btn"))
        self.text_edit.setPlainText(text)
        self._is_refreshing = False
        
        if not self.isVisible():
            self._place_bottom_right()
            self.show()
        
        self.raise_()
        self.activateWindow()

    @property
    def last_pixmap(self):
        return self._last_pixmap

    def copy_text(self):
        """Copy the current text editor content to the system clipboard."""
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.text_edit.toPlainText())

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
