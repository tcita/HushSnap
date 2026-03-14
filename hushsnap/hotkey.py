from ctypes import wintypes
from PyQt6 import QtCore, QtWidgets

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
                # 性能优化：在 nativeEventFilter 中第一时间抓取屏幕
                # 这能确保在进入 Qt 事件循环队列之前，屏幕图像已经“冻结”
                screen = QtWidgets.QApplication.primaryScreen()
                if screen:
                    device_pixel_ratio = screen.devicePixelRatio()
                    # 抓取整个桌面
                    screen_pixmap = screen.grabWindow(0)
                    screen_pixmap.setDevicePixelRatio(device_pixel_ratio)
                    # 将抓取好的图像传给后续流程
                    self.trigger_signal.emit(screen_pixmap)
                return True, 0
        return False, 0


class Communicator(QtCore.QObject):
    # 修改信号，使其携带捕获到的 Pixmap
    trigger = QtCore.pyqtSignal(object)
