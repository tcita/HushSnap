"""
截图交互界面模块。
负责全屏遮罩展示、鼠标拖拽选区绘制以及最终的图像裁剪与剪贴板写入。
"""

import ctypes
import logging
import sys
import traceback
from ctypes import wintypes

from PyQt6 import QtCore, QtGui, QtWidgets

from .constants import (
    CAPTURE_CLICK_THRESHOLD_PX,
    CAPTURE_OVERLAY_RGBA,
    CAPTURE_SELECTION_MIN_PX,
    DEBUG_TOPMOST_DELAY_MS,
)
from .logging_config import get_logger
from .system.win32_window_utils import (
    get_hwnd_value,
    get_window_snapshot,
)

logger = get_logger(__name__)


class CaptureWindow(QtWidgets.QWidget):
    """
    全屏截图窗口类。
    工作流程：
    1. 接收全屏位图作为背景。
    2. 设置为无边框全屏置顶窗口。
    3. 响应鼠标事件：
       - 左键点击拖动：创建选区。
       - 左键单纯点击：全屏截图。
       - 右键/Esc：退出。
    """
    def __init__(self, pixmap):
        super().__init__()
        self.pixmap = pixmap

        # 设置窗口属性：工具窗口样式、无边框、初始置顶标志
        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

        # 铺满主屏幕
        self.setWindowState(QtCore.Qt.WindowState.WindowFullScreen)
        screen = QtWidgets.QApplication.primaryScreen()
        self.setGeometry(screen.geometry())

        # 设置交互参数
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.start_pos = None
        self.curr_pos = None
        self.click_threshold = CAPTURE_CLICK_THRESHOLD_PX

        self._topmost_debug_seq = 0

    def _debug_topmost_state(self, stage, extra=""):
        """审计置顶状态。只有在 DEBUG 级别下才会执行耗时的 Win32 快照抓取。"""
        if not logger.isEnabledFor(logging.DEBUG) or sys.platform != "win32":
            return

        try:
            user32 = ctypes.windll.user32
            self_hwnd = self.winId()
            fg_hwnd = user32.GetForegroundWindow()

            self._topmost_debug_seq += 1
            msg = (
                f"seq={self._topmost_debug_seq},stage={stage},"
                f"self=[{get_window_snapshot(self_hwnd)}],"
                f"fg=[{get_window_snapshot(fg_hwnd)}]"
            )
            if extra: msg += f", {extra}"
            logger.debug(f"topmost_audit | {msg}")
        except Exception:
            logger.debug(f"topmost_audit_err | {traceback.format_exc().strip()}")

    def showEvent(self, event):
        """窗口显示事件：异步触发 Win32 抢占焦点逻辑，并设置安全自毁定时器。"""
        super().showEvent(event)
        # 将所有置顶与焦点抢占逻辑移至异步队列，确保窗口句柄完全就绪后再执行
        QtCore.QTimer.singleShot(0, self._force_win_topmost)

        # 安全快门：25秒后自动关闭窗口，防止程序挂起导致遮罩无法移除
        QtCore.QTimer.singleShot(25000, self.close)
        
        # 单线程异步审计，开启debug等级的日志后处理极端情况
        if logger.isEnabledFor(logging.DEBUG):
            QtCore.QTimer.singleShot(
                DEBUG_TOPMOST_DELAY_MS,
                lambda: self._debug_topmost_state(f"post_show_{DEBUG_TOPMOST_DELAY_MS}ms"),
            )

    def _force_win_topmost(self):
        """
        置顶逻辑（渐进式）：
        1. 基础置顶 (HWND_TOPMOST) + 焦点转移尝试。
        2. 验证焦点状态。
        3. 响应性检查：确认目标窗口未死锁后再进行线程挂载 (AttachThreadInput)。
        """
        if sys.platform != "win32": return
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            
            # --- 严格声明 API 签名：防止 64 位系统下的句柄截断 ---
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.IsHungAppWindow.argtypes = [wintypes.HWND]
            user32.IsHungAppWindow.restype = wintypes.BOOL
            
            # SendMessageTimeoutW: 同步取消模式，设置 200ms 超时防止死锁 (WM_CANCELMODE=0x1F, SMTO_ABORTIFHUNG=0x2)
            user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(wintypes.DWORD)]
            user32.SendMessageTimeoutW.restype = wintypes.LPARAM
            
            user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
            user32.AttachThreadInput.restype = wintypes.BOOL
            
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetActiveWindow.argtypes = [wintypes.HWND]
            user32.SetFocus.argtypes = [wintypes.HWND]
            
            hwnd = get_hwnd_value(self.winId())
            if not hwnd: return
            
            # --- 阶段 1: 基础抢占 (非入侵) ---
            fg_hwnd = user32.GetForegroundWindow()
            # 注意：ctypes 比较 HWND 时应比较其值
            if fg_hwnd and fg_hwnd != hwnd:
                unused_res = wintypes.DWORD()
                user32.SendMessageTimeoutW(fg_hwnd, 0x001F, 0, 0, 0x0002, 200, ctypes.byref(unused_res))
            
            # 基础视觉置顶 (HWND_TOPMOST = -1, SW_SHOW = 5)
            # SWP_SHOWWINDOW=0x40, SWP_NOMOVE=0x2, SWP_NOSIZE=0x1
            user32.ShowWindow(hwnd, 5)
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0043)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetFocus(hwnd)

            # --- 阶段 2: 验证与健康检查 ---
            if user32.GetForegroundWindow() == hwnd:
                if logger.isEnabledFor(logging.DEBUG):
                    self._debug_topmost_state("force_complete_soft")
                return

            if not fg_hwnd: return
            
            # 检查原前台窗口是否已死锁，防止挂载后同步卡死
            if user32.IsHungAppWindow(fg_hwnd):
                logger.warning(f"topmost_warn | Target window {fg_hwnd} is HUNG. Skipping AttachThreadInput.")
                return

            # --- 阶段 3: 入侵式强制挂载 (fallback) ---
            curr_tid = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
            
            attached = False
            if fg_tid and fg_tid != curr_tid:
                try:
                    attached = bool(user32.AttachThreadInput(curr_tid, fg_tid, True))
                except Exception as e:
                    logger.debug(f"topmost_warn | AttachThreadInput failed: {e}")

            try:
                if attached:
                    user32.SetForegroundWindow(hwnd)
                    user32.SetActiveWindow(hwnd)
                    user32.SetFocus(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(curr_tid, fg_tid, False)

            if logger.isEnabledFor(logging.DEBUG):
                self._debug_topmost_state("force_complete_hard")
        except Exception:
            logger.error(f"topmost_err | {traceback.format_exc().strip()}")

    def _set_clipboard_pixmap(self, pixmap, scene):
        """将生成的图片写入系统剪贴板。"""
        try:
            if pixmap.isNull():
                logger.error(f"clip_err | scene={scene}, reason=null")
                return False

            cb = QtWidgets.QApplication.clipboard()
            cb.setPixmap(pixmap, mode=cb.Mode.Clipboard)
            
            # 兼容性处理：如果 setPixmap 没立即生效，尝试强制处理一次事件循环
            if cb.pixmap(mode=cb.Mode.Clipboard).isNull():
                QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            
            if not cb.pixmap(mode=cb.Mode.Clipboard).isNull():
                return True

            # 备选方案：尝试以 Image 格式写入
            cb.setImage(pixmap.toImage(), mode=cb.Mode.Clipboard)
            success = not cb.image(mode=cb.Mode.Clipboard).isNull()
            if not success:
                logger.error(f"clip_failed | scene={scene}")
            return success
        except Exception:
            logger.error(f"clip_exc | scene={scene}, trace={traceback.format_exc().strip()}")
            return False

    def paintEvent(self, event):
        """绘制事件：渲染全屏背景、半透明遮罩和选区高亮。"""
        painter = QtGui.QPainter(self)
        # 1. 绘制底层全屏背景
        painter.drawPixmap(self.rect(), self.pixmap)
        # 2. 填充半透明灰色遮罩
        painter.fillRect(self.rect(), QtGui.QColor(*CAPTURE_OVERLAY_RGBA))

        # 3. 如果正在拖拽选区，绘制“挖洞”效果（即显示选区内的原始背景）
        if self.start_pos and self.curr_pos:
            rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
            if rect.width() >= CAPTURE_SELECTION_MIN_PX and rect.height() >= CAPTURE_SELECTION_MIN_PX:
                painter.save()
                painter.setClipRect(rect)
                painter.drawPixmap(self.rect(), self.pixmap)
                painter.restore()
                # 绘制深橙色选区边框
                painter.setPen(QtGui.QPen(QtGui.QColor("#FF8C00"), 2))
                painter.drawRect(rect)

    def mousePressEvent(self, event):
        """鼠标按下：记录起始点或关闭窗口。"""
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.close()
        elif event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.start_pos = event.position().toPoint()
            self.curr_pos = self.start_pos

    def mouseMoveEvent(self, event):
        """鼠标移动：更新选区坐标并触发重绘。"""
        if self.start_pos:
            self.curr_pos = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        """鼠标释放：根据拖动距离判断是选区截图还是全屏截图。"""
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.start_pos:
            self.curr_pos = event.position().toPoint()
            rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
            
            # 如果移动距离过小，视为单纯点击 -> 全屏截图
            if (self.curr_pos - self.start_pos).manhattanLength() <= self.click_threshold:
                full = self.pixmap.copy()
                full.setDevicePixelRatio(self.pixmap.devicePixelRatio())
                self._set_clipboard_pixmap(full, "fullscreen")
            else:
                # 选区截图：根据屏幕缩放比例转换逻辑坐标为实际像素坐标
                ratio = self.pixmap.devicePixelRatio()
                physical = QtCore.QRect(
                    int(rect.x() * ratio), int(rect.y() * ratio),
                    int(rect.width() * ratio), int(rect.height() * ratio)
                )
                final = self.pixmap.copy(physical)
                final.setDevicePixelRatio(ratio)
                self._set_clipboard_pixmap(final, "region")

            self.start_pos = self.curr_pos = None
            self.close()

    def keyPressEvent(self, event):
        """按键响应：Esc 退出截图。"""
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()
