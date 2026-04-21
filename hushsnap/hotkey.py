"""
HushSnap global hotkey listener module.
Uses a Qt native event filter to capture Windows-level WM_HOTKEY messages.
"""

from ctypes import wintypes
from PyQt6 import QtCore, QtWidgets

from .constants import WM_HOTKEY


class HotkeyFilter(QtCore.QAbstractNativeEventFilter):
    """
    Native Windows event filter.
    Listens to system-broadcast messages and extracts hotkey activation events.
    """
    def __init__(self, on_trigger):
        """
        Initialize the filter.
        
        Args:
            on_trigger (callable): Callback invoked with the captured screen pixmap.
        """
        super().__init__()
        self.on_trigger = on_trigger

    def nativeEventFilter(self, event_type, message):
        """
        Filter native Windows events.
        
        Args:
            event_type (bytes): Event type, usually b"windows_generic_MSG" on Windows.
            message (sip.voidptr): Pointer to a Windows MSG struct.
            
        Returns:
            tuple: (bool, int) indicating whether the event was handled and the return value.
        """
        if event_type == b"windows_generic_MSG":
            # Convert message pointer to a Python-friendly MSG struct.
            message_struct = wintypes.MSG.from_address(int(message))
            if message_struct.message == WM_HOTKEY:
                # Performance optimization: capture screen immediately in nativeEventFilter.
                # This runs before Qt's event queue, so the screenshot is effectively frozen
                # before the capture UI appears, reducing on-screen change interference.
                screen = QtWidgets.QApplication.primaryScreen()
                if screen:
                    device_pixel_ratio = screen.devicePixelRatio()
                    # Grab the entire desktop (WId 0).
                    screen_pixmap = screen.grabWindow(0)
                    screen_pixmap.setDevicePixelRatio(device_pixel_ratio)
                    
                    # Forward screenshot data to the UI thread callback.
                    self.on_trigger(screen_pixmap)
                
                # Return True to stop propagation to other filters.
                return True, 0
        return False, 0
