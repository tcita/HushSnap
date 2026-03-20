"""
HushSnap 全局热键监听模块
利用 Qt 的原生事件过滤器 (Native Event Filter) 捕获 Windows 系统级的 WM_HOTKEY 消息。
"""

from ctypes import wintypes
from PyQt6 import QtCore, QtWidgets

from .constants import WM_HOTKEY


class HotkeyFilter( ):
    """
    Windows 原生事件过滤器。
    专门用于监听系统广播的消息，并从中筛选出热键激活消息。
    """
    def __init__(self, trigger_signal):
        """
        初始化过滤器。
        
        Args:
            trigger_signal (QtCore.pyqtSignal): 热键触发时发射的信号。
        """
        super().__init__()
        self.trigger_signal = trigger_signal

    def nativeEventFilter(self, event_type, message):
        """
        过滤原生 Windows 事件。
        
        Args:
            event_type (bytes): 事件类型，Windows 下通常为 b"windows_generic_MSG"。
            message (sip.voidptr): 指向 Windows MSG 结构体的指针。
            
        Returns:
            tuple: (bool, int) 表示是否处理了该事件以及返回值。
        """
        if event_type == b"windows_generic_MSG":
            # 将消息指针转换为 Python 友好的 MSG 结构体
            message_struct = wintypes.MSG.from_address(int(message))
            if message_struct.message == WM_HOTKEY:
                # 性能优化：在 nativeEventFilter 中第一时间抓取屏幕
                # 因为 nativeEventFilter 的执行优先级高于 Qt 的事件队列，
                # 在这里截图能确保在显示截图界面前，屏幕图像已经“冻结”，减少画面变动的干扰。
                screen = QtWidgets.QApplication.primaryScreen()
                if screen:
                    device_pixel_ratio = screen.devicePixelRatio()
                    # 抓取整个桌面 (WId 0)
                    screen_pixmap = screen.grabWindow(0)
                    screen_pixmap.setDevicePixelRatio(device_pixel_ratio)
                    
                    # 将截图数据通过信号发送给 UI 线程(launch_capture_window)进行后续的选取处理
                    self.trigger_signal.emit(screen_pixmap)
                
                # 返回 True 表示该消息已处理，不再传递给其他过滤器
                return True, 0
        return False, 0


class Communicator(QtCore.QObject):
    """
    信号传输中转类。
    用于在原生过滤器和 Qt 窗口系统之间架起通信桥梁。
    """
    # 携带捕获到的 QPixmap 对象的信号
    trigger = QtCore.pyqtSignal(object)
