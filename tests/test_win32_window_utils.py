"""
Unit tests for Win32 window utility functions.
Tests HWND value extraction and conversion from various Python types.
"""

import pytest
import ctypes
from hushsnap.system.win32_window_utils import get_hwnd_value

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
