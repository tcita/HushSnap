"""
Unit tests for the hotkey module.
Tests the Windows native event filter for capturing global hotkeys.
"""

import pytest
from unittest.mock import MagicMock, patch
from ctypes import wintypes
from PyQt6 import QtCore, QtGui
from hushsnap import hotkey
from hushsnap.hotkey import HotkeyFilter, WM_HOTKEY

@pytest.fixture
def mock_app():
    """Fixture to mock the QApplication instance."""
    with patch("PyQt6.QtWidgets.QApplication") as mock:
        yield mock

def test_hotkey_filter_inheritance():
    """Verify that HotkeyFilter correctly inherits from QAbstractNativeEventFilter."""
    mock_callback = MagicMock()
    filter = HotkeyFilter(mock_callback)
    # This check ensures the class is compatible with installNativeEventFilter
    assert isinstance(filter, QtCore.QAbstractNativeEventFilter)

def test_hotkey_filter_ignore_other_events():
    """Verify that the filter ignores non-Windows/non-hotkey events."""
    mock_callback = MagicMock()
    filter = HotkeyFilter(mock_callback)
    
    # Test non-windows event
    handled, ret = filter.nativeEventFilter(b"other_event", None)
    assert handled is False
    mock_callback.assert_not_called()

@patch("ctypes.wintypes.MSG.from_address")
def test_hotkey_filter_handle_wm_hotkey(mock_from_address, mock_app):
    """Test handling of the WM_HOTKEY message and screenshot capture trigger."""
    mock_callback = MagicMock()
    filter = HotkeyFilter(mock_callback)

    # Mock MSG
    mock_msg = MagicMock()
    mock_msg.message = WM_HOTKEY
    mock_from_address.return_value = mock_msg

    # grab_full_screen()'s compositing is covered by test_dpi; here we only
    # verify the filter grabs and forwards the result to the callback.
    mock_pixmap = MagicMock()
    with patch.object(hotkey, "grab_full_screen", return_value=mock_pixmap):
        handled, ret = filter.nativeEventFilter(b"windows_generic_MSG", 12345)

    assert handled is True
    assert ret == 0
    mock_callback.assert_called_once_with(mock_pixmap)


@patch("ctypes.wintypes.MSG.from_address")
def test_hotkey_filter_handle_taskbar_created(mock_from_address, mock_app):
    """Test handling of the WM_TASKBARCREATED message and tray icon restoration."""
    mock_callback = MagicMock()
    filter = HotkeyFilter(on_trigger=MagicMock(), on_taskbar_created=mock_callback)
    
    # We patch WM_TASKBARCREATED to a non-zero test value
    with patch("hushsnap.hotkey.WM_TASKBARCREATED", 0xC003):
        # Mock MSG
        mock_msg = MagicMock()
        mock_msg.message = 0xC003
        mock_from_address.return_value = mock_msg
        
        # Simulate WM_TASKBARCREATED native event
        handled, ret = filter.nativeEventFilter(b"windows_generic_MSG", 12345)
        
        assert handled is False  # TaskbarCreated shouldn't block the message propagation
        assert ret == 0
        mock_callback.assert_called_once()

