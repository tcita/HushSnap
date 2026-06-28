"""
HushSnap global hotkey listener module.
Uses a Qt native event filter to capture Windows-level WM_HOTKEY messages.
"""

from ctypes import wintypes
from PyQt6 import QtCore, QtWidgets

from .constants import WM_HOTKEY
from .dpi import grab_all_screens


import ctypes
import logging

logger = logging.getLogger(__name__)

WM_TASKBARCREATED = 0
if hasattr(ctypes, "windll"):
    try:
        WM_TASKBARCREATED = ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated")
    except Exception:
        logger.debug("hotkey: RegisterWindowMessageW(TaskbarCreated) failed", exc_info=True)


class HotkeyFilter(QtCore.QAbstractNativeEventFilter):
    """
    Native Windows event filter.
    Listens to system-broadcast messages and extracts hotkey activation events.
    Also handles Explorer crash/restart by listening to the 'TaskbarCreated' message.
    """
    def __init__(self, on_trigger, on_taskbar_created=None):
        """
        Initialize the filter.

        Args:
            on_trigger (callable): Callback for the screenshot hotkey.
            on_taskbar_created (callable): Callback when the Windows Explorer taskbar is recreated.
        """
        super().__init__()
        self.on_trigger = on_trigger
        self.on_taskbar_created = on_taskbar_created
        self.hotkey_id = None

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
                # Stress-test / crash-diagnostic marker: this is t=0 of a
                # capture round. Grep `[OCR_CHAIN]` to reconstruct the full
                # pipeline timeline and pinpoint where a crash halts.
                logger.debug("[OCR_CHAIN] hotkey WM_HOTKEY received")
                # Performance optimization: capture screen immediately in nativeEventFilter.
                # This runs before Qt's event queue, so the screenshot is effectively frozen
                # before the capture UI appears, reducing on-screen change interference.
                screens_and_pixmaps = grab_all_screens()
                if screens_and_pixmaps:
                    count = len(screens_and_pixmaps) if isinstance(screens_and_pixmaps, list) else 1
                    logger.debug("[OCR_CHAIN] hotkey grabbed %d screens", count)
                    self.on_trigger(screens_and_pixmaps)

                # Return True to stop propagation to other filters.
                return True, 0
            
            elif (
                WM_TASKBARCREATED != 0
                and message_struct.message == WM_TASKBARCREATED
                and self.on_taskbar_created is not None
            ):
                self.on_taskbar_created()
                # Do not return True so other native filters can also receive this message if they wish.
                
        return False, 0

