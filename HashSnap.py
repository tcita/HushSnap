import os
import sys
import socket
import ctypes
import json
import subprocess
from pathlib import Path
from datetime import datetime
import traceback
from ctypes import wintypes
from PyQt6 import QtWidgets, QtCore, QtGui

# --- 1. Windows 原生热键常量定义 ---
WM_HOTKEY = 0x0312
HOTKEY_ID = 1
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
DEFAULT_HOTKEY = "Alt+Q"
APP_VERSION = "1.0.0"

# --- 2. Logging switches (visible) ---
# User default: lightweight logs only.
LOG_MODE_LIGHT = "light"
LOG_MODE_DEBUG = "debug"
DEFAULT_LOG_MODE = LOG_MODE_LIGHT
LOG_MODE_ENV = "HASHSNAP_LOG_MODE"  # values: light | debug
DEBUG_TOPMOST_ENV = "HASHSNAP_DEBUG_TOPMOST"  # backward-compatible override

# --- 3. UI language switches ---
UI_LANG_ENV = "HASHSNAP_UI_LANG"  # values: auto | en | zh
UI_LANG_AUTO = "auto"
UI_LANG_EN = "en"
UI_LANG_ZH = "zh"
DEFAULT_UI_LANG = UI_LANG_AUTO

UI_TEXT = {
    UI_LANG_EN: {
        "error": "Error",
        "hotkey_taken": "{hotkey} is already in use.\nConfig: {config_path}",
        "uninstaller_not_found_title": "Uninstaller Not Found",
        "uninstaller_not_found_body": "No uninstaller was found in the app directory. Uninstall HashSnap from Control Panel > Programs and Features.",
        "confirm_uninstall_title": "Confirm Uninstall",
        "confirm_uninstall_body": "HashSnap uninstaller will be launched. Continue?",
        "launch_uninstall_failed": "Failed to Launch Uninstaller",
        "settings_about_title": "Settings / About",
        "settings_about_body": "HashSnap {version}\nCurrent hotkey: {hotkey}\nConfig: {config_path}",
        "settings_about_info": "You can uninstall from Control Panel > Programs and Features, or click \"Uninstall\" below.",
        "uninstall_btn": "Uninstall",
        "close_btn": "Close",
        "open_dir_failed": "Cannot Open Folder",
        "menu_settings_about": "Settings / About...",
        "menu_open_install_dir": "Open Install Folder",
        "menu_quit": "Quit",
        "hotkey_not_updated_title": "HashSnap Hotkey Not Updated",
        "hotkey_invalid_config": "Invalid config. Keep using {hotkey}\n{error}",
        "hotkey_enabled_title": "HashSnap Hotkey Enabled",
        "hotkey_enabled": "Enabled {hotkey}",
        "hotkey_still_occupied": "{hotkey} is still in use",
        "hotkey_updated_title": "HashSnap Hotkey Updated",
        "hotkey_updated": "{old_hotkey} -> {new_hotkey}",
        "hotkey_error_title": "HashSnap Hotkey Error",
        "hotkey_recover_failed": "New hotkey is unavailable, and old hotkey recovery failed.",
        "hotkey_kept_old": "{new_hotkey} is in use. Kept {old_hotkey}.",
    },
    UI_LANG_ZH: {
        "error": "错误",
        "hotkey_taken": "{hotkey} 热键已被占用！\n配置文件: {config_path}",
        "uninstaller_not_found_title": "未找到卸载程序",
        "uninstaller_not_found_body": "当前目录未检测到卸载程序，请在 控制面板 -> 程序和功能 中卸载 HashSnap。",
        "confirm_uninstall_title": "确认卸载",
        "confirm_uninstall_body": "将启动 HashSnap 卸载程序，是否继续？",
        "launch_uninstall_failed": "启动卸载失败",
        "settings_about_title": "设置 / 关于",
        "settings_about_body": "HashSnap {version}\n当前热键: {hotkey}\n配置文件: {config_path}",
        "settings_about_info": "可在 控制面板 -> 程序和功能 卸载，或点击下方“卸载”按钮。",
        "uninstall_btn": "卸载",
        "close_btn": "关闭",
        "open_dir_failed": "无法打开目录",
        "menu_settings_about": "设置/关于...",
        "menu_open_install_dir": "打开安装目录",
        "menu_quit": "退出",
        "hotkey_not_updated_title": "HashSnap 热键未更新",
        "hotkey_invalid_config": "配置无效，继续使用 {hotkey}\n{error}",
        "hotkey_enabled_title": "HashSnap 热键已启用",
        "hotkey_enabled": "已启用 {hotkey}",
        "hotkey_still_occupied": "{hotkey} 仍被占用",
        "hotkey_updated_title": "HashSnap 热键已更新",
        "hotkey_updated": "{old_hotkey} -> {new_hotkey}",
        "hotkey_error_title": "HashSnap 热键错误",
        "hotkey_recover_failed": "新热键不可用，且旧热键恢复失败。",
        "hotkey_kept_old": "{new_hotkey} 被占用，已保持 {old_hotkey}",
    },
}

