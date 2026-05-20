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
def test_hotkey_manager_initialization(mock_register, mock_add_atom, mock_tray, mock_translate, dummy_config_path):
    """Test hotkey manager atom creation and value assignments."""
    mock_add_atom.side_effect = [100, 101]

    mgr = HotkeyManager(
        tray_icon=mock_tray,
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1,
        virtual_key=65,
        name="Alt+A",
        ocr_modifier=2,
        ocr_virtual_key=66,
        ocr_name="Ctrl+B",
    )

    assert mgr.hotkey_id == 100
    assert mgr.ocr_hotkey_id == 101
    assert mgr.current_hotkey_modifier == 1
    assert mgr.current_hotkey_virtual_key == 65
    assert mgr.current_hotkey_name == "Alt+A"
    assert mgr.current_ocr_hotkey_modifier == 2
    assert mgr.current_ocr_hotkey_virtual_key == 66
    assert mgr.current_ocr_hotkey_name == "Ctrl+B"


@patch("ctypes.windll.kernel32.GlobalAddAtomW")
@patch("ctypes.windll.user32.RegisterHotKey")
def test_register_initial_success(mock_register, mock_add_atom, mock_tray, mock_translate, dummy_config_path):
    """Verify that register_initial registers the hotkey with user32 successfully."""
    mock_add_atom.side_effect = [100, 101]
    mock_register.return_value = True

    mgr = HotkeyManager(
        tray_icon=mock_tray,
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
@patch("PyQt6.QtWidgets.QMessageBox.warning")
def test_register_initial_failure(mock_warning, mock_register, mock_add_atom, mock_tray, mock_translate, dummy_config_path):
    """Verify that register_initial handles OS conflict properly by raising a QMessageBox warning."""
    mock_add_atom.side_effect = [100, 101]
    mock_register.return_value = False  # Simulate conflict

    mgr = HotkeyManager(
        tray_icon=mock_tray,
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1,
        virtual_key=65,
        name="Alt+A",
    )

    success = mgr.register_initial()
    assert success is False
    assert mgr.hotkey_registered is False
    mock_warning.assert_called_once()
    assert "Alt+A" in mock_warning.call_args[0][2]


@patch("ctypes.windll.kernel32.GlobalAddAtomW")
@patch("ctypes.windll.user32.RegisterHotKey")
def test_register_ocr_initial_success(mock_register, mock_add_atom, mock_tray, mock_translate, dummy_config_path):
    """Verify that register_ocr_initial registers the OCR hotkey successfully."""
    mock_add_atom.side_effect = [100, 101]
    mock_register.return_value = True

    mgr = HotkeyManager(
        tray_icon=mock_tray,
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1,
        virtual_key=65,
        name="Alt+A",
        ocr_modifier=2,
        ocr_virtual_key=66,
        ocr_name="Ctrl+B",
    )

    success = mgr.register_ocr_initial()
    assert success is True
    assert mgr.ocr_hotkey_registered is True
    mock_register.assert_called_once_with(None, 101, 2, 66)


@patch("ctypes.windll.kernel32.GlobalAddAtomW")
@patch("ctypes.windll.user32.UnregisterHotKey")
@patch("ctypes.windll.kernel32.GlobalDeleteAtom")
def test_release_resources(mock_delete_atom, mock_unregister, mock_add_atom, mock_tray, mock_translate, dummy_config_path):
    """Verify that release_resources unregisters hotkeys and deletes generated atoms."""
    mock_add_atom.side_effect = [100, 101]
    mock_unregister.return_value = True

    mgr = HotkeyManager(
        tray_icon=mock_tray,
        translate=mock_translate,
        config_path=dummy_config_path,
        modifier=1,
        virtual_key=65,
        name="Alt+A",
        ocr_modifier=2,
        ocr_virtual_key=66,
        ocr_name="Ctrl+B",
    )
    mgr.hotkey_registered = True
    mgr.ocr_hotkey_registered = True

    mgr.release_resources()

    # Unregister should be called for both hotkey IDs
    mock_unregister.assert_has_calls([
        call(None, 100),
        call(None, 101),
    ], any_order=True)

    # Atoms should be deleted
    mock_delete_atom.assert_has_calls([
        call(100),
        call(101),
    ], any_order=True)

    assert mgr.hotkey_registered is False
    assert mgr.ocr_hotkey_registered is False
