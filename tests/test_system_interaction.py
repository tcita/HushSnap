"""
Unit tests for system-level interactions and the capture window.
Includes tests for window behavior, DPI scaling, and clipboard interaction.
"""

import pytest
from unittest.mock import MagicMock, patch
from PyQt6 import QtCore, QtGui, QtWidgets
from hushsnap.capture_window import CaptureWindow
import ctypes

@pytest.fixture
def qapp():
    """Fixture to provide a QApplication instance."""
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app

@pytest.fixture
def mock_pixmap():
    """Fixture to provide a mock QPixmap for testing."""
    pixmap = QtGui.QPixmap(100, 100)
    pixmap.fill(QtCore.Qt.GlobalColor.white)
    pixmap.setDevicePixelRatio(2.0) # Simulate High DPI
    return pixmap

def test_capture_window_initialization(qapp, mock_pixmap):
    """Test proper initialization of the CaptureWindow widget."""
    with patch("PyQt6.QtWidgets.QApplication.primaryScreen") as mock_screen:
        mock_screen.return_value.geometry.return_value = QtCore.QRect(0, 0, 100, 100)
        
        win = CaptureWindow(mock_pixmap)
        
        assert win.windowFlags() & QtCore.Qt.WindowType.WindowStaysOnTopHint
        assert win.windowFlags() & QtCore.Qt.WindowType.FramelessWindowHint
        assert win.geometry() == QtCore.QRect(0, 0, 100, 100)
        win.close()

@patch("ctypes.windll.user32.GetForegroundWindow")
@patch("ctypes.windll.user32.SetForegroundWindow")
@patch("ctypes.windll.user32.AttachThreadInput")
@patch("ctypes.windll.kernel32.GetCurrentThreadId")
@patch("ctypes.windll.user32.GetWindowThreadProcessId")
@patch("ctypes.windll.user32.IsHungAppWindow")
def test_capture_window_force_topmost_logic(
    mock_is_hung,
    mock_get_thread_id, 
    mock_get_curr_id, 
    mock_attach, 
    mock_set_fg, 
    mock_get_fg, 
    qapp, 
    mock_pixmap
):
    """Test the complex logic used to force the capture window to the foreground."""
    # Setup: Foreground is SOME OTHER window (0x999)
    # Our window ID will be something else
    mock_get_fg.return_value = 0x999 
    mock_get_curr_id.return_value = 100
    mock_get_thread_id.return_value = 200 # Other thread
    mock_is_hung.return_value = False
    mock_attach.return_value = True
    
    win = CaptureWindow(mock_pixmap)
    
    # We need to mock winId to return a stable value for comparison
    with patch.object(win, "winId", return_value=0x123):
        # Trigger the async logic
        win._force_win_topmost()
        
        # Verify AttachThreadInput was called with (current_tid, target_tid, True)
        mock_attach.assert_any_call(100, 200, True)
        # Verify SetForegroundWindow was called for our window
        mock_set_fg.assert_called()
        # Verify AttachThreadInput was detached (False)
        mock_attach.assert_any_call(100, 200, False)
    
    win.close()

@patch("ctypes.windll.user32.GetForegroundWindow")
@patch("ctypes.windll.user32.IsHungAppWindow")
@patch("ctypes.windll.user32.AttachThreadInput")
def test_capture_window_force_topmost_hung(
    mock_attach,
    mock_is_hung,
    mock_get_fg,
    qapp,
    mock_pixmap
):
    """Verify that thread attachment is skipped if the target window is hung."""
    # Scenario: Target window is HUNG
    mock_get_fg.return_value = 0x999
    mock_is_hung.return_value = True
    
    win = CaptureWindow(mock_pixmap)
    with patch.object(win, "winId", return_value=0x123):
        win._force_win_topmost()
        
        # Should NOT call AttachThreadInput if window is hung
        mock_attach.assert_not_called()
    
    win.close()

@patch("ctypes.windll.user32.GetForegroundWindow")
@patch("ctypes.windll.user32.IsHungAppWindow")
@patch("ctypes.windll.user32.AttachThreadInput")
@patch("ctypes.windll.kernel32.GetCurrentThreadId")
@patch("ctypes.windll.user32.GetWindowThreadProcessId")
@patch("ctypes.windll.user32.SetForegroundWindow")
def test_capture_window_force_topmost_attach_fail(
    mock_set_fg,
    mock_get_thread_id,
    mock_get_curr_id,
    mock_attach,
    mock_is_hung,
    mock_get_fg,
    qapp,
    mock_pixmap
):
    """Verify behavior when AttachThreadInput fails."""
    # Scenario: AttachThreadInput fails (e.g., high-privilege window)
    # 1st call (Stage 1): returns 0x999
    # 2nd call (Stage 2): returns 0x999 (meaning soft attempt failed)
    mock_get_fg.side_effect = [0x999, 0x999]
    mock_is_hung.return_value = False
    mock_get_curr_id.return_value = 100
    mock_get_thread_id.return_value = 200
    mock_attach.return_value = False # Fails
    
    win = CaptureWindow(mock_pixmap)
    with patch.object(win, "winId", return_value=0x123):
        win._force_win_topmost()
        
        # Attach was attempted
        mock_attach.assert_called_with(100, 200, True)
        
        # In Stage 1, SetForegroundWindow is called once.
        # In Stage 3, it should NOT be called because attached is False.
        # Total calls should be 1.
        assert mock_set_fg.call_count == 1
        
        # Verify detach was NOT called
        assert mock_attach.call_count == 1
    
    win.close()

def test_capture_window_dpi_scaling_clip(qapp, mock_pixmap):
    """Test that clipping logic correctly handles High DPI scaling."""
    # Pixmap is 100x100 with devicePixelRatio=2.0
    # Logical size is 50x50
    win = CaptureWindow(mock_pixmap)
    
    # Simulate a mouse drag from (10, 10) to (30, 30) in logical coordinates
    win.start_pos = QtCore.QPoint(10, 10)
    
    with patch.object(win, "_set_clipboard_pixmap") as mock_set_clip:
        # Create a mock event at (30, 30)
        event = MagicMock()
        event.button.return_value = QtCore.Qt.MouseButton.LeftButton
        event.position.return_value = QtCore.QPointF(30, 30)
        
        win.mouseReleaseEvent(event)
        
        # Verify that the clipped pixmap has the correct physical size
        # Logical (10,10,30,30) inclusive is width=21, height=21
        # Physical (21*2.0) = 42
        args, _ = mock_set_clip.call_args
        clipped_pixmap = args[0]
        
        assert clipped_pixmap.width() == 42
        assert clipped_pixmap.height() == 42
        assert clipped_pixmap.devicePixelRatio() == 2.0
    
    win.close()

def test_clipboard_fallback_logic(qapp, mock_pixmap):
    """Test fallback logic when setting the clipboard pixmap fails."""
    win = CaptureWindow(mock_pixmap)
    
    mock_clipboard = MagicMock()
    # Simulate primary setPixmap failing (returning null pixmap from clipboard)
    mock_clipboard.pixmap.return_value.isNull.return_value = True
    # Simulate secondary setImage failing too
    mock_clipboard.image.return_value.isNull.return_value = True
    
    with patch("PyQt6.QtWidgets.QApplication.clipboard", return_value=mock_clipboard):
        success = win._set_clipboard_pixmap(mock_pixmap, "test")
        assert success is False
        # Verify both setPixmap and setImage were attempted
        mock_clipboard.setPixmap.assert_called()
        mock_clipboard.setImage.assert_called()
    
    win.close()