# 托盘图标：程序内绘制，避免依赖外部 ico 文件
def create_tray_icon():
    def _draw(size):
        pix = QtGui.QPixmap(size, size)
        pix.fill(QtCore.Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        pad = max(1, int(size * 0.08))
        rect = QtCore.QRectF(pad, pad, size - 2 * pad, size - 2 * pad)

        # 圆角底板渐变
        grad = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QtGui.QColor(45, 174, 229))
        grad.setColorAt(1.0, QtGui.QColor(20, 122, 191))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QBrush(grad))
        painter.drawRoundedRect(rect, size * 0.22, size * 0.22)

        # 相机机身
        body_w = rect.width() * 0.70
        body_h = rect.height() * 0.48
        body_x = rect.center().x() - body_w / 2
        body_y = rect.center().y() - body_h / 2 + size * 0.02
        body = QtCore.QRectF(body_x, body_y, body_w, body_h)
        painter.setBrush(QtGui.QColor(245, 250, 255))
        painter.drawRoundedRect(body, size * 0.07, size * 0.07)

        # 顶部取景突起
        hump = QtCore.QRectF(
            body.left() + body.width() * 0.10,
            body.top() - body.height() * 0.20,
            body.width() * 0.26,
            body.height() * 0.22,
        )
        painter.drawRoundedRect(hump, size * 0.04, size * 0.04)

        # 镜头
        lens_r = size * 0.14
        lens_c = QtCore.QPointF(body.center().x(), body.center().y())
        painter.setBrush(QtGui.QColor(28, 70, 110))
        painter.drawEllipse(lens_c, lens_r, lens_r)
        painter.setBrush(QtGui.QColor(120, 192, 245))
        painter.drawEllipse(lens_c, lens_r * 0.55, lens_r * 0.55)

        # 高光
        hl = QtCore.QRectF(
            rect.left() + size * 0.14,
            rect.top() + size * 0.12,
            size * 0.28,
            size * 0.13,
        )
        painter.setBrush(QtGui.QColor(255, 255, 255, 70))
        painter.drawRoundedRect(hl, size * 0.05, size * 0.05)

        painter.end()
        return pix

    icon = QtGui.QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128):
        icon.addPixmap(_draw(s))
    return icon


def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_config_path():
    return get_app_dir() / "hashsnap_config.json"


