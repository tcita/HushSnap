import pytest
import ctypes
from unittest.mock import patch, MagicMock
from hushsnap.system.win32_window_utils import get_hwnd_value, get_window_snapshot

def test_get_hwnd_value_int():
    """Verify that integer values are correctly returned as HWND values."""
    assert get_hwnd_value(123) == 123
    assert get_hwnd_value(0xABC) == 0xABC

def test_get_hwnd_value_none():
    """Verify that None is correctly handled as a null HWND (0)."""
    assert get_hwnd_value(None) == 0

class MockWinId:
    """Mock object that implements __index__ to simulate a Qt winId."""
    def __init__(self, val):
        self.val = val
    def __index__(self):
        return self.val

def test_get_hwnd_value_indexable():
    """Verify that objects implementing __index__ (like Qt winId) are handled correctly."""
    mock = MockWinId(456)
    assert get_hwnd_value(mock) == 456

def test_get_hwnd_value_ctypes():
    """Verify that ctypes pointer types are correctly converted to integer HWNDs."""
    val = 789
    ptr = ctypes.c_void_p(val)
    assert get_hwnd_value(ptr) == val

def test_get_hwnd_value_invalid():
    """Verify that invalid types return a null HWND (0) instead of raising an error."""
    assert get_hwnd_value("not an hwnd") == 0

def test_get_window_snapshot_invalid():
    """Verify that invalid/null hwnd returns fallback string."""
    assert get_window_snapshot(None) == "hwnd=0x0"
    assert get_window_snapshot("invalid") == "hwnd=0x0"

@patch("ctypes.windll.user32")
def test_get_window_snapshot_valid(mock_user32):
    """Test get_window_snapshot correctly calls user32 APIs for valid hwnd."""
    # Mock calls
    mock_user32.GetWindowThreadProcessId.return_value = 456
    mock_user32.GetWindowLongW.side_effect = [0x1234, 0x5678]  # GWL_STYLE, GWL_EXSTYLE
    mock_user32.GetWindowRect.return_value = 1
    
    def mock_get_class_name(h, buf, size):
        buf.value = "TestClass"
        return len(buf.value)
    mock_user32.GetClassNameW.side_effect = mock_get_class_name
    
    mock_user32.GetWindowTextLengthW.return_value = 9
    
    def mock_get_window_text(h, buf, size):
        buf.value = "TestTitle"
        return len(buf.value)
    mock_user32.GetWindowTextW.side_effect = mock_get_window_text
    
    mock_user32.IsWindowVisible.return_value = 1

    snapshot = get_window_snapshot(123)
    
    assert "hwnd=0x0000007B" in snapshot  # 123 is 0x7B
    assert "tid=456" in snapshot
    assert "class='TestClass'" in snapshot
    assert "title='TestTitle'" in snapshot
    assert "visible=1" in snapshot

