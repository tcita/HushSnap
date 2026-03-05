from ctypes import wintypes

from PyQt6 import QtCore

from .constants import WM_HOTKEY


# --- 2. 原生事件过滤器：监听全局热键 ---
class HotkeyFilter(QtCore.QAbstractNativeEventFilter):
    def __init__(self, trigger_signal):
        super().__init__()
        self.trigger_signal = trigger_signal

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            message_struct = wintypes.MSG.from_address(int(message))
            if message_struct.message == WM_HOTKEY:
                self.trigger_signal.emit()
                return True, 0
        return False, 0


class Communicator(QtCore.QObject):
    trigger = QtCore.pyqtSignal()