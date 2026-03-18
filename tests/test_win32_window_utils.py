import pytest
import ctypes
from hushsnap.system.win32_window_utils import get_hwnd_value

def test_get_hwnd_value_int():
    assert get_hwnd_value(123) == 123
    assert get_hwnd_value(0xABC) == 0xABC

def test_get_hwnd_value_none():
    assert get_hwnd_value(None) == 0

class MockWinId:
    def __init__(self, val):
        self.val = val
    def __index__(self):
        return self.val

def test_get_hwnd_value_indexable():
    mock = MockWinId(456)
    assert get_hwnd_value(mock) == 456

def test_get_hwnd_value_ctypes():
    val = 789
    ptr = ctypes.c_void_p(val)
    assert get_hwnd_value(ptr) == val

def test_get_hwnd_value_invalid():
    assert get_hwnd_value("not an hwnd") == 0
