"""
Unit tests for hotkey capture in the settings dialog.

Covers two things:
- _qt_key_to_hotkey_token: which keys the capture box accepts as a hotkey's
  main key. Only letters, digits and function keys are accepted; everything
  else (Esc, Tab, Enter, Space, arrows, Backspace) returns None so the dialog
  silently ignores it — most apps don't bind those as shortcuts, so letting
  them through would only set up conflicts.
- _is_conflict_prone_hotkey / _hotkey_frame_state: which valid combos earn a
  soft amber warning (Ctrl+S, F1, Alt+Space, bare keys) vs. stay green.
"""

import pytest
from PyQt6 import QtCore

from hushsnap.constants import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN
from hushsnap.ui.settings_dialog import (
    _is_conflict_prone_hotkey,
    _hotkey_frame_state,
    _qt_key_to_hotkey_token,
)

# Common virtual-key codes used across cases.
VK_A = 0x41
VK_B = 0x42
VK_C = 0x43
VK_I = 0x49
VK_M = 0x4D
VK_Q = 0x51
VK_S = 0x53
VK_V = 0x56
VK_X = 0x58
VK_Z = 0x5A
VK_F1 = 0x70
VK_F4 = 0x73
VK_SPACE = 0x20
VK_5 = 0x35


# ═══════════════════════════════════════════════════════════════════════
# Conflict-prone (should warn)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("vk,label", [
    (VK_A, "Ctrl+A"),
    (VK_C, "Ctrl+C"),
    (VK_S, "Ctrl+S"),
    (VK_V, "Ctrl+V"),
    (VK_X, "Ctrl+X"),
    (VK_Z, "Ctrl+Z"),
])
def test_pure_ctrl_edit_op_is_flagged(vk, label):
    """Ctrl + canonical edit/file op (copy, save, undo …) collides with nearly
    every app and is flagged. Only near-universal shortcuts earn a warning."""
    assert _is_conflict_prone_hotkey(MOD_CONTROL, vk) is True


@pytest.mark.parametrize("vk,label", [
    (VK_Q, "Ctrl+Q"),
    (VK_M, "Ctrl+M"),
    (VK_B, "Ctrl+B"),
    (VK_I, "Ctrl+I"),
    (VK_5, "Ctrl+5"),
    (VK_F1, "Ctrl+F1"),
    (VK_F4, "Ctrl+F4"),
])
def test_pure_ctrl_minor_key_is_safe(vk, label):
    """Ctrl + keys that are app-specific or rare (Ctrl+Q/B/I/M, Ctrl+digits,
    Ctrl+F-keys) are NOT flagged — a soft warning should only nag on genuinely
    high-collision combos, not the long tail."""
    assert _is_conflict_prone_hotkey(MOD_CONTROL, vk) is False


def test_bare_function_key_is_flagged():
    """F1-F12 without modifiers shadow help/refresh/fullscreen keys."""
    assert _is_conflict_prone_hotkey(0, VK_F1) is True


def test_bare_letter_is_flagged():
    """A key with no modifier intercepts normal typing."""
    assert _is_conflict_prone_hotkey(0, VK_Q) is True


def test_bare_space_is_flagged():
    assert _is_conflict_prone_hotkey(0, VK_SPACE) is True


def test_alt_space_is_flagged():
    """Alt+Space is the window menu — shell-reserved."""
    assert _is_conflict_prone_hotkey(MOD_ALT, VK_SPACE) is True


def test_alt_f4_is_flagged():
    """Alt+F4 closes the active window — shell-reserved."""
    assert _is_conflict_prone_hotkey(MOD_ALT, VK_F4) is True


# ═══════════════════════════════════════════════════════════════════════
# Safe (should NOT warn)
# ═══════════════════════════════════════════════════════════════════════

def test_default_alt_q_is_safe():
    """The default hotkey lives in the safe Alt+letter region."""
    assert _is_conflict_prone_hotkey(MOD_ALT, VK_Q) is False


def test_alt_letter_is_safe():
    assert _is_conflict_prone_hotkey(MOD_ALT, VK_S) is False


def test_alt_shift_letter_is_safe():
    assert _is_conflict_prone_hotkey(MOD_ALT | MOD_SHIFT, VK_S) is False


def test_ctrl_shift_letter_is_safe():
    """Multi-modifier Ctrl combos (Ctrl+Shift+…) rarely collide."""
    assert _is_conflict_prone_hotkey(MOD_CONTROL | MOD_SHIFT, VK_S) is False


def test_ctrl_alt_letter_is_safe():
    assert _is_conflict_prone_hotkey(MOD_CONTROL | MOD_ALT, VK_S) is False


def test_win_letter_is_safe():
    """Win+letter is not flagged here — RegisterHotKey rejects Win combos
    (shell-reserved) and reports failure at register time, so the soft
    warning path is not the right signal for them."""
    assert _is_conflict_prone_hotkey(MOD_WIN, VK_Q) is False


# ═══════════════════════════════════════════════════════════════════════
# _hotkey_frame_state — drives the persistent settings-page pill tint
# ═══════════════════════════════════════════════════════════════════════

def test_frame_state_safe_for_default():
    assert _hotkey_frame_state("Alt+Q") == "safe"


def test_frame_state_warning_for_conflict_prone():
    """A risky binding tints the settings page amber so the reminder persists."""
    assert _hotkey_frame_state("Ctrl+S") == "warning"
    assert _hotkey_frame_state("F1") == "warning"


def test_frame_state_safe_on_parse_failure():
    """Unparseable input must not crash or falsely warn — fall back to safe."""
    assert _hotkey_frame_state("") == "safe"
    assert _hotkey_frame_state("garbage") == "safe"


# ═══════════════════════════════════════════════════════════════════════
# _qt_key_to_hotkey_token — which main keys the capture box accepts
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("key,expected", [
    (QtCore.Qt.Key.Key_A, "A"),
    (QtCore.Qt.Key.Key_Z, "Z"),
    (QtCore.Qt.Key.Key_0, "0"),
    (QtCore.Qt.Key.Key_9, "9"),
    (QtCore.Qt.Key.Key_F1, "F1"),
    (QtCore.Qt.Key.Key_F12, "F12"),
])
def test_token_accepts_letter_digit_function(key, expected):
    """Letters, digits and function keys are the universal hotkey vocabulary."""
    assert _qt_key_to_hotkey_token(key) == expected


@pytest.mark.parametrize("key", [
    QtCore.Qt.Key.Key_Escape,
    QtCore.Qt.Key.Key_Tab,
    QtCore.Qt.Key.Key_Enter,
    QtCore.Qt.Key.Key_Return,
    QtCore.Qt.Key.Key_Space,
    QtCore.Qt.Key.Key_Backspace,
    QtCore.Qt.Key.Key_Left,
    QtCore.Qt.Key.Key_Up,
    QtCore.Qt.Key.Key_Right,
    QtCore.Qt.Key.Key_Down,
    QtCore.Qt.Key.Key_Insert,
    QtCore.Qt.Key.Key_Delete,
    QtCore.Qt.Key.Key_Home,
    QtCore.Qt.Key.Key_End,
    QtCore.Qt.Key.Key_PageUp,
    QtCore.Qt.Key.Key_PageDown,
])
def test_token_rejects_non_shortcut_keys(key):
    """Esc/Tab/Enter/Space/arrows/Backspace/nav keys return None so the capture
    dialog silently ignores them — they aren't used as hotkey main keys and
    accepting them would only create conflicts. The user presses one, nothing
    happens, and they reach for a normal key."""
    assert _qt_key_to_hotkey_token(key) is None
