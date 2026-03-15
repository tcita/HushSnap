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


# 截图窗口逻辑
class CaptureWindow(QtWidgets.QWidget):
    def __init__(self, pixmap):
        super().__init__()
        self.pixmap = pixmap

        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.setWindowState(QtCore.Qt.WindowState.WindowFullScreen)
        screen = QtWidgets.QApplication.primaryScreen()
        self.setGeometry(screen.geometry())

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
        super().showEvent(event)
        # 核心：将所有置顶与焦点抢占逻辑移至异步队列，确保窗口句柄完全就绪后再执行
        QtCore.QTimer.singleShot(0, self._force_win_topmost)
        
        # 异步审计，处理某些极端情况
        if logger.isEnabledFor(logging.DEBUG):
            QtCore.QTimer.singleShot(
                DEBUG_TOPMOST_DELAY_MS,
                lambda: self._debug_topmost_state(f"post_show_{DEBUG_TOPMOST_DELAY_MS}ms"),
            )

    def _force_win_topmost(self):
        """最强置顶逻辑：整合了强制取消前台模式、线程输入挂载和硬件级置顶。"""
        if sys.platform != "win32": return
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd_val = get_hwnd_value(self.winId())
            if not hwnd_val: return

            hwnd = wintypes.HWND(hwnd_val)
            fg_hwnd = user32.GetForegroundWindow()
            
            # 1. 核心：如果当前焦点不是我，强制对方退出菜单/取消模式（解决开始菜单、右键菜单抢占问题）
            if fg_hwnd and get_hwnd_value(fg_hwnd) != hwnd_val:
                user32.PostMessageW(fg_hwnd, 0x001F, 0, 0) # WM_CANCELMODE

            # 2. 挂载线程输入，绕过 Windows 对 SetForegroundWindow 的严格限制
            curr_tid = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            attached = False
            if fg_tid and fg_tid != curr_tid:
                attached = bool(user32.AttachThreadInput(curr_tid, fg_tid, True))

            try:
                # 3. 硬件级置顶与焦点设置
                # HWND_TOPMOST (-1)
                # SWP_NOSIZE(0x0001) | SWP_NOMOVE(0x0002) | SWP_SHOWWINDOW(0x0040)
                user32.ShowWindow(hwnd, 5) # SW_SHOW
                user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0040 | 0x0002 | 0x0001)
                user32.SetForegroundWindow(hwnd)
                user32.SetActiveWindow(hwnd)
                user32.SetFocus(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(curr_tid, fg_tid, False)
            
            if logger.isEnabledFor(logging.DEBUG):
                self._debug_topmost_state("force_complete")
        except Exception:
            logger.error(f"topmost_force_err | {traceback.format_exc().strip()}")

    def _set_clipboard_pixmap(self, pixmap, scene):
        """写入剪贴板。"""
        try:
            if pixmap.isNull():
                logger.error(f"clip_err | scene={scene}, reason=null")
                return False

            cb = QtWidgets.QApplication.clipboard()
            cb.setPixmap(pixmap, mode=cb.Mode.Clipboard)
            
            if cb.pixmap(mode=cb.Mode.Clipboard).isNull():
                QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            
            if not cb.pixmap(mode=cb.Mode.Clipboard).isNull():
                return True

            cb.setImage(pixmap.toImage(), mode=cb.Mode.Clipboard)
            success = not cb.image(mode=cb.Mode.Clipboard).isNull()
            if not success:
                logger.error(f"clip_failed | scene={scene}")
            return success
        except Exception:
            logger.error(f"clip_exc | scene={scene}, trace={traceback.format_exc().strip()}")
            return False

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.drawPixmap(self.rect(), self.pixmap)
        painter.fillRect(self.rect(), QtGui.QColor(*CAPTURE_OVERLAY_RGBA))

        if self.start_pos and self.curr_pos:
            rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
            if rect.width() >= CAPTURE_SELECTION_MIN_PX and rect.height() >= CAPTURE_SELECTION_MIN_PX:
                painter.save()
                painter.setClipRect(rect)
                painter.drawPixmap(self.rect(), self.pixmap)
                painter.restore()
                painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.cyan, 2))
                painter.drawRect(rect)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.close()
        elif event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.start_pos = event.position().toPoint()
            self.curr_pos = self.start_pos

    def mouseMoveEvent(self, event):
        if self.start_pos:
            self.curr_pos = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.start_pos:
            self.curr_pos = event.position().toPoint()
            rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
            
            if (self.curr_pos - self.start_pos).manhattanLength() <= self.click_threshold:
                full = self.pixmap.copy()
                full.setDevicePixelRatio(self.pixmap.devicePixelRatio())
                self._set_clipboard_pixmap(full, "fullscreen")
            else:
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
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()
