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
    mock_screen = MagicMock()
    mock_screen.geometry.return_value = QtCore.QRect(0, 0, 100, 100)

    # CaptureWindow covers the screen under the cursor (multi-monitor aware).
    with patch("PyQt6.QtWidgets.QApplication.screenAt", return_value=mock_screen), \
         patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=mock_screen):
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


def test_capture_window_clamps_drag_to_frozen_screen(qapp):
    """A drag straying past the frozen screen's edge is clamped to that edge.

    Multi-monitor edge case: pressing on the primary screen and releasing on a
    neighbouring monitor must not select pixels that were never captured.  The
    selection cursor is clamped to the frozen window's bounds, so the resulting
    rect stops at the edge and its size stays honest.
    """
    # 100x100 frozen screen at the origin, DPR 1.0 to keep the math transparent.
    screen_geo = QtCore.QRect(0, 0, 100, 100)
    mock_screen = MagicMock()
    mock_screen.geometry.return_value = screen_geo
    mock_screen.devicePixelRatio.return_value = 1.0

    pixmap = QtGui.QPixmap(100, 100)
    pixmap.fill(QtCore.Qt.GlobalColor.white)
    pixmap.setDevicePixelRatio(1.0)

    with patch("PyQt6.QtWidgets.QApplication.screenAt", return_value=mock_screen), \
         patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=mock_screen):
        win = CaptureWindow(pixmap)
        # Bounds are the LOCAL rect (0,0,w,h), not the global geometry —
        # event.position() is widget-local.
        assert win._selection_bounds == QtCore.QRect(0, 0, 100, 100)

    def mk_event(x, y):
        e = MagicMock()
        e.button.return_value = QtCore.Qt.MouseButton.LeftButton
        e.position.return_value = QtCore.QPointF(x, y)
        return e

    # Press near the right edge, drag well past it (onto a neighbour), release.
    win.mousePressEvent(mk_event(90, 50))
    win.mouseMoveEvent(mk_event(500, 500))
    assert win.curr_pos == QtCore.QPoint(99, 99)  # clamped to right/bottom edge

    with patch.object(win, "_set_clipboard_pixmap") as mock_set_clip:
        win.mouseReleaseEvent(mk_event(500, 500))

    args, _ = mock_set_clip.call_args
    clipped = args[0]
    # (90,50)-(99,99) → 10 wide, 50 tall, physical = logical at DPR 1.0
    assert clipped.width() == 10
    assert clipped.height() == 50
    win.close()


def test_capture_window_region_select_on_secondary_monitor(qapp):
    """Region drag-select works on a non-origin (secondary) monitor.

    Regression: _selection_bounds was set from self.geometry() (global desktop
    coords, e.g. x=2560 on a secondary screen) while event.position() is
    widget-local. The mismatch clamped every local point to the screen's
    global top-left, collapsing any drag into a click → only fullscreen
    capture worked on secondary monitors. Bounds must be the LOCAL rect.
    """
    # Secondary monitor: top-left at (2560, 0) in desktop coords, 200x150.
    screen_geo = QtCore.QRect(2560, 0, 200, 150)
    mock_screen = MagicMock()
    mock_screen.geometry.return_value = screen_geo
    mock_screen.devicePixelRatio.return_value = 1.0

    pixmap = QtGui.QPixmap(200, 150)
    pixmap.fill(QtCore.Qt.GlobalColor.white)
    pixmap.setDevicePixelRatio(1.0)

    with patch("PyQt6.QtWidgets.QApplication.screenAt", return_value=mock_screen), \
         patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=mock_screen):
        win = CaptureWindow(pixmap)
        # Local rect, NOT the global (2560,0,...) geometry.
        assert win._selection_bounds == QtCore.QRect(0, 0, 200, 150)

    def mk_event(x, y):
        e = MagicMock()
        e.button.return_value = QtCore.Qt.MouseButton.LeftButton
        e.position.return_value = QtCore.QPointF(x, y)
        return e

    # A genuine region drag in LOCAL coords (well past the click threshold).
    win.mousePressEvent(mk_event(20, 30))
    win.mouseMoveEvent(mk_event(120, 100))
    assert win.curr_pos == QtCore.QPoint(120, 100)  # not collapsed to (0,0)

    with patch.object(win, "_set_clipboard_pixmap") as mock_set_clip:
        win.mouseReleaseEvent(mk_event(120, 100))

    args, _ = mock_set_clip.call_args
    clipped = args[0]
    # (20,30)-(120,100) → 101 wide, 71 tall — a real region, not fullscreen.
    assert clipped.width() == 101
    assert clipped.height() == 71
    assert clipped.width() != 200  # would be 200 if mis-detected as a click
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
