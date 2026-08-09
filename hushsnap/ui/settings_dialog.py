"""
HushSnap settings dialog module.
Provides UI to view current hotkey and capture/change a new hotkey.
"""

import logging
import asyncio
from PyQt6 import QtCore, QtGui, QtWidgets

from .. import __version__
from ..config import (
    parse_hotkey,
    update_hotkey_in_config,

    get_configured_ui_lang,
    update_ui_lang_in_config,
    get_ocr_font_size,
    update_ocr_font_size,
    get_auto_ocr_after_capture,
    update_auto_ocr_after_capture,
    get_close_ocr_popup_on_focus_loss,
    update_close_ocr_popup_on_focus_loss,
    get_show_capture_dimension_label,
    update_show_capture_dimension_label,
    get_thumbnail_display_time,
    update_thumbnail_display_time,
    get_thumbnail_frame,
    update_thumbnail_frame,
)
from ..constants import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN
from ..system import startup_manager
from .thumbnail import thumbnail_manager
from .styles import (
    BRAND_GREEN,
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
    SETTINGS_LABEL_COLOR,
    SETTINGS_WARNING_COLOR,
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

    def wheelEvent(self, event):
        # Swallow the wheel so hovering a combo and scrolling moves the
        # settings page instead of silently changing the selected value.
        # QComboBox's default cycles the current index on the scroll wheel,
        # which users trip over while just trying to scroll down - a value
        # change should be a deliberate click-to-open-the-dropdown action,
        # never an accidental scroll. Ignoring (not accepting) lets the
        # event propagate to the enclosing QScrollArea so the page scrolls.
        event.ignore()


class CategoryList(QtWidgets.QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(110)
        self.setStyleSheet(
            "QListWidget {"
            "  background: #F2F2F2;"
            "  border: none;"
            "  border-right: 0.5px solid #E5E5E5;"
            "  outline: none;"
            "  padding: 10px 0;"
            "}"
            "QListWidget::item {"
            "  padding: 10px 16px;"
            "  color: #777;"
            "  font-size: 13px;"
            "  font-weight: 500;"
            "  border: none;"
            "  font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
            "QListWidget::item:selected {"
            "  background: #FFFFFF;"
            "  color: #333;"
            "  border-left: 3px solid " + BRAND_GREEN + ";"
            "}"
            "QListWidget::item:hover:!selected {"
            "  background: #EBEBEB;"
            "}"
        )


def _load_svg_icon(name, color_str="#CCCCCC", size=20):
    """Load a single-color SVG icon from the icons dir."""
    from .icon_utils import load_svg_icon
    return load_svg_icon(name, color_str, size=size)


def create_system_icon():
    return _load_svg_icon("system")

def create_language_icon():
    return _load_svg_icon("language")


def _qt_key_to_hotkey_token(key):
    """Convert Qt key enum value into internal hotkey token text.

    Only letters, digits and function keys are accepted as the main key of a
    hotkey — the universal hotkey vocabulary. Everything else (Esc, Tab,
    Enter, Space, arrows, Backspace, …) returns None so the capture dialog
    can silently ignore it: most apps don't bind those as shortcuts, so
    letting them through would only set up conflicts. The user presses one,
    nothing happens, and they reach for a normal key.
    """
    if QtCore.Qt.Key.Key_A <= key <= QtCore.Qt.Key.Key_Z:
        return chr(key)
    if QtCore.Qt.Key.Key_0 <= key <= QtCore.Qt.Key.Key_9:
        return chr(key)
    if QtCore.Qt.Key.Key_F1 <= key <= QtCore.Qt.Key.Key_F24:
        return f"F{key - QtCore.Qt.Key.Key_F1 + 1}"
    return None


# Virtual-key codes for Alt-modified combos Windows reserves for the shell.
_VK_SPACE = 0x20
_VK_F4 = 0x73

# Ctrl + these letters are near-universal edit/file ops (select all, copy, paste,
# cut, undo, redo, save, print, new, open, find, close). Binding them as a global
# hotkey shadows shortcuts almost every app uses, so they earn an amber warning.
# Other Ctrl+letter combos (Q/M/G/J/B/I/U/L/…) are app-specific or rare and are
# left alone — a soft warning should only flag genuinely high-collision keys.
_CONFLICT_PRONE_CTRL_KEYS = frozenset({
    0x41,  # A — select all
    0x43,  # C — copy
    0x46,  # F — find
    0x4E,  # N — new
    0x4F,  # O — open
    0x50,  # P — print
    0x53,  # S — save
    0x56,  # V — paste
    0x57,  # W — close
    0x58,  # X — cut
    0x59,  # Y — redo
    0x5A,  # Z — undo
})


def _is_conflict_prone_hotkey(modifier_mask, virtual_key):
    """Return True when a combo is likely to clash with common app/system shortcuts.

    These all *register* successfully via ``RegisterHotKey`` (so they are valid
    global hotkeys) but silently hijack combos users rely on elsewhere — editor
    save/copy, browser tabs, help keys, window menus. We flag them so the capture
    dialog can show a soft amber warning instead of letting the binding go
    through with no signal that it may shadow another app's shortcut.

    Considered safe (no warning): combos that include Alt or Win (other than the
    reserved Alt+Space / Alt+F4), Ctrl combos with an extra modifier such as
    Ctrl+Shift+… / Ctrl+Alt+…, and the long tail of Ctrl+letter combos that are
    not near-universal shortcuts. Only flag combos that collide with shortcuts
    almost every app uses — bare keys, the shell window keys, and the canonical
    Ctrl+edit/file ops.
    """
    has_alt = bool(modifier_mask & MOD_ALT)
    has_ctrl = bool(modifier_mask & MOD_CONTROL)
    has_shift = bool(modifier_mask & MOD_SHIFT)
    has_win = bool(modifier_mask & MOD_WIN)

    # No modifier at all → bare key intercepts normal typing / common keys.
    if not (has_alt or has_ctrl or has_win):
        return True

    # Alt+Space (window menu) and Alt+F4 (close) are shell-reserved.
    if has_alt and not has_ctrl and not has_win:
        if virtual_key in (_VK_SPACE, _VK_F4):
            return True
        # Alt + letter/digit/function is the safe region the default Alt+Q lives in.
        return False

    # Pure Ctrl + a single key (no Alt/Win/Shift). Only the near-universal
    # edit/file ops are worth a warning — Ctrl+A/C/V/X/Z/Y (edit), S/P/N/O/F/W
    # (file/find/close). The rest (Ctrl+Q/M/G/J/B/I/U/L/…) are app-specific or
    # rare, so we don't nag on them.
    if has_ctrl and not has_alt and not has_win and not has_shift:
        return virtual_key in _CONFLICT_PRONE_CTRL_KEYS

    # Ctrl+Shift+…, Ctrl+Alt+…, Win+… (non-reserved), etc. → low collision risk.
    return False


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
    """Build a row of kbd pills separated by '+' labels (no frame).

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
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    container.setStyleSheet("background: transparent; border: none;")

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
    def __init__(self, parent=None, track_radius=12, thumb_radius=10):
        super().__init__(parent)
        self.setCheckable(True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
        self._track_radius = track_radius
        self._thumb_radius = thumb_radius
        self._margin = (self._track_radius * 2 - self._thumb_radius * 2) // 2
        self._base_offset = self._margin
        self._offset = self._base_offset
        self._color_off = QtGui.QColor("#D5D5D5")
        self._color_on = QtGui.QColor(BRAND_GREEN)
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
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
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


class PulseDot(QtWidgets.QWidget):
    """A flat, modern status indicator: filled core + pulsing outline ring.

    Matches the app's clean, low-chrome design language — no 3D gradients,
    no shadows, no specular highlights. Just a solid dot with a breathing
    outline ring whose opacity pulses to signal "listening".

    Colour can be switched at runtime (green = safe / recording,
    amber = conflict warning).
    """

    _SIZE = 20   # widget size
    _CORE_R = 5  # filled centre radius
    _RING_R = 8  # pulsing outline radius
    _RING_W = 2  # outline stroke width

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self._SIZE, self._SIZE)
        self._alpha = 255
        self._color = QtGui.QColor(BRAND_GREEN)

        self._anim = QtCore.QVariantAnimation(
            self,
            startValue=40,
            endValue=255,
            duration=1400,
            valueChanged=self._update_alpha,
        )
        self._anim.setEasingCurve(QtCore.QEasingCurve.Type.InOutCubic)
        self._anim.finished.connect(self._toggle_direction)
        self._anim.start()

    # ── public API ────────────────────────────────────────────────

    def set_color(self, color):
        """Switch the dot to *color* (QColor or hex string like ``"#E8941A"``)."""
        if isinstance(color, str):
            color = QtGui.QColor(color)
        self._color = QtGui.QColor(color)
        self.update()

    # ── animation callbacks ───────────────────────────────────────

    def _update_alpha(self, value):
        self._alpha = value
        self.update()

    def _toggle_direction(self):
        if self._anim.direction() == QtCore.QVariantAnimation.Direction.Forward:
            self._anim.setDirection(QtCore.QVariantAnimation.Direction.Backward)
        else:
            self._anim.setDirection(QtCore.QVariantAnimation.Direction.Forward)
        self._anim.start()

    # ── painting ──────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        cx = self.width() / 2.0
        cy = self.height() / 2.0

        # ── pulsing outline ring ──────────────────────────────────
        ring_alpha = int(self._alpha * 0.55)
        ring_color = QtGui.QColor(self._color)
        ring_color.setAlpha(ring_alpha)

        pen = QtGui.QPen(ring_color, self._RING_W)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QtCore.QPointF(cx, cy), self._RING_R, self._RING_R)

        # ── solid filled core ─────────────────────────────────────
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(QtCore.QPointF(cx, cy), self._CORE_R, self._CORE_R)


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
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        # Pulse indicator row (Recording status)
        status_row = QtWidgets.QHBoxLayout()
        status_row.setSpacing(8)
        self.pulse_dot = PulseDot(self)
        status_row.addWidget(self.pulse_dot, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        
        self.status_title = QtWidgets.QLabel(self.translate("settings_hotkey_capture_waiting"))
        self.status_title.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {BRAND_GREEN}; "
            "font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", \"Segoe UI\", sans-serif; "
            "background: transparent; border: none;"
        )
        status_row.addWidget(self.status_title, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        status_row.addStretch()
        layout.addLayout(status_row)

        # Container for custom Kbd Pills representation of the captured hotkey
        self.pills_container = QtWidgets.QFrame()
        self.pills_container.setObjectName("pillsContainer")
        self.pills_container.setStyleSheet(
            "QFrame#pillsContainer {"
            "  border: 1px solid #D5D5D5;"
            "  border-radius: 8px;"
            "  background-color: #F9F9F9;"
            "  min-height: 50px;"
            "}"
        )
        
        self.pills_layout = QtWidgets.QHBoxLayout(self.pills_container)
        self.pills_layout.setContentsMargins(16, 12, 16, 12)
        self.pills_layout.setSpacing(6)
        
        layout.addWidget(self.pills_container)

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
        self.save_button.setStyleSheet((CAPTURE_SAVE_BUTTON_STYLE + """
            QPushButton#saveButton:enabled {
                background-color: __BRAND__;
            }
            QPushButton#saveButton:enabled:hover {
                background-color: #4eb579;
            }
            QPushButton#saveButton:enabled:pressed {
                background-color: #3f9b65;
            }
        """).replace("__BRAND__", BRAND_GREEN))
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.accept)
        button_row.addWidget(self.save_button)

        layout.addLayout(button_row)

        self._set_placeholder_display()
        self._set_feedback(self.translate("settings_hotkey_capture_waiting"))
        QtCore.QTimer.singleShot(0, self.setFocus)

    def _set_placeholder_display(self):
        # Clear container layout
        while self.pills_layout.count():
            item = self.pills_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        placeholder = QtWidgets.QLabel(self.translate("settings_hotkey_capture_placeholder"))
        placeholder.setStyleSheet(
            "color: #999999; font-size: 13px; font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "background: transparent; border: none;"
        )
        self.pills_layout.addWidget(placeholder, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)

        # Reset stylesheet to neutral grey border
        self._set_neutral_frame()

        # Reset dot to green "waiting" state
        self._set_status("safe",
            self.translate("settings_hotkey_capture_waiting"))

    def _update_pills_display(self, hotkey_string, state="safe"):
        # Clear container layout
        while self.pills_layout.count():
            item = self.pills_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        accent = SETTINGS_WARNING_COLOR if state == "warning" else BRAND_GREEN

        parts = [p.strip() for p in hotkey_string.split("+") if p.strip()]
        for i, part in enumerate(parts):
            if i > 0:
                plus = _make_plus_label()
                self.pills_layout.addWidget(plus, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
            pill = _make_kbd_pill(part)
            pill.setStyleSheet(
                f"border: 1.5px solid {accent}; border-radius: 5px; background: #FFFFFF; "
                "padding: 4px 10px; font-family: 'Consolas', 'Segoe UI', monospace; "
                f"font-size: 13px; font-weight: bold; color: {accent};"
            )
            self.pills_layout.addWidget(pill, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

    # Frame states for the pills container. Color is set only when a complete
    # combo is captured; a half-finished input (bare modifier, unmappable key)
    # stays neutral rather than flashing red mid-keystroke.
    _FRAME_STYLES = {
        "neutral": (
            "QFrame#pillsContainer {"
            "  border: 1px solid #D5D5D5;"
            "  border-radius: 8px;"
            "  background-color: #F9F9F9;"
            "  min-height: 50px;"
            "}"
        ),
        "safe": (
            "QFrame#pillsContainer {"
            f"  border: 1.5px solid {BRAND_GREEN};"
            "  border-radius: 8px;"
            "  background-color: #F2FDF6;"
            "  min-height: 50px;"
            "}"
        ),
        "warning": (
            "QFrame#pillsContainer {"
            f"  border: 1.5px solid {SETTINGS_WARNING_COLOR};"
            "  border-radius: 8px;"
            "  background-color: #FFF7EC;"
            "  min-height: 50px;"
            "}"
        ),
    }

    def _set_neutral_frame(self):
        self.pills_container.setStyleSheet(self._FRAME_STYLES["neutral"])

    def _set_frame_style(self, state):
        """Set the pills frame to 'safe' (green) or 'warning' (amber)."""
        self.pills_container.setStyleSheet(self._FRAME_STYLES[state])

    def _set_status(self, state, title_text):
        """Update the pulse dot colour and status title to *state*.

        ``"safe"`` → green dot, ``"warning"`` → amber dot.
        """
        if state == "warning":
            self.pulse_dot.set_color(SETTINGS_WARNING_COLOR)
            self.status_title.setStyleSheet(
                f"font-size: 13px; font-weight: bold; color: {SETTINGS_WARNING_COLOR}; "
                "font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", \"Segoe UI\", sans-serif; "
                "background: transparent; border: none;"
            )
        else:
            self.pulse_dot.set_color(BRAND_GREEN)
            self.status_title.setStyleSheet(
                f"font-size: 13px; font-weight: bold; color: {BRAND_GREEN}; "
                "font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", \"Segoe UI\", sans-serif; "
                "background: transparent; border: none;"
            )
        self.status_title.setText(title_text)

    def _set_feedback(self, message, is_warning=False):
        self.feedback_label.setText(message)
        if is_warning:
            color = SETTINGS_WARNING_COLOR
            weight = "500"
        else:
            color = "#777"
            weight = "400"
        self.feedback_label.setStyleSheet(
            f"font-size: 12px; font-weight: {weight}; border: none;"
            f" background: transparent; color: {color};"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
        )

    def keyPressEvent(self, event):
        """Override key event to intercept and parse user shortcut combination.

        The capture box is overwrite-style, not edit-style: pressing a valid
        combo replaces whatever was captured, and anything that isn't a valid
        combo is silently ignored. No feedback, no clearing — the user presses
        an unusable key (Backspace, Tab, Enter, a bare modifier, an arrow…),
        nothing happens, and they reach for a normal key. This sidesteps the
        whole class of "did my captured combo get deleted?" confusion: the only
        way to change what's shown is to capture a new valid combo.
        """
        key = event.key()
        key_token = _qt_key_to_hotkey_token(key)
        if key_token is None:
            # Bare modifier, Backspace, Tab, Enter, arrows, … — silently ignore.
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
            modifier_mask, virtual_key, canonical_hotkey = parse_hotkey(requested_hotkey)
        except Exception as exc:
            logger.debug(f"Rejected invalid hotkey input '{requested_hotkey}': {exc}")
            event.accept()
            return

        self.captured_hotkey = canonical_hotkey

        if _is_conflict_prone_hotkey(modifier_mask, virtual_key):
            state = "warning"
        else:
            state = "safe"

        self._update_pills_display(canonical_hotkey, state)

        # Status row: just the hotkey name — compact, never overflows.
        # The dot colour + frame + pill borders already signal safe vs warning.
        # Feedback label: the full explanatory sentence (word-wrapped).
        if state == "warning":
            self._set_status("warning", canonical_hotkey)
            self._set_feedback(
                self.translate("settings_hotkey_capture_risky",
                               hotkey=canonical_hotkey),
                is_warning=True,
            )
            self._set_frame_style("warning")
        else:
            self._set_status("safe", canonical_hotkey)
            self._set_feedback(
                self.translate("settings_hotkey_capture_captured",
                               hotkey=canonical_hotkey),
            )
            self._set_frame_style("safe")
        self.save_button.setEnabled(True)
        event.accept()


class SettingsDialogController(QtCore.QObject):
    """Settings panel with categories (General, Capture, OCR)."""
    language_changed = QtCore.pyqtSignal()

    def __init__(self, translate, config_path, hotkey_manager,
                 on_font_size_changed=None, on_dim_label_changed=None):
        super().__init__()
        self.translate = translate
        self.config_path = config_path
        self.hotkey_manager = hotkey_manager
        self._on_font_size_changed = on_font_size_changed
        self._on_dim_label_changed = on_dim_label_changed
        self._dialog = None
        self._screenshot_pills_container = None
        self._screenshot_pills = None

    def is_visible(self) -> bool:
        """Return True if the settings dialog is currently visible."""
        return self._dialog is not None and self._dialog.isVisible()

    def _refresh_pills(self):
        if self._screenshot_pills_container is None:
            return
        name = self.hotkey_manager.current_hotkey_name
        try:
            self._screenshot_pills = _rebuild_kbd_pills(
                self._screenshot_pills_container, name)
        except RuntimeError:
            self._screenshot_pills_container = None

    def show(self, section=None):
        """Open the settings dialog, optionally jumping to *section*.

        *section* may be ``"general"``, ``"capture"``, or ``"ocr"``.
        Defaults to the General page.
        """
        _SECTION_INDEX = {"general": 0, "capture": 1, "ocr": 2}
        target_row = _SECTION_INDEX.get(section, 0)

        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.raise_()
            self._dialog.activateWindow()
            if target_row != 0 and hasattr(self, "_sidebar"):
                self._sidebar.setCurrentRow(target_row)
            return

        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle(self.translate("settings_title"))
        dialog.setModal(False)
        dialog.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        
        # Enable resizing and set constraints
        dialog.setWindowFlags(dialog.windowFlags() | QtCore.Qt.WindowType.WindowMinMaxButtonsHint)
        dialog.setMinimumSize(600, 400) 
        dialog.resize(640, 460) # Balanced height, wide enough for content
        dialog.setStyleSheet(DIALOG_STYLE)
        self._dialog = dialog

        def clear_settings_dialog(_obj=None):
            self._dialog = None
            self._sidebar = None
            self._screenshot_pills_container = None
            self._screenshot_pills = None

        dialog.destroyed.connect(clear_settings_dialog)

        main_layout = QtWidgets.QHBoxLayout(dialog)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Sidebar ---
        sidebar = CategoryList()
        self._sidebar = sidebar
        main_layout.addWidget(sidebar)

        # --- Content Area ---
        content_stack = QtWidgets.QStackedWidget()
        content_stack.setStyleSheet("background: #FFFFFF;")
        main_layout.addWidget(content_stack)

        # --- Helper for scrollable pages ---
        def create_page():
            page = QtWidgets.QWidget()
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            # Modern slim scrollbar (matches the OCR popup's), replacing the
            # chunky Windows-XP-style default that appears once a page (e.g.
            # Capture, after the Corner Ornament row was added) overflows.
            scroll.setStyleSheet(
                "QScrollArea { background: transparent; }"
                "QScrollBar:vertical {"
                " background: transparent;"
                " width: 8px;"
                " margin: 4px 0px 4px 0px;"
                "}"
                "QScrollBar::handle:vertical {"
                " background: #C8C8C8;"
                " min-height: 30px;"
                " border-radius: 4px;"
                "}"
                "QScrollBar::handle:vertical:hover {"
                f" background: {BRAND_GREEN};"
                "}"
                "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
                " height: 0px;"
                "}"
                "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
                " background: transparent;"
                "}"
            )
            
            container = QtWidgets.QWidget()
            container.setStyleSheet("background: transparent;")
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(20, 20, 20, 20)
            layout.setSpacing(12)
            
            scroll.setWidget(container)
            
            page_layout = QtWidgets.QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.addWidget(scroll)
            
            return page, layout

        # ── Page: General ──────────────────────────────────────────
        general_page, general_layout = create_page()
        
        # Section: Interface
        general_layout.addWidget(QtWidgets.QLabel(self.translate("settings_section_general").upper()))
        
        # Language
        def change_language(index):
            selected_lang = combo_lang.itemData(index)
            try:
                update_ui_lang_in_config(self.config_path, selected_lang)
                self.language_changed.emit()
                dialog.close()
            except Exception as exc:
                logger.exception(f"Failed to save language setting: {exc}")

        lang_options = [
            (self.translate("settings_language_auto"), "auto"),
            (self.translate("settings_language_en"), "en"),
            (self.translate("settings_language_zh"), "zh"),
            (self.translate("settings_language_zh_tw"), "zh-TW"),
            (self.translate("settings_language_ja"), "ja"),
        ]
        card_lang, combo_lang = _make_language_card(
            self.translate("settings_language_label"),
            self.translate("settings_language_subtitle"),
            get_configured_ui_lang(self.config_path),
            lang_options,
        )
        combo_lang.currentIndexChanged.connect(change_language)
        general_layout.addWidget(card_lang)

        # Startup
        async def toggle_startup(checked):
            await startup_manager.set_startup_state(checked)
        def on_startup_toggled(checked):
            loop = asyncio.new_event_loop()
            loop.run_until_complete(toggle_startup(checked))
            loop.close()

        loop = asyncio.get_event_loop()
        initial_startup = loop.run_until_complete(startup_manager.get_startup_state())
        card_start, switch_start = _make_startup_card(
            self.translate("settings_startup_label"),
            self.translate("settings_startup_subtitle"),
            initial_startup,
        )
        switch_start.clicked.connect(on_startup_toggled)
        general_layout.addWidget(card_start)
        
        general_layout.addStretch()
        content_stack.addWidget(general_page)

        # ── Page: Capture ──────────────────────────────────────────
        capture_page, capture_layout = create_page()
        
        # Section: Shortcuts & Behavior
        capture_layout.addWidget(QtWidgets.QLabel(self.translate("settings_section_capture").upper()))

        # Hotkey
        def change_hotkey():
            cap = HotkeyCaptureDialog(self.translate, parent=dialog)
            # Temporarily unregister the live global hotkey while the capture
            # dialog is open. Otherwise pressing the *current* hotkey (e.g. to
            # rebind it) is intercepted by the OS as a WM_HOTKEY and triggers a
            # screenshot behind the modal dialog — the overlay then can't win
            # foreground from the dialog, leaving Esc dead and the user stuck.
            hm = self.hotkey_manager
            old_modifier = hm.current_hotkey_modifier
            old_vk = hm.current_hotkey_virtual_key
            old_name = hm.current_hotkey_name
            hm.unregister_current_hotkey()
            try:
                accepted = cap.exec() == QtWidgets.QDialog.DialogCode.Accepted
            finally:
                if not accepted:
                    # Re-register the previous hotkey so the user isn't left
                    # with no working shortcut after cancelling.
                    hm.register_hotkey(old_modifier, old_vk, old_name)
            if accepted:
                hot = cap.captured_hotkey
                update_hotkey_in_config(self.config_path, hot)
                hm.apply_hotkey_reload()
                self._refresh_pills()

        card_hot, self._screenshot_pills_container, self._screenshot_pills, btn_hot = _make_setting_card(
            self.translate("settings_hotkey_label"),
            self.translate("settings_hotkey_subtitle"),
            self.hotkey_manager.current_hotkey_name,
            self.translate("settings_change_hotkey_btn"),
        )
        btn_hot.clicked.connect(change_hotkey)
        capture_layout.addWidget(card_hot)

        # Thumbnail Time
        def change_thumb_time(index):
            val = combo_thumb.itemData(index)
            update_thumbnail_display_time(val, self.config_path)
            thumbnail_manager.refresh_current()

        thumb_options = [
            (self.translate("settings_thumbnail_time_3s"), 3000),
            (self.translate("settings_thumbnail_time_5s"), 5000),
            (self.translate("settings_thumbnail_time_12s"), 12000),
            (self.translate("settings_thumbnail_time_30s"), 30000),
            (self.translate("settings_thumbnail_time_never"), 0),
        ]
        card_thumb, combo_thumb = _make_language_card(
            self.translate("settings_thumbnail_time_label"),
            self.translate("settings_thumbnail_time_subtitle"),
            get_thumbnail_display_time(self.config_path),
            thumb_options,
        )
        combo_thumb.currentIndexChanged.connect(change_thumb_time)
        capture_layout.addWidget(card_thumb)

        # Thumbnail decorative corner ornament (pick one, or none)
        def on_thumb_frame(index):
            ornament_id = combo_frame.itemData(index)
            update_thumbnail_frame(ornament_id, self.config_path)
            # A change only affects the *next* thumbnail shown; the currently
            # visible one was built with a fixed window size, so we can't
            # reshape it live.  No refresh needed - next capture picks it up.
        frame_options = [
            (self.translate("settings_ornament_off"), ""),
            (self.translate("settings_ornament_vine"), "vine"),
            (self.translate("settings_ornament_butterfly"), "butterfly"),
            (self.translate("settings_ornament_floral"), "floral"),
        ]
        card_frame, combo_frame = _make_language_card(
            self.translate("settings_thumbnail_frame_label"),
            self.translate("settings_thumbnail_frame_subtitle"),
            get_thumbnail_frame(self.config_path),
            frame_options,
        )
        combo_frame.currentIndexChanged.connect(on_thumb_frame)
        capture_layout.addWidget(card_frame)

        # Auto OCR after capture
        def on_auto_ocr(checked):
            update_auto_ocr_after_capture(checked, self.config_path)
            hint_clip.setVisible(checked)
        card_auto_ocr, switch_auto_ocr = _make_startup_card(
            self.translate("settings_auto_ocr_after_capture_label"),
            self.translate("settings_auto_ocr_after_capture_subtitle"),
            get_auto_ocr_after_capture(self.config_path),
        )
        switch_auto_ocr.clicked.connect(on_auto_ocr)
        capture_layout.addWidget(card_auto_ocr)

        hint_clip = QtWidgets.QLabel(self.translate("settings_auto_ocr_clipboard_hint"))

        # Show Size & Position label on the capture overlay
        def on_show_dim(checked):
            update_show_capture_dimension_label(checked, self.config_path)
            if self._on_dim_label_changed is not None:
                self._on_dim_label_changed(checked)
        card_dim, switch_dim = _make_startup_card(
            self.translate("settings_show_dimension_label"),
            self.translate("settings_show_dimension_subtitle"),
            get_show_capture_dimension_label(self.config_path),
        )
        switch_dim.clicked.connect(on_show_dim)
        capture_layout.addWidget(card_dim)
        hint_clip.setStyleSheet(
            "font-size:11px; color:#999; padding:2px 14px 0 14px;"
            "font-family:\"Microsoft YaHei\",\"Microsoft JhengHei\",sans-serif;"
        )
        hint_clip.setVisible(switch_auto_ocr.isChecked())
        capture_layout.addWidget(hint_clip)

        capture_layout.addStretch()
        content_stack.addWidget(capture_page)

        # ── Page: OCR ──────────────────────────────────────────
        ocr_page, ocr_layout = create_page()
        
        # Section: Recognition Engine
        ocr_layout.addWidget(QtWidgets.QLabel(self.translate("settings_section_ocr").upper()))

        # Font size
        def on_font_val(v):
            update_ocr_font_size(v)
            if self._on_font_size_changed: self._on_font_size_changed()
        
        card_font = QtWidgets.QFrame()
        card_font.setObjectName("settingCard")
        card_font.setStyleSheet(SETTING_CARD_STYLE)
        l_font = QtWidgets.QHBoxLayout(card_font)
        l_font.setContentsMargins(14, 10, 14, 10)
        v_font = QtWidgets.QVBoxLayout()
        label_font = QtWidgets.QLabel(self.translate("settings_ocr_font_size_label"))
        label_font.setObjectName("rowLabel")
        label_font.setStyleSheet(ROW_LABEL_STYLE)
        v_font.addWidget(label_font)
        sub_font = QtWidgets.QLabel(self.translate("settings_ocr_font_size_subtitle"))
        sub_font.setObjectName("subtitle")
        sub_font.setStyleSheet(SUBTITLE_STYLE)
        v_font.addWidget(sub_font)
        l_font.addLayout(v_font)
        l_font.addStretch()
        step = FontSizeStepper(initial_value=get_ocr_font_size())
        step.value_changed.connect(on_font_val)
        l_font.addWidget(step, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        ocr_layout.addWidget(card_font)

        # Close OCR popup on click outside
        def on_close_on_click_outside(checked):
            update_close_ocr_popup_on_focus_loss(checked, self.config_path)
        card_ocr_click_outside, switch_ocr_click_outside = _make_startup_card(
            self.translate("settings_close_ocr_on_click_outside_label"),
            self.translate("settings_close_ocr_on_click_outside_subtitle"),
            get_close_ocr_popup_on_focus_loss(self.config_path),
        )
        switch_ocr_click_outside.clicked.connect(on_close_on_click_outside)
        ocr_layout.addWidget(card_ocr_click_outside)

        ocr_layout.addStretch()
        content_stack.addWidget(ocr_page)

        # --- Sidebar Actions ---
        sidebar.addItem(self.translate("settings_section_general"))
        sidebar.addItem(self.translate("settings_section_capture"))
        sidebar.addItem(self.translate("settings_section_ocr"))
        sidebar.currentRowChanged.connect(content_stack.setCurrentIndex)
        sidebar.setCurrentRow(target_row)

        # --- Footer (Version) ---
        footer_layout = QtWidgets.QVBoxLayout()
        footer_layout.setContentsMargins(0, 0, 0, 8)
        version_label = QtWidgets.QLabel(f"HushSnap v{__version__}  ·  TCITA Studio")
        version_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet("font-size: 10px; color: #BBB; border: none; background: transparent; font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;")
        footer_layout.addWidget(version_label)
        
        # Add footer to content side
        content_layout = QtWidgets.QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(content_stack)
        content_layout.addLayout(footer_layout)
        
        # We need to wrap the sidebar and the new content_layout in a QHBoxLayout
        # The main_layout already contains sidebar and content_stack. 
        # Let's fix that.
        while main_layout.count():
            item = main_layout.takeAt(0)
            if item.widget(): item.widget().setParent(None)
        
        main_layout.addWidget(sidebar)
        container_right = QtWidgets.QWidget()
        container_right.setLayout(content_layout)
        main_layout.addWidget(container_right)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
