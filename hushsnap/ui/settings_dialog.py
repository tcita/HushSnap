"""
HushSnap settings dialog module.
Provides UI to view current hotkey and capture/change a new hotkey.
"""

import logging
from PyQt6 import QtCore, QtWidgets

from ..config import parse_hotkey, update_hotkey_in_config, update_ocr_hotkey_in_config
from .styles import (
    CAPTURE_CANCEL_BUTTON_STYLE,
    CAPTURE_DIALOG_STYLE,
    CAPTURE_FEEDBACK_STYLE,
    CAPTURE_HINT_STYLE,
    CAPTURE_INPUT_STYLE,
    CAPTURE_SAVE_BUTTON_STYLE,
    DIALOG_STYLE,
    GHOST_BUTTON_STYLE,
    HEADER_BAR_STYLE,
    HEADER_ICON_STYLE,
    HEADER_TITLE_STYLE,
    KBD_PILL_STYLE,
    PLUS_LABEL_STYLE,
    ROW_LABEL_STYLE,
    SETTING_CARD_STYLE,
    SETTINGS_CAPTURE_DIALOG_MIN_WIDTH,
    SETTINGS_DIALOG_WIDTH,
    SETTINGS_ERROR_COLOR,
    STATUS_LABEL_STYLE,
    SUBTITLE_STYLE,
)

logger = logging.getLogger(__name__)


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
    pill.setFixedHeight(24)
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
    btn.setFixedHeight(26)
    return btn


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


class SettingsDialogController:
    """Settings panel with per-setting cards, per-key kbd pills, header bar."""

    def __init__(self, translate, config_path, hotkey_manager):
        self.translate = translate
        self.config_path = config_path
        self.hotkey_manager = hotkey_manager
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

        # --- Header bar ---
        header = QtWidgets.QFrame()
        header.setObjectName("headerBar")
        header.setStyleSheet(HEADER_BAR_STYLE)
        header.setFixedHeight(44)
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 16, 0)
        header_layout.setSpacing(8)

        icon = QtWidgets.QLabel("⚙")  # gear
        icon.setObjectName("headerIcon")
        icon.setStyleSheet(HEADER_ICON_STYLE)
        header_layout.addWidget(icon, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

        title = QtWidgets.QLabel(self.translate("settings_title"))
        title.setObjectName("headerTitle")
        title.setStyleSheet(HEADER_TITLE_STYLE)
        header_layout.addWidget(title, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

        header_layout.addStretch()
        outer_layout.addWidget(header)

        # --- Body ---
        body = QtWidgets.QWidget()
        body.setStyleSheet("background: transparent; border: none;")
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(16, 14, 16, 8)
        body_layout.setSpacing(8)

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
