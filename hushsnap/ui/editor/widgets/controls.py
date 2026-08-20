from PyQt6 import QtCore, QtGui, QtWidgets
from ..constants import (
    _SWATCH_COLORS, _SWATCH_COLS, _SWATCH_SIZE, _SWATCH_PAD, _SWATCH_GAP
)
from ...styles import BRAND_GREEN


# Curated font list for the text tool. A plain QComboBox (not QFontComboBox)
# so we control exactly which families are shown - the full 300+ system list is
# noise for a screenshot annotation tool. Each item is rendered in its own
# family via Qt.FontRole, so the popup still gives a live preview per row.
#
# Win10/11 constants (always present, not probed): Segoe UI, Consolas.
# Probed families: Arial, Times New Roman, and the three CJK UI fonts. The CJK
# three are listed together (not gated by UI language) - a machine with
# multiple language packs may have several installed, and the user may want to
# annotate in a script other than their UI language. "命中即显示"。
_CURATED_ALWAYS = ["Segoe UI", "Consolas"]
_CURATED_PROBED = ["Arial", "Times New Roman", "Microsoft YaHei",
                   "Microsoft JhengHei", "Yu Gothic UI"]


class _CuratedFontComboBox(QtWidgets.QComboBox):
    """Font picker showing a small curated list of installed families.

    A plain QComboBox (not QFontComboBox) so we control exactly which families
    are shown - the full 300+ system list is noise for a screenshot annotation
    tool. Each item is rendered in its own family via Qt.FontRole, so the popup
    still gives a live preview per row. The set is intentionally fixed and
    small: if a family isn't listed, it isn't selectable - the curated choice
    trades exhaustive freedom for a glanceable list, which suits the
    quick-annotation use case.
    """

    def __init__(self, parent: QtWidgets.QWidget = None):
        super().__init__(parent)
        self._populate()

    def _add_family(self, family: str) -> None:
        """Append a family item rendered in its own font via FontRole."""
        self.addItem(family)
        i = self.count() - 1
        f = QtGui.QFont(family)
        f.setPointSize(11)
        self.setItemData(i, f, QtCore.Qt.ItemDataRole.FontRole)

    def _populate(self) -> None:
        self.clear()
        installed = set(QtGui.QFontDatabase.families())
        for fam in _CURATED_ALWAYS:
            self._add_family(fam)
        for fam in _CURATED_PROBED:
            if fam in installed:
                self._add_family(fam)

    def showPopup(self) -> None:
        popup = self.view().window()
        if popup:
            popup.setWindowFlags(
                QtCore.Qt.WindowType.Popup
                | QtCore.Qt.WindowType.FramelessWindowHint,
            )
            popup.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
        super().showPopup()

class _ColorButton(QtWidgets.QWidget):
    """Custom circular color selection button."""

    clicked = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._color = QtGui.QColor("#FFFFFF")
        self._hovered = False
        self._pressed = False

    def setColor(self, color: QtGui.QColor) -> None:
        self._color = color
        self.update()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = False
        self._pressed = False
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self.update()
        if was_pressed and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        inset = 2.0 if self._pressed else 1.0
        rect = QtCore.QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)

        display = QtGui.QColor(self._color)
        display.setAlpha(255)
        painter.setBrush(QtGui.QBrush(display))

        if self._pressed:
            pen = QtGui.QPen(QtGui.QColor(BRAND_GREEN), 2.5)
        elif self._hovered:
            pen = QtGui.QPen(QtGui.QColor(BRAND_GREEN), 2.0)
        else:
            pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 102), 2.0)

        painter.setPen(pen)
        painter.drawEllipse(rect)
        painter.end()

class _SwatchPopup(QtWidgets.QFrame):
    """Lightweight color swatch grid popup."""

    color_selected = QtCore.pyqtSignal(QtGui.QColor)

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setWindowFlags(
            QtCore.Qt.WindowType.Popup
            | QtCore.Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        # Opaque card: rounded corners are drawn by the OS via DWM on
        # Windows 11 (round_window_corners_via_dwm).  A translucent popup with
        # QSS border-radius composites as black corners on systems without DWM
        # composition (VMs, RDP, basic theme), so we stay opaque and let the OS
        # round the corners.
        self.setStyleSheet("_SwatchPopup { background: #333; border: 1px solid #555; }")

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(_SWATCH_PAD + 2, _SWATCH_PAD + 2, _SWATCH_PAD + 2, _SWATCH_PAD + 2)
        grid.setSpacing(_SWATCH_GAP)

        for i, (hex_color, tooltip) in enumerate(_SWATCH_COLORS):
            btn = QtWidgets.QPushButton()
            btn.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tooltip)
            r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
            border = "1px solid rgba(255,255,255,30)" if hex_color == "#FFFFFF" else "none"
            btn.setStyleSheet(
                f"QPushButton {{"
                f"  background-color: rgb({r},{g},{b});"
                f"  border: {border}; border-radius: {_SWATCH_SIZE // 2}px;"
                f"}}"
                f"QPushButton:hover {{ border: 2px solid #fff; }}"
                f"QPushButton:pressed {{ border: 2px solid {BRAND_GREEN}; }}"
            )
            btn.clicked.connect(lambda checked, c=QtGui.QColor(hex_color): self._on_pick(c))
            row, col = divmod(i, _SWATCH_COLS)
            grid.addWidget(btn, row, col)

        self.adjustSize()

    def _on_pick(self, color: QtGui.QColor) -> None:
        self.color_selected.emit(color)
        self.close()

    def show_near(self, anchor: QtWidgets.QWidget) -> None:
        """Position popup below (or above) the anchor widget."""
        pos = anchor.mapToGlobal(QtCore.QPoint(0, anchor.height() + 3))
        screen = QtWidgets.QApplication.screenAt(pos)
        if screen:
            sg = screen.availableGeometry()
            if pos.y() + self.height() > sg.bottom():
                pos = anchor.mapToGlobal(QtCore.QPoint(0, -self.height() - 3))
            if pos.x() + self.width() > sg.right():
                pos.setX(sg.right() - self.width() - 4)
        self.move(pos)
        from ...styles import round_window_corners_via_dwm
        round_window_corners_via_dwm(self)
        self.show()
