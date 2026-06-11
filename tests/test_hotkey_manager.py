"""
Unit tests for HotkeyManager.
Covers registration, unregistration, resource release, and conflict handling.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from PyQt6 import QtWidgets

from hushsnap.system.hotkey_manager import HotkeyManager


@pytest.fixture
def qapp():
    """Fixture to provide a QApplication instance."""
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def mock_tray():
    """Fixture to mock QSystemTrayIcon."""
    return MagicMock()


@pytest.fixture
def mock_translate():
    """Fixture to mock translation function."""
    def _translate(key, **kwargs):
        if kwargs:
            return f"{key}_translated_{str(kwargs)}"
        return f"{key}_translated"
    return _translate


@pytest.fixture
def dummy_config_path(tmp_path):
    """Fixture to provide a temporary dummy config path."""
    return tmp_path / "config.toml"


@patch("ctypes.windll.kernel32.GlobalAddAtomW")
@patch("ctypes.windll.user32.RegisterHotKey")
def test_hotkey_manager_initialization(mock_register, mock_add_atom, mock_translate, dummy_config_path):
    """Test hotkey manager atom creation and value assignments."""
    mock_add_atom.return_value = 100

    mgr = HotkeyManager(
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1,
        virtual_key=65,
        name="Alt+A",
    )

    assert mgr.hotkey_id == 100
    assert mgr.current_hotkey_modifier == 1
    assert mgr.current_hotkey_virtual_key == 65
    assert mgr.current_hotkey_name == "Alt+A"


@patch("ctypes.windll.kernel32.GlobalAddAtomW")
@patch("ctypes.windll.user32.RegisterHotKey")
def test_register_initial_success(mock_register, mock_add_atom, mock_translate, dummy_config_path):
    """Verify that register_initial registers the hotkey with user32 successfully."""
    mock_add_atom.return_value = 100
    mock_register.return_value = True

    mgr = HotkeyManager(
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1,
        virtual_key=65,
        name="Alt+A",
    )

    success = mgr.register_initial()
    assert success is True
    assert mgr.hotkey_registered is True
    mock_register.assert_called_once_with(None, 100, 1, 65)


@patch("ctypes.windll.kernel32.GlobalAddAtomW")
@patch("ctypes.windll.user32.RegisterHotKey")
def test_register_initial_failure(mock_register, mock_add_atom, mock_translate, dummy_config_path):
    """Verify that register_initial records conflict without showing a dialog."""
    mock_add_atom.return_value = 100
    mock_register.return_value = False  # Simulate conflict

    mgr = HotkeyManager(
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1,
        virtual_key=65,
        name="Alt+A",
    )

    success = mgr.register_initial()
    assert success is False
    assert mgr.hotkey_registered is False
    assert mgr._startup_conflicts == [("main", "Alt+A")]


@patch("PyQt6.QtWidgets.QMessageBox.question")
def test_resolve_startup_conflicts_single_opens_settings(mock_question, mock_translate, dummy_config_path):
    """Single conflict → user clicks Yes → callback is invoked."""
    mock_question.return_value = QtWidgets.QMessageBox.StandardButton.Yes
    callback_called = []

    mgr = HotkeyManager(
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1, virtual_key=65, name="Alt+A",
    )
    mgr._startup_conflicts = [("main", "Alt+A")]

    mgr.resolve_startup_conflicts(lambda: callback_called.append(True))
    assert callback_called == [True]
    mock_question.assert_called_once()


@patch("PyQt6.QtWidgets.QMessageBox.question")
def test_resolve_startup_conflicts_ignores(mock_question, mock_translate, dummy_config_path):
    """Single conflict → user clicks No → callback is NOT invoked."""
    mock_question.return_value = QtWidgets.QMessageBox.StandardButton.No
    callback_called = []

    mgr = HotkeyManager(
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1, virtual_key=65, name="Alt+A",
    )
    mgr._startup_conflicts = [("main", "Alt+A")]

    mgr.resolve_startup_conflicts(lambda: callback_called.append(True))
    assert callback_called == []
    mock_question.assert_called_once()


@patch("PyQt6.QtWidgets.QMessageBox.question")
def test_resolve_startup_conflicts_multiple(mock_question, mock_translate, dummy_config_path):
    """Multiple conflicts → combined message → callback invoked on Yes."""
    mock_question.return_value = QtWidgets.QMessageBox.StandardButton.Yes
    callback_called = []

    mgr = HotkeyManager(
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1, virtual_key=65, name="Alt+Q",
    )
    mgr._startup_conflicts = [("main", "Alt+Q"), ("ocr", "Alt+Z")]

    mgr.resolve_startup_conflicts(lambda: callback_called.append(True))
    assert callback_called == [True]
    # Verify the message contains both hotkey names
    call_args = mock_question.call_args[0]
    assert "Alt+Q" in call_args[2]
    assert "Alt+Z" in call_args[2]


def test_resolve_startup_conflicts_no_conflicts(mock_translate, dummy_config_path):
    """No conflicts → no dialog, no callback."""
    callback_called = []

    mgr = HotkeyManager(
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1, virtual_key=65, name="Alt+Q",
    )
    # _startup_conflicts is empty by default
    mgr.resolve_startup_conflicts(lambda: callback_called.append(True))
    assert callback_called == []


@patch("ctypes.windll.kernel32.GlobalAddAtomW")
@patch("ctypes.windll.user32.UnregisterHotKey")
@patch("ctypes.windll.kernel32.GlobalDeleteAtom")
def test_release_resources(mock_delete_atom, mock_unregister, mock_add_atom, mock_translate, dummy_config_path):
    """Verify that release_resources unregisters hotkeys and deletes generated atoms."""
    mock_add_atom.return_value = 100
    mock_unregister.return_value = True

    mgr = HotkeyManager(
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1,
        virtual_key=65,
        name="Alt+A",
    )
    mgr.hotkey_registered = True

    mgr.release_resources()

    # Unregister should be called for hotkey ID
    mock_unregister.assert_called_once_with(None, 100)

    # Atoms should be deleted
    mock_delete_atom.assert_called_once_with(100)

    assert mgr.hotkey_registered is False