def _write_default_config_if_missing(config_path):
    if config_path.exists():
        return
    try:
        config_path.write_text(
            json.dumps({"hotkey": DEFAULT_HOTKEY, "language": UI_LANG_AUTO}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _parse_vk_key(token):
    t = token.strip().upper()
    if len(t) == 1 and "A" <= t <= "Z":
        return ord(t)
    if len(t) == 1 and "0" <= t <= "9":
        return ord(t)
    if t.startswith("F") and t[1:].isdigit():
        n = int(t[1:])
        if 1 <= n <= 24:
            return 0x6F + n

    named = {
        "ESC": 0x1B,
        "ESCAPE": 0x1B,
        "TAB": 0x09,
        "ENTER": 0x0D,
        "RETURN": 0x0D,
        "SPACE": 0x20,
        "LEFT": 0x25,
        "UP": 0x26,
        "RIGHT": 0x27,
        "DOWN": 0x28,
    }
    return named.get(t)


def parse_hotkey(hotkey_text):
    parts = [p.strip() for p in hotkey_text.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("Hotkey must include at least one modifier and one key.")

    modifier_tokens = parts[:-1]
    key_token = parts[-1]

    mod = 0
    for raw in modifier_tokens:
        t = raw.lower()
        if t == "alt":
            mod |= MOD_ALT
        elif t in ("ctrl", "control"):
            mod |= MOD_CONTROL
        elif t == "shift":
            mod |= MOD_SHIFT
        elif t in ("win", "windows"):
            mod |= MOD_WIN
        else:
            raise ValueError(f"Unknown modifier: {raw}")

    if mod == 0:
        raise ValueError("At least one modifier is required.")

    vk = _parse_vk_key(key_token)
    if vk is None:
        raise ValueError(f"Unsupported key: {key_token}")

    canonical_mods = []
    if mod & MOD_CONTROL:
        canonical_mods.append("Ctrl")
    if mod & MOD_ALT:
        canonical_mods.append("Alt")
    if mod & MOD_SHIFT:
        canonical_mods.append("Shift")
    if mod & MOD_WIN:
        canonical_mods.append("Win")
    canonical_hotkey = "+".join(canonical_mods + [key_token.upper()])
    return mod, vk, canonical_hotkey


def read_hotkey_text_from_config(config_path):
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Config must be a JSON object.")
    value = data.get("hotkey")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("hotkey must be a non-empty string.")
    return value.strip()


def load_hotkey_setting():
    config_path = get_config_path()
    _write_default_config_if_missing(config_path)

    try:
        mod, vk, canonical = parse_hotkey(read_hotkey_text_from_config(config_path))
        return mod, vk, canonical, config_path
    except Exception:
        mod, vk, canonical = parse_hotkey(DEFAULT_HOTKEY)
        return mod, vk, canonical, config_path


def _read_ui_lang_from_config(config_path):
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return UI_LANG_AUTO
        value = data.get("language", UI_LANG_AUTO)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {UI_LANG_AUTO, UI_LANG_EN, UI_LANG_ZH}:
                return v
    except Exception:
        pass
    return UI_LANG_AUTO


def resolve_ui_lang(config_path):
    env = os.environ.get(UI_LANG_ENV, "").strip().lower()
    if env in {UI_LANG_EN, UI_LANG_ZH}:
        return env

    cfg = _read_ui_lang_from_config(config_path)
    if cfg in {UI_LANG_EN, UI_LANG_ZH}:
        return cfg

    locale_name = QtCore.QLocale.system().name().lower()
    return UI_LANG_ZH if locale_name.startswith("zh") else UI_LANG_EN


def ui_text(lang, key, **kwargs):
    table = UI_TEXT.get(lang, UI_TEXT[UI_LANG_EN])
    text = table.get(key, UI_TEXT[UI_LANG_EN].get(key, key))
    return text.format(**kwargs)

# 防止多开
def is_already_running():
    try:
        lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lock_socket.bind(("127.0.0.1", 65432))
        return lock_socket
    except socket.error:
        return None

# 截图窗口逻辑
class CaptureWindow(QtWidgets.QWidget):
    def __init__(self, pixmap):
        super().__init__()
        self.pixmap = pixmap
        
        # 【修改点 1】：移除 BypassWindowManagerHint 和原生的 Window 标志
        # Tool + Frameless + StaysOnTop 已经足够覆盖全屏、不显示任务栏图标，
        # 并且能让系统正常处理焦点转移，从而触发开始菜单的自动收起（Light-dismiss）。
        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool |
            QtCore.Qt.WindowType.FramelessWindowHint | 
            QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)

        self.setWindowState(QtCore.Qt.WindowState.WindowFullScreen)
        # 显式覆盖所有屏幕区域
        screen = QtWidgets.QApplication.primaryScreen()
        self.setGeometry(screen.geometry())
        
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.start_pos = None
        self.curr_pos = None
        self.click_threshold = 8
        # Keep logs next to installed executable for stable post-install debugging.
        self.log_path = get_app_dir() / "hashsnap_capture_debug.log"

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
        QtCore.QTimer.singleShot(120, lambda: self._debug_topmost_state("show_event_t+120ms"))

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
        return buf.value.replace("\n", " " ).strip()

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
                level=LOG_MODE_DEBUG
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
                    f"target={self._fmt_hwnd(fg_hwnd)}, ret={pm_ret}, err={pm_err}"
                )
            else:
                self._debug_topmost_state(
                    "post_cancelmode_skip",
                    f"fg={self._fmt_hwnd(fg_hwnd)}"
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
                    f"current_tid={current_tid}, fg_tid={fg_tid}, ret={attach_ret}, err={attach_err}"
                )
            else:
                self._debug_topmost_state(
                    "attach_thread_input_skip",
                    f"current_tid={current_tid}, fg_tid={fg_tid}"
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
                    f"focus={self._fmt_hwnd(focus_ret)}/{focus_err}, flags=0x{swp_flags:04X}"
                )
            finally:
                if attached:
                    kernel32.SetLastError(0)
                    detach_ret = user32.AttachThreadInput(current_tid, fg_tid, False)
                    detach_err = kernel32.GetLastError()
                    self._debug_topmost_state(
                        "detach_thread_input",
                        f"current_tid={current_tid}, fg_tid={fg_tid}, ret={detach_ret}, err={detach_err}"
                    )

            self._debug_topmost_state("force_exit")
        except Exception:
            self._write_log(
                "topmost_force_exception",
                f"traceback={traceback.format_exc().strip()}"
            )

    def _write_log(self, reason, extra="", level=LOG_MODE_LIGHT):
        if level == LOG_MODE_DEBUG and not self.debug_topmost:
            return

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                    f"scene={scene}, size={pixmap.width()}x{pixmap.height()}, dpr={pixmap.devicePixelRatio():.2f}"
                )
                return False

            return True
        except Exception:
            self._write_log(
                "clipboard_write_exception",
                f"scene={scene}, traceback={traceback.format_exc().strip()}"
            )
            return False

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.drawPixmap(self.rect(), self.pixmap)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 80)) 
        
        if self.start_pos is not None and self.curr_pos is not None:
            rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
            # Only draw selection preview when dragged area is large enough.
            if rect.width() >= 10 and rect.height() >= 10:
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
                    int(rect.x() * ratio), int(rect.y() * ratio),
                    int(rect.width() * ratio), int(rect.height() * ratio)
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


