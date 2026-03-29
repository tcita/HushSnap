"""
Floating OCR text popup widget.
"""

from PyQt6 import QtCore, QtWidgets


class OcrPopup(QtWidgets.QWidget):
    """Semi-transparent floating popup for recognized OCR text."""

    def __init__(self, translate, parent=None):
        super().__init__(parent)
        self.translate = translate
        self._drag_pos = None

        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self.resize(560, 320)

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

        close_btn = QtWidgets.QPushButton("x")
        close_btn.setObjectName("ocrCloseBtn")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.hide)
        header.addWidget(close_btn)
        layout.addLayout(header)

        self.text_edit = QtWidgets.QPlainTextEdit()
        self.text_edit.setReadOnly(True)
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
            " background-color: rgba(18, 22, 28, 218);"
            " border: 1px solid rgba(255, 255, 255, 42);"
            " border-radius: 14px;"
            "}"
            "#ocrTitle {"
            " color: #F1F5F9;"
            " font-size: 20px;"
            " font-weight: 600;"
            "}"
            "#ocrText {"
            " background: rgba(255, 255, 255, 16);"
            " border: 1px solid rgba(255, 255, 255, 20);"
            " border-radius: 10px;"
            " color: #F8FAFC;"
            " font-size: 17px;"
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

    def show_text(self, text):
        """Display OCR text and show popup near bottom-right corner."""
        self.title_label.setText(self.translate("ocr_popup_title"))
        self.text_edit.setPlainText(text)
        self._place_bottom_right()
        self.show()
        self.raise_()
        self.activateWindow()

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
