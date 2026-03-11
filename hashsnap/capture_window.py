import ctypes
import os
import sys
import traceback
from ctypes import wintypes
from datetime import datetime

from PyQt6 import QtCore, QtGui, QtWidgets

from .config import get_app_dir
from .constants import (
    CAPTURE_CLICK_THRESHOLD_PX,
    CAPTURE_DEBUG_LOG_FILENAME,
    CAPTURE_LOG_TS_FMT,
    CAPTURE_OVERLAY_RGBA,
    CAPTURE_SELECTION_MIN_PX,
    DEBUG_TOPMOST_DELAY_MS,
)
from .logging_config import (
    DEBUG_TOPMOST_ENV,
    DEFAULT_LOG_MODE,
    LOG_MODE_DEBUG,
    LOG_MODE_ENV,
    LOG_MODE_LIGHT,
)


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
        # 显式覆盖所有屏幕区域
        screen = QtWidgets.QApplication.primaryScreen()
        self.setGeometry(screen.geometry())

        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.start_pos = None
        self.curr_pos = None
        self.click_threshold = CAPTURE_CLICK_THRESHOLD_PX
        # Keep logs next to installed executable for stable post-install debugging.
        self.log_path = get_app_dir() / CAPTURE_DEBUG_LOG_FILENAME

        # 日志模式：默认轻量；设置 HASHSNAP_LOG_MODE=debug 开启详细调试日志。
        raw_log_mode = os.environ.get(LOG_MODE_ENV, DEFAULT_LOG_MODE).strip().lower()
        self.log_mode = raw_log_mode if raw_log_mode in {LOG_MODE_LIGHT, LOG_MODE_DEBUG} else DEFAULT_LOG_MODE
        self.debug_topmost = self.log_mode == LOG_MODE_DEBUG

        # 兼容旧开关：HASHSNAP_DEBUG_TOPMOST=1/0 可直接覆盖 detailed topmost 调试。
        legacy = os.environ.get(DEBUG_TOPMOST_ENV)
        if legacy is not None:
            self.debug_topmost = legacy.strip().lower() not in {"0", "false", "off", "no"}
            if self.debug_topmost:
                self.log_mode = LOG_MODE_DEBUG

        self._topmost_debug_seq = 0

    def showEvent(self, event):
        super().showEvent(event)
        self._debug_topmost_state("show_event_before_focus_ops")

        # 先执行一次，尽量命中开始菜单仍在前台的时机
        self._force_win_topmost()

        # 再走 Qt 焦点链
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._debug_topmost_state("show_event_after_focus_ops")

        # 最后补一次，处理竞争条件
        QtCore.QTimer.singleShot(0, self._force_win_topmost)
        QtCore.QTimer.singleShot(
            DEBUG_TOPMOST_DELAY_MS,
            lambda: self._debug_topmost_state(f"show_event_t+{DEBUG_TOPMOST_DELAY_MS}ms"),
        )

    def _hwnd_value(self, hwnd):
        if hwnd is None:
            return 0

        # sip.voidptr (Qt winId) usually supports int() directly.
        try:
            return int(hwnd)
        except Exception:
            pass

        try:
            return int(hwnd.__index__())
        except Exception:
            pass

        try:
            if isinstance(hwnd, int):
                return hwnd
            if isinstance(hwnd, (bytes, bytearray)):
                return int.from_bytes(hwnd, byteorder=sys.byteorder, signed=False)

            value = getattr(hwnd, "value", None)
            if isinstance(value, int):
                return value
            if isinstance(value, (bytes, bytearray)):
                return int.from_bytes(value, byteorder=sys.byteorder, signed=False)

            if hasattr(hwnd, "asinteger"):
                try:
                    return int(hwnd.asinteger())
                except Exception:
                    pass

            casted = ctypes.cast(hwnd, ctypes.c_void_p)
            return int(casted.value or 0)
        except Exception:
            return 0

    def _self_hwnd(self):
        # Force native handle creation, then try multiple Qt ids.
        try:
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
        except Exception:
            pass

        candidates = []
        try:
            candidates.append(self.winId())
        except Exception:
            pass
        try:
            candidates.append(self.effectiveWinId())
        except Exception:
            pass
        try:
            wh = self.windowHandle()
            if wh is not None:
                candidates.append(wh.winId())
        except Exception:
            pass

        for candidate in candidates:
            val = self._hwnd_value(candidate)
            if val:
                return val

        return 0

    def _fmt_hwnd(self, hwnd):
        val = self._hwnd_value(hwnd)
        if val <= 0:
            return "0x0"
        return f"0x{val:08X}"

    def _get_window_class(self, user32, hwnd):
        if not hwnd:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, len(buf))
        return buf.value.strip()

    def _get_window_title(self, user32, hwnd):
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, buf, len(buf))
        return buf.value.replace("\n", " ").strip()

    def _window_snapshot(self, user32, hwnd):
        if not hwnd:
            return "hwnd=0x0"

        pid = wintypes.DWORD(0)
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        GWL_STYLE = -16
        GWL_EXSTYLE = -20
        style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF

        rect = wintypes.RECT()
        has_rect = user32.GetWindowRect(hwnd, ctypes.byref(rect))
        rect_text = f"{rect.left},{rect.top},{rect.right},{rect.bottom}" if has_rect else "n/a"

        cls = self._get_window_class(user32, hwnd)
        title = self._get_window_title(user32, hwnd)
        visible = int(bool(user32.IsWindowVisible(hwnd)))
        topmost = int(bool(ex_style & 0x00000008))

        return (
            f"hwnd={self._fmt_hwnd(hwnd)},tid={tid},pid={pid.value},class={cls!r},title={title!r},"
            f"visible={visible},topmost={topmost},style=0x{style:08X},ex=0x{ex_style:08X},rect={rect_text}"
        )

    def _debug_topmost_state(self, stage, extra=""):
        if not self.debug_topmost or sys.platform != "win32":
            return

        try:
            user32 = ctypes.windll.user32
            hwnd = wintypes.HWND(self._self_hwnd())
            fg_hwnd = wintypes.HWND(self._hwnd_value(user32.GetForegroundWindow()))

            self._topmost_debug_seq += 1
            msg = (
                f"seq={self._topmost_debug_seq},stage={stage},"
                f"self=[{self._window_snapshot(user32, hwnd)}],"
                f"fg=[{self._window_snapshot(user32, fg_hwnd)}]"
            )
            if extra:
                msg += f", {extra}"
            self._write_log("topmost_debug", msg, level=LOG_MODE_DEBUG)
        except Exception:
            self._write_log(
                "topmost_debug_exception",
                f"stage={stage}, traceback={traceback.format_exc().strip()}",
                level=LOG_MODE_DEBUG,
            )

    def _force_win_topmost(self):
        if sys.platform != "win32":
            return

        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd_val = self._self_hwnd()
            hwnd = wintypes.HWND(hwnd_val)
            if not hwnd_val:
                wid = "<err>"
                ewid = "<err>"
                try:
                    wid = repr(self.winId())
                except Exception:
                    pass
                try:
                    ewid = repr(self.effectiveWinId())
                except Exception:
                    pass
                self._write_log("topmost_debug", f"stage=force_no_hwnd,winId={wid},effectiveWinId={ewid}", level=LOG_MODE_DEBUG)
                return

            WM_CANCELMODE = 0x001F
            HWND_TOPMOST = wintypes.HWND(-1)
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_FRAMECHANGED = 0x0020
            SWP_SHOWWINDOW = 0x0040
            SW_SHOW = 5
            swp_flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_FRAMECHANGED

            self._debug_topmost_state("force_enter")

            fg_hwnd = wintypes.HWND(self._hwnd_value(user32.GetForegroundWindow()))
            fg_val = self._hwnd_value(fg_hwnd)
            if fg_val and fg_val != hwnd_val:
                kernel32.SetLastError(0)
                pm_ret = user32.PostMessageW(fg_hwnd, WM_CANCELMODE, 0, 0)
                pm_err = kernel32.GetLastError()
                self._debug_topmost_state(
                    "post_cancelmode",
                    f"target={self._fmt_hwnd(fg_hwnd)}, ret={pm_ret}, err={pm_err}",
                )
            else:
                self._debug_topmost_state(
                    "post_cancelmode_skip",
                    f"fg={self._fmt_hwnd(fg_hwnd)}",
                )

            current_tid = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            attached = False
            if fg_tid and fg_tid != current_tid:
                kernel32.SetLastError(0)
                attach_ret = user32.AttachThreadInput(current_tid, fg_tid, True)
                attach_err = kernel32.GetLastError()
                attached = bool(attach_ret)
                self._debug_topmost_state(
                    "attach_thread_input",
                    f"current_tid={current_tid}, fg_tid={fg_tid}, ret={attach_ret}, err={attach_err}",
                )
            else:
                self._debug_topmost_state(
                    "attach_thread_input_skip",
                    f"current_tid={current_tid}, fg_tid={fg_tid}",
                )

            try:
                kernel32.SetLastError(0)
                show_ret = user32.ShowWindow(hwnd, SW_SHOW)
                show_err = kernel32.GetLastError()

                kernel32.SetLastError(0)
                top_ret = user32.BringWindowToTop(hwnd)
                top_err = kernel32.GetLastError()

                kernel32.SetLastError(0)
                pos_ret = user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, swp_flags)
                pos_err = kernel32.GetLastError()

                kernel32.SetLastError(0)
                fg_ret = user32.SetForegroundWindow(hwnd)
                fg_err = kernel32.GetLastError()

                kernel32.SetLastError(0)
                active_ret = user32.SetActiveWindow(hwnd)
                active_err = kernel32.GetLastError()

                kernel32.SetLastError(0)
                focus_ret = user32.SetFocus(hwnd)
                focus_err = kernel32.GetLastError()

                self._debug_topmost_state(
                    "force_calls_done",
                    f"show={show_ret}/{show_err}, bring={top_ret}/{top_err}, pos={pos_ret}/{pos_err}, "
                    f"fg={fg_ret}/{fg_err}, active={self._fmt_hwnd(active_ret)}/{active_err}, "
                    f"focus={self._fmt_hwnd(focus_ret)}/{focus_err}, flags=0x{swp_flags:04X}",
                )
            finally:
                if attached:
                    kernel32.SetLastError(0)
                    detach_ret = user32.AttachThreadInput(current_tid, fg_tid, False)
                    detach_err = kernel32.GetLastError()
                    self._debug_topmost_state(
                        "detach_thread_input",
                        f"current_tid={current_tid}, fg_tid={fg_tid}, ret={detach_ret}, err={detach_err}",
                    )

            self._debug_topmost_state("force_exit")
        except Exception:
            self._write_log(
                "topmost_force_exception",
                f"traceback={traceback.format_exc().strip()}",
            )

    def _write_log(self, reason, extra="", level=LOG_MODE_LIGHT):
        if level == LOG_MODE_DEBUG and not self.debug_topmost:
            return

        ts = datetime.now().strftime(CAPTURE_LOG_TS_FMT)
        line = f"[{ts}] {reason}"
        if extra:
            line += f" | {extra}"
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _set_clipboard_pixmap(self, pixmap, scene):
        try:
            if pixmap.isNull():
                self._write_log("clipboard_write_failed", f"scene={scene}, reason=pixmap_is_null")
                return False

            clipboard = QtWidgets.QApplication.clipboard()
            clipboard.setPixmap(pixmap, mode=clipboard.Mode.Clipboard)
            written_pixmap = clipboard.pixmap(mode=clipboard.Mode.Clipboard)

            # Some clipboard backends update slightly later; only pump events when needed.
            if written_pixmap.isNull():
                QtWidgets.QApplication.processEvents(
                    QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                )
                written_pixmap = clipboard.pixmap(mode=clipboard.Mode.Clipboard)

            if not written_pixmap.isNull():
                return True

            # Compatibility fallback for environments that prefer image payloads.
            clipboard.setImage(pixmap.toImage(), mode=clipboard.Mode.Clipboard)
            written_img = clipboard.image(mode=clipboard.Mode.Clipboard)
            if written_img.isNull():
                QtWidgets.QApplication.processEvents(
                    QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                )
                written_img = clipboard.image(mode=clipboard.Mode.Clipboard)

            if written_img.isNull():
                self._write_log(
                    "clipboard_write_failed",
                    f"scene={scene}, size={pixmap.width()}x{pixmap.height()}, dpr={pixmap.devicePixelRatio():.2f}",
                )
                return False

            return True
        except Exception:
            self._write_log(
                "clipboard_write_exception",
                f"scene={scene}, traceback={traceback.format_exc().strip()}",
            )
            return False

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.drawPixmap(self.rect(), self.pixmap)
        painter.fillRect(self.rect(), QtGui.QColor(*CAPTURE_OVERLAY_RGBA))

        if self.start_pos is not None and self.curr_pos is not None:
            rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
            # Only draw selection preview when dragged area is large enough.
            if rect.width() >= CAPTURE_SELECTION_MIN_PX and rect.height() >= CAPTURE_SELECTION_MIN_PX:
                # Avoid per-frame pixmap copy while dragging.
                painter.save()
                painter.setClipRect(rect)
                painter.drawPixmap(self.rect(), self.pixmap)
                painter.restore()
                painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.cyan, 2))
                painter.drawRect(rect)

    def mousePressEvent(self, event):
        # 右键单击：直接退出取消截图
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.close()
            return

        # 左键单击：记录起始位置
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.start_pos = event.position().toPoint()
            self.curr_pos = self.start_pos

    def mouseMoveEvent(self, event):
        if self.start_pos is not None:
            self.curr_pos = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.start_pos is not None:
            self.curr_pos = event.position().toPoint()
            rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
            moved = (self.curr_pos - self.start_pos).manhattanLength()

            # 判断逻辑：拖拽距离极小，判定为“左键单击”
            if moved <= self.click_threshold:
                # 动作：截取全屏
                full_pixmap = self.pixmap.copy()
                full_pixmap.setDevicePixelRatio(self.pixmap.devicePixelRatio())
                self._set_clipboard_pixmap(full_pixmap, "fullscreen_click")
            else:
                # 动作：截取选区
                ratio = self.pixmap.devicePixelRatio()
                physical_rect = QtCore.QRect(
                    int(rect.x() * ratio),
                    int(rect.y() * ratio),
                    int(rect.width() * ratio),
                    int(rect.height() * ratio),
                )
                final_pixmap = self.pixmap.copy(physical_rect)
                final_pixmap.setDevicePixelRatio(ratio)
                self._set_clipboard_pixmap(final_pixmap, "region_drag")

            self.start_pos = None
            self.curr_pos = None
            self.close()

    # 按 Esc 键也可以直接退出截图
    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()





