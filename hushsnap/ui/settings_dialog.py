"""
HushSnap settings dialog module.
Provides UI to view current hotkey and capture/change a new hotkey.
"""

import logging
import asyncio
from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import (
    parse_hotkey,
    update_hotkey_in_config,
    update_ocr_hotkey_in_config,
    get_configured_ui_lang,
    update_ui_lang_in_config,
    get_ocr_font_size,
    update_ocr_font_size,
)
from ..system import startup_manager
from .styles import (
    CAPTURE_CANCEL_BUTTON_STYLE,
    CAPTURE_DIALOG_STYLE,
    CAPTURE_FEEDBACK_STYLE,
    CAPTURE_HINT_STYLE,
    CAPTURE_INPUT_STYLE,
    CAPTURE_SAVE_BUTTON_STYLE,
    COMBOBOX_STYLE,
    DIALOG_STYLE,
    GHOST_BUTTON_STYLE,
    HEADER_BAR_STYLE,
    HEADER_ICON_STYLE,
    HEADER_TITLE_STYLE,
    KBD_PILL_STYLE,
    PLUS_LABEL_STYLE,
    ROW_LABEL_STYLE,
    SECTION_HEADER_STYLE,
    SETTING_CARD_STYLE,
    SETTINGS_CAPTURE_DIALOG_MIN_WIDTH,
    SETTINGS_DIALOG_WIDTH,
    SETTINGS_ERROR_COLOR,
    SETTINGS_LABEL_COLOR,
    STATUS_LABEL_STYLE,
    SUBTITLE_STYLE,
    MESSAGE_BOX_STYLE,
)

logger = logging.getLogger(__name__)


class CheckmarkDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, combo_box, parent=None):
        super().__init__(parent)
        self.combo_box = combo_box

    def paint(self, painter, option, index):
        is_popup_item = isinstance(option.widget, QtWidgets.QAbstractItemView)
        super().paint(painter, option, index)

        if is_popup_item and index.row() == self.combo_box.currentIndex():
            painter.save()
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            rect = option.rect
            checkmark_text = "✓"
            
            font = painter.font()
            font.setBold(True)
            font.setPointSize(11)
            painter.setFont(font)
            
            painter.setPen(QtGui.QColor(SETTINGS_LABEL_COLOR))
            text_rect = rect.adjusted(0, 0, -16, 0)
            painter.drawText(
                text_rect,
                QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
                checkmark_text
            )
            painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 32))
        return size


