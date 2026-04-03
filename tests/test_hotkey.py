"""
Unit tests for the hotkey module.
Tests the Windows native event filter for capturing global hotkeys.
"""

import pytest
from unittest.mock import MagicMock, patch
from ctypes import wintypes
from PyQt6 import QtCore
from hushsnap.hotkey import HotkeyFilter, WM_HOTKEY

@pytest.fixture
def mock_app():
    """Fixture to mock the QApplication instance."""
    with patch("PyQt6.QtWidgets.QApplication") as mock:
        yield mock

def test_hotkey_filter_inheritance():
    """Verify that HotkeyFilter correctly inherits from QAbstractNativeEventFilter."""
    mock_signal = MagicMock()
    filter = HotkeyFilter(mock_signal)
    # This check ensures the class is compatible with installNativeEventFilter
    assert isinstance(filter, QtCore.QAbstractNativeEventFilter)

def test_hotkey_filter_ignore_other_events():
    """Verify that the filter ignores non-Windows/non-hotkey events."""
    mock_signal = MagicMock()
    filter = HotkeyFilter(mock_signal)
    
    # Test non-windows event
    handled, ret = filter.nativeEventFilter(b"other_event", None)
    assert handled is False
    mock_signal.emit.assert_not_called()

@patch("ctypes.wintypes.MSG.from_address")
def test_hotkey_filter_handle_wm_hotkey(mock_from_address, mock_app):
    """Test handling of the WM_HOTKEY message and screenshot capture trigger."""
    mock_signal = MagicMock()
    filter = HotkeyFilter(mock_signal)
    
    # Mock MSG
    mock_msg = MagicMock()
    mock_msg.message = WM_HOTKEY
    mock_from_address.return_value = mock_msg
    
    # Mock Screen
    mock_screen = MagicMock()
    mock_pixmap = MagicMock()
    mock_screen.grabWindow.return_value = mock_pixmap
    mock_screen.devicePixelRatio.return_value = 1.0
    mock_app.primaryScreen.return_value = mock_screen
    
    # Simulate WM_HOTKEY
    handled, ret = filter.nativeEventFilter(b"windows_generic_MSG", 12345)
    
    assert handled is True
    assert ret == 0
    mock_screen.grabWindow.assert_called_once_with(0)
    mock_pixmap.setDevicePixelRatio.assert_called_once_with(1.0)
    mock_signal.emit.assert_called_once_with(mock_pixmap)