# --- 2. 原生事件过滤器：监听全局热键 ---
class HotkeyFilter(QtCore.QAbstractNativeEventFilter):
    def __init__(self, trigger_signal):
        super().__init__()
        self.trigger_signal = trigger_signal

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self.trigger_signal.emit()
                return True, 0
        return False, 0


class Communicator(QtCore.QObject):
    trigger = QtCore.pyqtSignal()


def main():
    # 1. 检查多开
    _lock = is_already_running()
    if not _lock:
        return

    # 2. 正常初始化 App (PyQt6 自动处理高DPI)
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 3. 加载配置与 UI 语言
    hotkey_mod, hotkey_vk, hotkey_name, config_path = load_hotkey_setting()
    ui_lang = resolve_ui_lang(config_path)

    def t(key, **kwargs):
        return ui_text(ui_lang, key, **kwargs)

    # 4. 系统托盘设置
    icon = create_tray_icon()
    app.setWindowIcon(icon)
    tray_icon = QtWidgets.QSystemTrayIcon(icon, app)
    menu = QtWidgets.QMenu()
    tray_icon.setContextMenu(menu)
    tray_icon.show()

    comm = Communicator()
    comm.win = None

    def on_tray_activated(reason):
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            comm.trigger.emit()

    tray_icon.activated.connect(on_tray_activated)

    def launch():
        try:
            if comm.win and comm.win.isVisible(): 
                return
        except RuntimeError: 
            comm.win = None
        
        screen = QtWidgets.QApplication.primaryScreen()
        # 抓取屏幕时显式设置 DPR，确保后续选区计算正确
        dpr = screen.devicePixelRatio()
        p = screen.grabWindow(0)
        p.setDevicePixelRatio(dpr)
        
        comm.win = CaptureWindow(p)
        comm.win.show()

    comm.trigger.connect(launch)

    # 5. 注册系统全局热键
    if not ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, hotkey_mod, hotkey_vk):
        QtWidgets.QMessageBox.warning(
            None,
            t("error"),
            t("hotkey_taken", hotkey=hotkey_name, config_path=config_path),
        )
        hotkey_registered = False
    else:
        hotkey_registered = True

    current_hotkey_mod = hotkey_mod
    current_hotkey_vk = hotkey_vk
    current_hotkey_name = hotkey_name

    def find_uninstaller():
        app_dir = get_app_dir()

        # Prefer the newest uninstaller to avoid launching stale unins000.exe.
        candidates = []
        for p in app_dir.glob("unins*.exe"):
            try:
                stat = p.stat()
                candidates.append((stat.st_mtime, p))
            except Exception:
                candidates.append((0.0, p))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def launch_uninstaller():
        uninstaller_path = find_uninstaller()
        if not uninstaller_path:
            QtWidgets.QMessageBox.warning(
                None,
                t("uninstaller_not_found_title"),
                t("uninstaller_not_found_body"),
            )
            return

        confirm = QtWidgets.QMessageBox.question(
            None,
            t("confirm_uninstall_title"),
            t("confirm_uninstall_body"),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        try:
            subprocess.Popen([str(uninstaller_path)], cwd=str(uninstaller_path.parent))
            app.quit()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(None, t("launch_uninstall_failed"), str(exc))

    def show_settings_about():
        box = QtWidgets.QMessageBox()
        box.setIcon(QtWidgets.QMessageBox.Icon.Information)
        box.setWindowTitle(t("settings_about_title"))
        box.setText(
            t("settings_about_body", version=APP_VERSION, hotkey=current_hotkey_name, config_path=config_path)
        )
        box.setInformativeText(t("settings_about_info"))
        uninstall_btn = box.addButton(t("uninstall_btn"), QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(t("close_btn"), QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == uninstall_btn:
            launch_uninstaller()

    def open_app_dir():
        try:
            os.startfile(get_app_dir())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(None, t("open_dir_failed"), str(exc))

    settings_about_action = menu.addAction(t("menu_settings_about"))
    settings_about_action.triggered.connect(show_settings_about)
    
    open_dir_action = menu.addAction(t("menu_open_install_dir"))
    open_dir_action.triggered.connect(open_app_dir)

    menu.addSeparator()
    quit_action = menu.addAction(t("menu_quit"))
    quit_action.triggered.connect(app.quit)

    def unregister_current_hotkey():
        nonlocal hotkey_registered
        if hotkey_registered:
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
            hotkey_registered = False

    def register_hotkey(mod, vk, name):
        nonlocal hotkey_registered, current_hotkey_mod, current_hotkey_vk, current_hotkey_name
        if ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, mod, vk):
            hotkey_registered = True
            current_hotkey_mod = mod
            current_hotkey_vk = vk
            current_hotkey_name = name
            return True
        return False

    # 5. 监听配置文件变化，变更时再重载热键（无轮询）
    watcher = QtCore.QFileSystemWatcher(app)
    config_path_str = str(config_path)
    config_dir_str = str(config_path.parent)
    watcher.addPath(config_dir_str)
    if config_path.exists():
        watcher.addPath(config_path_str)

    reload_timer = QtCore.QTimer(app)
    reload_timer.setSingleShot(True)
    reload_timer.setInterval(300)

    def ensure_watch_targets():
        if config_dir_str not in watcher.directories():
            watcher.addPath(config_dir_str)
        if config_path.exists() and config_path_str not in watcher.files():
            watcher.addPath(config_path_str)

    def apply_hotkey_reload():
        nonlocal current_hotkey_mod, current_hotkey_vk, current_hotkey_name
        ensure_watch_targets()
        try:
            new_mod, new_vk, new_name = parse_hotkey(read_hotkey_text_from_config(config_path))
        except Exception as exc:
            tray_icon.showMessage(
                t("hotkey_not_updated_title"),
                t("hotkey_invalid_config", hotkey=current_hotkey_name, error=exc),
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )
            return

        if new_mod == current_hotkey_mod and new_vk == current_hotkey_vk:
            if hotkey_registered:
                return
            if register_hotkey(new_mod, new_vk, new_name):
                tray_icon.showMessage(
                    t("hotkey_enabled_title"),
                    t("hotkey_enabled", hotkey=new_name),
                    QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                    2000,
                )
            else:
                tray_icon.showMessage(
                    t("hotkey_not_updated_title"),
                    t("hotkey_still_occupied", hotkey=new_name),
                    QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                    3000,
                )
            return

        old_mod, old_vk, old_name = current_hotkey_mod, current_hotkey_vk, current_hotkey_name
        unregister_current_hotkey()
        if register_hotkey(new_mod, new_vk, new_name):
            tray_icon.showMessage(
                t("hotkey_updated_title"),
                t("hotkey_updated", old_hotkey=old_name, new_hotkey=new_name),
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
            return

        # 新热键注册失败，回滚旧热键。
        if not register_hotkey(old_mod, old_vk, old_name):
            tray_icon.showMessage(
                t("hotkey_error_title"),
                t("hotkey_recover_failed"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Critical,
                4000,
            )
            return

        tray_icon.showMessage(
            t("hotkey_not_updated_title"),
            t("hotkey_kept_old", new_hotkey=new_name, old_hotkey=old_name),
            QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
            3000,
        )

    def schedule_hotkey_reload(_path):
        ensure_watch_targets()
        reload_timer.start()

    watcher.fileChanged.connect(schedule_hotkey_reload)
    watcher.directoryChanged.connect(schedule_hotkey_reload)
    reload_timer.timeout.connect(apply_hotkey_reload)

    app.aboutToQuit.connect(unregister_current_hotkey)

    # 安装过滤器捕获热键
    nav_filter = HotkeyFilter(comm.trigger)
    app.installNativeEventFilter(nav_filter)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()