class SleekComboBox(QtWidgets.QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view().setContentsMargins(0, 4, 0, 4)

    def showPopup(self):
        popup = self.view().window()
        if popup:
            popup.setWindowFlags(QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint)
            popup.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        super().showPopup()


def create_system_icon():
    pixmap = QtGui.QPixmap(20, 20)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    
    pen = QtGui.QPen(QtGui.QColor("#CCCCCC"), 1.5)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    
    painter.drawRoundedRect(2, 3, 16, 11, 1.5, 1.5)
    painter.drawLine(10, 14, 10, 17)
    painter.drawLine(7, 17, 13, 17)
    
    painter.end()
    return QtGui.QIcon(pixmap)


def create_language_icon():
    pixmap = QtGui.QPixmap(20, 20)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    
    pen = QtGui.QPen(QtGui.QColor("#CCCCCC"), 1.5)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    
    painter.drawEllipse(2, 2, 16, 16)
    painter.drawEllipse(6, 2, 8, 16)
    painter.drawLine(2, 10, 18, 10)
    
    painter.end()
    return QtGui.QIcon(pixmap)


def _qt_key_to_hotkey_token(key):
    """Convert Qt key enum value into internal hotkey token text."""
    if QtCore.Qt.Key.Key_A <= key <= QtCore.Qt.Key.Key_Z:
        return chr(key)
    if QtCore.Qt.Key.Key_0 <= key <= QtCore.Qt.Key.Key_9:
        return chr(key)
    if QtCore.Qt.Key.Key_F1 <= key <= QtCore.Qt.Key.Key_F24:
        return f"F{key - QtCore.Qt.Key.Key_F1 + 1}"

    special_map = {
        QtCore.Qt.Key.Key_Escape: "ESC",
        QtCore.Qt.Key.Key_Tab: "TAB",
        QtCore.Qt.Key.Key_Enter: "ENTER",
        QtCore.Qt.Key.Key_Return: "ENTER",
        QtCore.Qt.Key.Key_Space: "SPACE",
        QtCore.Qt.Key.Key_Left: "LEFT",
        QtCore.Qt.Key.Key_Up: "UP",
        QtCore.Qt.Key.Key_Right: "RIGHT",
        QtCore.Qt.Key.Key_Down: "DOWN",
    }
    return special_map.get(key)


def _make_kbd_pill(text):
    """Create a single kbd pill for one key."""
    pill = QtWidgets.QLabel(text)
    pill.setObjectName("kbdPill")
    pill.setStyleSheet(KBD_PILL_STYLE)
    pill.setFixedHeight(26)
    return pill


def _make_plus_label():
    """Create a '+' separator between kbd pills."""
    lbl = QtWidgets.QLabel("+")
    lbl.setObjectName("plusLabel")
    lbl.setStyleSheet(PLUS_LABEL_STYLE)
    return lbl


def _build_kbd_pills_row(hotkey_string):
    """Build a row of individual kbd pills separated by '+' labels.

    Returns (container_widget, pill_labels_list) so the caller can
    update text via setText on each pill later.
    """
    container = QtWidgets.QWidget()
    container.setStyleSheet("background: transparent; border: none;")
    layout = QtWidgets.QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)

    pills = []
    parts = [p.strip() for p in hotkey_string.split("+") if p.strip()]
    for i, part in enumerate(parts):
        if i > 0:
            plus = _make_plus_label()
            layout.addWidget(plus, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        pill = _make_kbd_pill(part)
        pills.append(pill)
        layout.addWidget(pill, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

    return container, pills


def _rebuild_kbd_pills(container, hotkey_string):
    """Clear container layout and rebuild all pills + plus labels from scratch.

    Returns the new list of pill widgets.
    """
    layout = container.layout()
    if layout is None:
        return []
    # Remove all existing widgets from the layout
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    pills = []
    parts = [p.strip() for p in hotkey_string.split("+") if p.strip()]
    for i, part in enumerate(parts):
        if i > 0:
            plus = _make_plus_label()
            layout.addWidget(plus, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        pill = _make_kbd_pill(part)
        pills.append(pill)
        layout.addWidget(pill, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

    return pills


def _make_ghost_button(text, callback=None):
    """Create a minimal ghost button."""
    btn = QtWidgets.QPushButton(text)
    btn.setObjectName("ghostButton")
    btn.setStyleSheet(GHOST_BUTTON_STYLE)
    btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    if callback:
        btn.clicked.connect(callback)
    btn.setFixedHeight(28)
    return btn


class SleekSwitch(QtWidgets.QAbstractButton):
    def __init__(self, parent=None, track_radius=12, thumb_radius=9):
        super().__init__(parent)
        self.setCheckable(True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
        self._track_radius = track_radius
        self._thumb_radius = thumb_radius
        self._margin = (self._track_radius * 2 - self._thumb_radius * 2) // 2
        self._base_offset = self._margin
        self._offset = self._base_offset
        self._color_off = QtGui.QColor("#D5D5D5")
        self._color_on = QtGui.QColor("#4CD964")
        self._thumb_color = QtGui.QColor("#FFFFFF")
        self._animation = QtCore.QVariantAnimation(
            self,
            startValue=self._base_offset,
            endValue=self._track_radius * 2 - self._thumb_radius * 2 + self._base_offset,
            duration=120,
            valueChanged=self._update_offset,
        )

    def _update_offset(self, value):
        self._offset = value
        self.update()

    def sizeHint(self):
        return QtCore.QSize(self._track_radius * 4, self._track_radius * 2)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        
        # Draw track
        track_color = self._color_on if self.isChecked() else self._color_off
        painter.setBrush(track_color)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), self._track_radius, self._track_radius)
        
        # Draw thumb
        painter.setBrush(self._thumb_color)
        painter.drawEllipse(QtCore.QPointF(self._offset + self._thumb_radius, self.height() // 2), self._thumb_radius, self._thumb_radius)

    def nextCheckState(self):
        super().nextCheckState()
        self._animation.setDirection(
            QtCore.QVariantAnimation.Direction.Forward if self.isChecked() else QtCore.QVariantAnimation.Direction.Backward
        )
        self._animation.start()


class FontSizeStepper(QtWidgets.QWidget):
    """Compact − / value / + stepper for font size selection."""
    value_changed = QtCore.pyqtSignal(int)

    STEPS = [12, 14, 16, 18, 20, 22, 24]
    _btn_style = (
        "QPushButton {"
        " background: transparent;"
        " border: 0.5px solid #D5D5D5;"
        " border-radius: 4px;"
        " color: #555;"
        " font-size: 13px;"
        " font-weight: 600;"
        " min-width: 26px;"
        " max-width: 26px;"
        " min-height: 26px;"
        " max-height: 26px;"
        " padding: 0;"
        "}"
        "QPushButton:hover { background: #F0F0F0; border-color: #BBB; }"
        "QPushButton:disabled { color: #CCC; border-color: #E8E8E8; }"
    )

    def __init__(self, initial_value=16, parent=None):
        super().__init__(parent)
        self._current = initial_value
        if self._current not in self.STEPS:
            self._current = 16

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._minus_btn = QtWidgets.QPushButton("−")
        self._minus_btn.setStyleSheet(self._btn_style)
        self._minus_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._minus_btn.clicked.connect(self._decrement)

        self._label = QtWidgets.QLabel(f"{self._current} px")
        self._label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            "color: #333; font-size: 13px; font-weight: 500;"
            " border: none; background: transparent;"
            " min-width: 44px;"
        )

        self._plus_btn = QtWidgets.QPushButton("+")
        self._plus_btn.setStyleSheet(self._btn_style)
        self._plus_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._plus_btn.clicked.connect(self._increment)

        layout.addWidget(self._minus_btn)
        layout.addWidget(self._label)
        layout.addWidget(self._plus_btn)

        self._update_enabled()

    def _decrement(self):
        idx = self.STEPS.index(self._current)
        if idx > 0:
            self._current = self.STEPS[idx - 1]
            self._label.setText(f"{self._current} px")
            self._update_enabled()
            self.value_changed.emit(self._current)

    def _increment(self):
        idx = self.STEPS.index(self._current)
        if idx < len(self.STEPS) - 1:
            self._current = self.STEPS[idx + 1]
            self._label.setText(f"{self._current} px")
            self._update_enabled()
            self.value_changed.emit(self._current)

    def _update_enabled(self):
        idx = self.STEPS.index(self._current)
        self._minus_btn.setEnabled(idx > 0)
        self._plus_btn.setEnabled(idx < len(self.STEPS) - 1)

    @property
    def value(self):
        return self._current


def _make_startup_card(label_text, subtitle_text, initial_state):
    """Build startup setting card: label + subtitle on left, switch on right.

    Returns (card_widget, switch).
    """
    card = QtWidgets.QFrame()
    card.setObjectName("settingCard")
    card.setStyleSheet(SETTING_CARD_STYLE)

    card_layout = QtWidgets.QVBoxLayout(card)
    card_layout.setContentsMargins(14, 10, 14, 10)
    card_layout.setSpacing(2)

    top_row = QtWidgets.QWidget()
    top_row.setStyleSheet("background: transparent; border: none;")
    top_layout = QtWidgets.QHBoxLayout(top_row)
    top_layout.setContentsMargins(0, 0, 0, 0)
    top_layout.setSpacing(8)

    label = QtWidgets.QLabel(label_text)
    label.setObjectName("rowLabel")
    label.setStyleSheet(ROW_LABEL_STYLE)
    top_layout.addWidget(label, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

    top_layout.addStretch()

    switch = SleekSwitch()
    switch.setChecked(initial_state)
    # Ensure animation starts at correct position
    if initial_state:
        switch._offset = switch._track_radius * 2 - switch._thumb_radius * 2 + switch._base_offset
    top_layout.addWidget(switch, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

    card_layout.addWidget(top_row)

    subtitle = QtWidgets.QLabel(subtitle_text)
    subtitle.setObjectName("subtitle")
    subtitle.setStyleSheet(SUBTITLE_STYLE)
    card_layout.addWidget(subtitle)

    return card, switch


def _make_setting_card(label_text, subtitle_text, hotkey_text, button_text):
    """Build one setting card: label + subtitle on left, kbd pills + ghost button on right.

    Returns (card_widget, pills_container, pills_list, ghost_button).
    """
    card = QtWidgets.QFrame()
    card.setObjectName("settingCard")
    card.setStyleSheet(SETTING_CARD_STYLE)

    card_layout = QtWidgets.QVBoxLayout(card)
    card_layout.setContentsMargins(14, 10, 14, 10)
    card_layout.setSpacing(2)

    # --- Top row: label (left) ... pills + button (right) ---
    top_row = QtWidgets.QWidget()
    top_row.setStyleSheet("background: transparent; border: none;")
    top_layout = QtWidgets.QHBoxLayout(top_row)
    top_layout.setContentsMargins(0, 0, 0, 0)
    top_layout.setSpacing(8)

    label = QtWidgets.QLabel(label_text)
    label.setObjectName("rowLabel")
    label.setStyleSheet(ROW_LABEL_STYLE)
    top_layout.addWidget(label, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

    top_layout.addStretch()

    pills_container, pills = _build_kbd_pills_row(hotkey_text)
    top_layout.addWidget(pills_container, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

    btn = _make_ghost_button(button_text)
    top_layout.addWidget(btn, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

    card_layout.addWidget(top_row)

    # --- Bottom row: subtitle ---
    subtitle = QtWidgets.QLabel(subtitle_text)
    subtitle.setObjectName("subtitle")
    subtitle.setStyleSheet(SUBTITLE_STYLE)
    card_layout.addWidget(subtitle)

    return card, pills_container, pills, btn


def _make_language_card(label_text, subtitle_text, current_lang, languages_options):
    """Build language setting card: label + subtitle on left, dropdown on right.

    languages_options: list of tuples (display_text, lang_code)
    Returns (card_widget, combo_box).
    """
    card = QtWidgets.QFrame()
    card.setObjectName("settingCard")
    card.setStyleSheet(SETTING_CARD_STYLE)

    card_layout = QtWidgets.QVBoxLayout(card)
    card_layout.setContentsMargins(14, 10, 14, 10)
    card_layout.setSpacing(2)

    # --- Top row: label (left) ... combo box (right) ---
    top_row = QtWidgets.QWidget()
    top_row.setStyleSheet("background: transparent; border: none;")
    top_layout = QtWidgets.QHBoxLayout(top_row)
    top_layout.setContentsMargins(0, 0, 0, 0)
    top_layout.setSpacing(8)

    label = QtWidgets.QLabel(label_text)
    label.setObjectName("rowLabel")
    label.setStyleSheet(ROW_LABEL_STYLE)
    top_layout.addWidget(label, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

    top_layout.addStretch()

    combo = SleekComboBox()
    combo.setObjectName("settingsCombo")
    combo.setStyleSheet(COMBOBOX_STYLE)

    # Set item delegate for checkmark custom paint and custom height
    delegate = CheckmarkDelegate(combo)
    combo.setItemDelegate(delegate)

    # Populate combo box and set current index
    for i, (display_text, lang_code) in enumerate(languages_options):
        if lang_code == "auto":
            icon = create_system_icon()
        else:
            icon = create_language_icon()
        combo.addItem(display_text, lang_code)
        
        # Set the icon via model DecorationRole
        model_index = combo.model().index(i, 0)
        combo.model().setData(model_index, icon, QtCore.Qt.ItemDataRole.DecorationRole)
        
        if lang_code == current_lang:
            combo.setCurrentIndex(combo.count() - 1)

    top_layout.addWidget(combo, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

    card_layout.addWidget(top_row)

    # --- Bottom row: subtitle ---
    subtitle = QtWidgets.QLabel(subtitle_text)
    subtitle.setObjectName("subtitle")
    subtitle.setStyleSheet(SUBTITLE_STYLE)
    card_layout.addWidget(subtitle)

    return card, combo


class HotkeyCaptureDialog(QtWidgets.QDialog):
    """Modal dialog that captures new hotkey input from keyboard."""

    def __init__(self, translate, parent=None):
        super().__init__(parent)
        self.translate = translate
        self.captured_hotkey = None
        self.setWindowTitle(self.translate("settings_hotkey_capture_title"))
        self.setModal(True)
        self.setMinimumWidth(SETTINGS_CAPTURE_DIALOG_MIN_WIDTH)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(CAPTURE_DIALOG_STYLE)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        hint = QtWidgets.QLabel(self.translate("settings_hotkey_capture_placeholder"))
        hint.setObjectName("captureHint")
        hint.setStyleSheet(CAPTURE_HINT_STYLE)
        layout.addWidget(hint)

        self.hotkey_display = QtWidgets.QLineEdit("")
        self.hotkey_display.setReadOnly(True)
        self.hotkey_display.setPlaceholderText(
            self.translate("settings_hotkey_capture_placeholder")
        )
        self.hotkey_display.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.hotkey_display.setStyleSheet(CAPTURE_INPUT_STYLE)
        self.hotkey_display.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hotkey_display)

        self.feedback_label = QtWidgets.QLabel("")
        self.feedback_label.setObjectName("captureFeedback")
        self.feedback_label.setStyleSheet(CAPTURE_FEEDBACK_STYLE)
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.feedback_label)

        layout.addStretch()

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch()

        cancel_button = QtWidgets.QPushButton(
            self.translate("settings_hotkey_capture_cancel_btn")
        )
        cancel_button.setObjectName("cancelButton")
        cancel_button.setStyleSheet(CAPTURE_CANCEL_BUTTON_STYLE)
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        self.save_button = QtWidgets.QPushButton(
            self.translate("settings_save_hotkey_btn")
        )
        self.save_button.setObjectName("saveButton")
        self.save_button.setStyleSheet(CAPTURE_SAVE_BUTTON_STYLE)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.accept)
        button_row.addWidget(self.save_button)

        layout.addLayout(button_row)

        self._set_feedback(self.translate("settings_hotkey_capture_waiting"))
        QtCore.QTimer.singleShot(0, self.setFocus)

    def _set_feedback(self, message, is_error=False):
        self.feedback_label.setText(message)
        self.feedback_label.setStyleSheet(
            f"font-size: 12px; border: none; background: transparent; color: {SETTINGS_ERROR_COLOR};"
            if is_error
            else "font-size: 12px; border: none; background: transparent; color: #999;"
        )

    def keyPressEvent(self, event):
        """Override key event to intercept and parse user shortcut combination."""
        modifier_only_keys = {
            QtCore.Qt.Key.Key_Control,
            QtCore.Qt.Key.Key_Shift,
            QtCore.Qt.Key.Key_Alt,
            QtCore.Qt.Key.Key_Meta,
            QtCore.Qt.Key.Key_Super_L,
            QtCore.Qt.Key.Key_Super_R,
        }

        key = event.key()
        if key in modifier_only_keys:
            self.captured_hotkey = None
            self.save_button.setEnabled(False)
            self._set_feedback(
                self.translate("settings_hotkey_capture_invalid"),
                is_error=True,
            )
            event.accept()
            return

        key_token = _qt_key_to_hotkey_token(key)
        if key_token is None:
            self.captured_hotkey = None
            self.save_button.setEnabled(False)
            self._set_feedback(
                self.translate("settings_hotkey_capture_invalid"),
                is_error=True,
            )
            event.accept()
            return

        modifiers = event.modifiers()
        modifier_tokens = []
        if modifiers & QtCore.Qt.KeyboardModifier.ControlModifier:
            modifier_tokens.append("Ctrl")
        if modifiers & QtCore.Qt.KeyboardModifier.AltModifier:
            modifier_tokens.append("Alt")
        if modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier:
            modifier_tokens.append("Shift")
        if modifiers & QtCore.Qt.KeyboardModifier.MetaModifier:
            modifier_tokens.append("Win")

        requested_hotkey = "+".join(modifier_tokens + [key_token]) if modifier_tokens else key_token
        try:
            _, _, canonical_hotkey = parse_hotkey(requested_hotkey)
        except Exception as exc:
            logger.debug(f"Rejected invalid hotkey input '{requested_hotkey}': {exc}")
            self.captured_hotkey = None
            self.save_button.setEnabled(False)
            self._set_feedback(
                self.translate("settings_hotkey_invalid"),
                is_error=True,
            )
            event.accept()
            return

        self.captured_hotkey = canonical_hotkey
        self.hotkey_display.setText(canonical_hotkey)
        self._set_feedback(
            self.translate("settings_hotkey_capture_captured", hotkey=canonical_hotkey),
            is_error=False,
        )
        self.save_button.setEnabled(True)
        event.accept()


class SettingsDialogController(QtCore.QObject):
    """Settings panel with per-setting cards, per-key kbd pills, header bar."""
    language_changed = QtCore.pyqtSignal()

    def __init__(self, translate, config_path, hotkey_manager, on_font_size_changed=None):
        super().__init__()
        self.translate = translate
        self.config_path = config_path
        self.hotkey_manager = hotkey_manager
        self._on_font_size_changed = on_font_size_changed
        self._dialog = None
        self._screenshot_pills_container = None
        self._screenshot_pills = None
        self._ocr_pills_container = None
        self._ocr_pills = None

    def _refresh_pills(self):
        if self._screenshot_pills_container is None:
            return
        try:
            self._screenshot_pills = _rebuild_kbd_pills(
                self._screenshot_pills_container, self.hotkey_manager.current_hotkey_name
            )
            self._ocr_pills = _rebuild_kbd_pills(
                self._ocr_pills_container, self.hotkey_manager.current_ocr_hotkey_name
            )
        except RuntimeError:
            self._screenshot_pills_container = None
            self._ocr_pills_container = None

    def show(self):
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.raise_()
            self._dialog.activateWindow()
            return

        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle(self.translate("settings_title"))
        dialog.setModal(False)
        dialog.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setFixedWidth(SETTINGS_DIALOG_WIDTH)
        dialog.setStyleSheet(DIALOG_STYLE)
        self._dialog = dialog

        def clear_settings_dialog(_obj=None):
            self._dialog = None
            self._screenshot_pills_container = None
            self._screenshot_pills = None
            self._ocr_pills_container = None
            self._ocr_pills = None

        dialog.destroyed.connect(clear_settings_dialog)

        outer_layout = QtWidgets.QVBoxLayout(dialog)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # --- Body ---
        body = QtWidgets.QWidget()
        body.setStyleSheet("background: transparent; border: none;")
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(16, 16, 16, 8)
        body_layout.setSpacing(8)

        # --- Section: Shortcuts ---
        shortcuts_header = QtWidgets.QLabel(self.translate("settings_section_shortcuts"))
        shortcuts_header.setObjectName("sectionHeader")
        shortcuts_header.setStyleSheet(SECTION_HEADER_STYLE)
        body_layout.addWidget(shortcuts_header)

        # --- Screenshot card ---
        def change_hotkey():
            capture_dialog = HotkeyCaptureDialog(self.translate, parent=dialog)
            capture_dialog.setWindowTitle(self.translate("settings_hotkey_capture_title"))
            if capture_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            canonical_hotkey = capture_dialog.captured_hotkey
            if not canonical_hotkey:
                return
            try:
                update_hotkey_in_config(self.config_path, canonical_hotkey)
            except Exception as exc:
                logger.exception(f"Failed to save hotkey '{canonical_hotkey}' to config: {exc}")
                _set_status(self.translate("settings_hotkey_save_failed"), True)
                return
            self.hotkey_manager.apply_hotkey_reload()
            self._refresh_pills()
            if self.hotkey_manager.current_hotkey_name == canonical_hotkey:
                _set_status("", False)
            else:
                _set_status(
                    self.translate(
                        "settings_hotkey_apply_failed",
                        old_hotkey=self.hotkey_manager.current_hotkey_name,
                        new_hotkey=canonical_hotkey,
                    ),
                    True,
                )

        card1, self._screenshot_pills_container, self._screenshot_pills, btn1 = _make_setting_card(
            self.translate("settings_hotkey_label"),
            self.translate("settings_hotkey_subtitle"),
            self.hotkey_manager.current_hotkey_name,
            self.translate("settings_change_hotkey_btn"),
        )
        btn1.clicked.connect(change_hotkey)
        body_layout.addWidget(card1)

        # --- OCR card ---
        def change_ocr_hotkey():
            capture_dialog = HotkeyCaptureDialog(self.translate, parent=dialog)
            capture_dialog.setWindowTitle(self.translate("settings_hotkey_capture_ocr_title"))
            if capture_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            canonical_hotkey = capture_dialog.captured_hotkey
            if not canonical_hotkey:
                return
            try:
                update_ocr_hotkey_in_config(self.config_path, canonical_hotkey)
            except Exception as exc:
                logger.exception(f"Failed to save OCR hotkey '{canonical_hotkey}' to config: {exc}")
                _set_status(self.translate("settings_hotkey_save_failed"), True)
                return
            self.hotkey_manager.apply_ocr_hotkey_reload()
            self._refresh_pills()
            if self.hotkey_manager.current_ocr_hotkey_name == canonical_hotkey:
                _set_status("", False)
            else:
                _set_status(
                    self.translate(
                        "settings_ocr_hotkey_apply_failed",
                        old_hotkey=self.hotkey_manager.current_ocr_hotkey_name,
                        new_hotkey=canonical_hotkey,
                    ),
                    True,
                )

        card2, self._ocr_pills_container, self._ocr_pills, btn2 = _make_setting_card(
            self.translate("menu_ocr_recognize"),
            self.translate("settings_ocr_hotkey_subtitle"),
            self.hotkey_manager.current_ocr_hotkey_name,
            self.translate("settings_change_ocr_hotkey_btn"),
        )
        btn2.clicked.connect(change_ocr_hotkey)
        body_layout.addWidget(card2)

        # --- Section: Preferences ---
        prefs_header = QtWidgets.QLabel(self.translate("settings_section_preferences"))
        prefs_header.setObjectName("sectionHeader")
        prefs_header.setStyleSheet(SECTION_HEADER_STYLE)
        body_layout.addWidget(prefs_header)

        # --- Language card ---
        def change_language(index):
            selected_lang = combo3.itemData(index)
            try:
                update_ui_lang_in_config(self.config_path, selected_lang)
                self.language_changed.emit()
                dialog.close()
            except Exception as exc:
                logger.exception(f"Failed to save language setting: {exc}")
                _set_status(self.translate("error"), True)

        lang_options = [
            (self.translate("settings_language_auto"), "auto"),
            (self.translate("settings_language_en"), "en"),
            (self.translate("settings_language_zh"), "zh"),
            (self.translate("settings_language_zh_tw"), "zh-TW"),
        ]

        current_lang = get_configured_ui_lang(self.config_path)

        card3, combo3 = _make_language_card(
            self.translate("settings_language_label"),
            self.translate("settings_language_subtitle"),
            current_lang,
            lang_options,
        )
        combo3.currentIndexChanged.connect(change_language)
        body_layout.addWidget(card3)

        # --- Font size card ---
        def change_font_size(value):
            try:
                update_ocr_font_size(value)
                if self._on_font_size_changed:
                    self._on_font_size_changed()
            except Exception as exc:
                logger.exception(f"Failed to save OCR font size: {exc}")
                _set_status(self.translate("error"), True)

        card5 = QtWidgets.QFrame()
        card5.setObjectName("settingCard")
        card5.setStyleSheet(SETTING_CARD_STYLE)

        card5_layout = QtWidgets.QVBoxLayout(card5)
        card5_layout.setContentsMargins(14, 10, 14, 10)
        card5_layout.setSpacing(2)

        top_row5 = QtWidgets.QWidget()
        top_row5.setStyleSheet("background: transparent; border: none;")
        top_layout5 = QtWidgets.QHBoxLayout(top_row5)
        top_layout5.setContentsMargins(0, 0, 0, 0)
        top_layout5.setSpacing(8)

        label5 = QtWidgets.QLabel(self.translate("settings_ocr_font_size_label"))
        label5.setObjectName("rowLabel")
        label5.setStyleSheet(ROW_LABEL_STYLE)
        top_layout5.addWidget(label5, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

        top_layout5.addStretch()

        stepper = FontSizeStepper(initial_value=get_ocr_font_size())
        stepper.value_changed.connect(change_font_size)
        top_layout5.addWidget(stepper, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

        card5_layout.addWidget(top_row5)

        subtitle5 = QtWidgets.QLabel(self.translate("settings_ocr_font_size_subtitle"))
        subtitle5.setObjectName("subtitle")
        subtitle5.setStyleSheet(SUBTITLE_STYLE)
        card5_layout.addWidget(subtitle5)

        body_layout.addWidget(card5)

        # --- Startup card ---
        async def toggle_startup(checked):
            success = await startup_manager.set_startup_state(checked)
            if not success and checked:
                real_state = await startup_manager.get_startup_state()
                card4_switch.setChecked(real_state)

        def on_startup_toggled(checked):
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(toggle_startup(checked))
                loop.close()
            except Exception as exc:
                logger.exception(f"Failed to toggle startup state: {exc}")

        loop = asyncio.get_event_loop()
        initial_startup = loop.run_until_complete(startup_manager.get_startup_state())

        card4, card4_switch = _make_startup_card(
            self.translate("settings_startup_label"),
            self.translate("settings_startup_subtitle"),
            initial_startup,
        )
        card4_switch.clicked.connect(on_startup_toggled)
        body_layout.addWidget(card4)

        outer_layout.addWidget(body)

        # --- Status label ---
        status_label = QtWidgets.QLabel("")
        status_label.setObjectName("statusLabel")
        status_label.setStyleSheet(STATUS_LABEL_STYLE)
        status_label.setWordWrap(True)
        status_container = QtWidgets.QWidget()
        status_container.setStyleSheet("background: transparent; border: none;")
        sc_layout = QtWidgets.QHBoxLayout(status_container)
        sc_layout.setContentsMargins(20, 0, 20, 12)
        sc_layout.addWidget(status_label)
        outer_layout.addWidget(status_container)

        def _set_status(message, is_error=False):
            status_label.setText(message)
            status_label.setStyleSheet(
                f"font-size: 12px; padding: 0 4px; border: none; background: transparent; color: {SETTINGS_ERROR_COLOR};"
                if is_error
                else STATUS_LABEL_STYLE
            )

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
