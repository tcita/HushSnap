# 打包命令: pyinstaller --noconsole --onefile --clean HashSnap.py
# 在项目根目录终端执行: $exe=(Resolve-Path '.\dist\HashSnap.exe').Path; $startup=[Environment]::GetFolderPath('Startup'); $lnk=Join-Path $startup 'HashSnap.lnk'; $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($lnk); $s.TargetPath=$exe; $s.WorkingDirectory=(Split-Path $exe); $s.IconLocation="$exe,0"; $s.Save(); Start-Process $exe
# 手动自启: 将.\dist\HashSnap.exe快捷方式放入shell:startup，并立刻启动一次

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
            json.dumps({"hotkey": DEFAULT_HOTKEY}, indent=2),
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
        # 使用 Window 标志确保它是一个独立的顶级窗口
        self.setWindowFlags(QtCore.Qt.WindowType.Window |
                            QtCore.Qt.WindowType.FramelessWindowHint | 
                            QtCore.Qt.WindowType.WindowStaysOnTopHint | 
                            QtCore.Qt.WindowType.Tool)
        
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
        self.log_path = get_app_dir() / "hashsnap_capture_error.log"

    def showEvent(self, event):
        super().showEvent(event)
        # 确保窗口能够接收输入
        self.raise_()
        self.activateWindow()
        self.setFocus()
        # 稍微增加延迟，确保系统完成窗口创建后再强制夺取最高优先级
        QtCore.QTimer.singleShot(100, self._force_win_topmost)

    def _force_win_topmost(self):
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                # 设置 WS_EX_TOPMOST 样式
                GWL_EXSTYLE = -20
                WS_EX_TOPMOST = 0x00000008
                current_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, current_style | WS_EX_TOPMOST)
                
                # 强制夺取前台焦点并置顶
                ctypes.windll.user32.BringWindowToTop(hwnd)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                # HWND_TOPMOST = -1, SWP_NOMOVE=2, SWP_NOSIZE=1, SWP_SHOWWINDOW=0x40
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
            except Exception:
                pass

    def _write_error_log(self, reason, extra=""):
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
                self._write_error_log("clipboard_write_failed", f"scene={scene}, reason=pixmap_is_null")
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
                self._write_error_log(
                    "clipboard_write_failed",
                    f"scene={scene}, size={pixmap.width()}x{pixmap.height()}, dpr={pixmap.devicePixelRatio():.2f}"
                )
                return False

            return True
        except Exception:
            self._write_error_log(
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

    # 3. 系统托盘设置
    icon = create_tray_icon()
    app.setWindowIcon(icon)
    tray_icon = QtWidgets.QSystemTrayIcon(icon, app)
    menu = QtWidgets.QMenu()
    tray_icon.setContextMenu(menu)
    tray_icon.show()

    comm = Communicator()
    comm.win = None

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
        # 显示后立即尝试一次强行置顶
        if sys.platform == "win32":
            QtCore.QTimer.singleShot(10, comm.win._force_win_topmost)

    comm.trigger.connect(launch)

    # 4. 注册系统全局热键 (从配置文件读取)
    hotkey_mod, hotkey_vk, hotkey_name, config_path = load_hotkey_setting()
    if not ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, hotkey_mod, hotkey_vk):
        QtWidgets.QMessageBox.warning(
            None,
            "错误",
            f"{hotkey_name} 热键已被占用！\n配置文件: {config_path}",
        )
        hotkey_registered = False
    else:
        hotkey_registered = True

    current_hotkey_mod = hotkey_mod
    current_hotkey_vk = hotkey_vk
    current_hotkey_name = hotkey_name

    def find_uninstaller():
        app_dir = get_app_dir()
        default_uninstaller = app_dir / "unins000.exe"
        if default_uninstaller.exists():
            return default_uninstaller
        candidates = sorted(app_dir.glob("unins*.exe"))
        return candidates[0] if candidates else None

    def launch_uninstaller():
        uninstaller_path = find_uninstaller()
        if not uninstaller_path:
            QtWidgets.QMessageBox.warning(
                None,
                "未找到卸载程序",
                "当前目录未检测到卸载程序，请在 控制面板 -> 程序和功能 中卸载 HashSnap。",
            )
            return

        confirm = QtWidgets.QMessageBox.question(
            None,
            "确认卸载",
            "将启动 HashSnap 卸载程序，是否继续？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        try:
            subprocess.Popen([str(uninstaller_path)], cwd=str(uninstaller_path.parent))
            app.quit()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(None, "启动卸载失败", str(exc))

    def show_settings_about():
        box = QtWidgets.QMessageBox()
        box.setIcon(QtWidgets.QMessageBox.Icon.Information)
        box.setWindowTitle("设置 / 关于")
        box.setText(
            f"HashSnap {APP_VERSION}\n当前热键: {current_hotkey_name}\n配置文件: {config_path}"
        )
        box.setInformativeText("可在 控制面板 -> 程序和功能 卸载，或点击下方“卸载”按钮。")
        uninstall_btn = box.addButton("卸载", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("关闭", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == uninstall_btn:
            launch_uninstaller()

    def open_app_dir():
        try:
            os.startfile(get_app_dir())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(None, "无法打开目录", str(exc))

    settings_about_action = menu.addAction("设置/关于...")
    settings_about_action.triggered.connect(show_settings_about)
    
    open_dir_action = menu.addAction("打开安装目录")
    open_dir_action.triggered.connect(open_app_dir)

    menu.addSeparator()
    quit_action = menu.addAction("退出")
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
                "HashSnap 热键未更新",
                f"配置无效，继续使用 {current_hotkey_name}\n{exc}",
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )
            return

        if new_mod == current_hotkey_mod and new_vk == current_hotkey_vk:
            if hotkey_registered:
                return
            if register_hotkey(new_mod, new_vk, new_name):
                tray_icon.showMessage(
                    "HashSnap 热键已启用",
                    f"已启用 {new_name}",
                    QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                    2000,
                )
            else:
                tray_icon.showMessage(
                    "HashSnap 热键未更新",
                    f"{new_name} 仍被占用",
                    QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                    3000,
                )
            return

        old_mod, old_vk, old_name = current_hotkey_mod, current_hotkey_vk, current_hotkey_name
        unregister_current_hotkey()
        if register_hotkey(new_mod, new_vk, new_name):
            tray_icon.showMessage(
                "HashSnap 热键已更新",
                f"{old_name} -> {new_name}",
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
            return

        # 新热键注册失败，回滚旧热键。
        if not register_hotkey(old_mod, old_vk, old_name):
            tray_icon.showMessage(
                "HashSnap 热键错误",
                "新热键不可用，且旧热键恢复失败。",
                QtWidgets.QSystemTrayIcon.MessageIcon.Critical,
                4000,
            )
            return

        tray_icon.showMessage(
            "HashSnap 热键未更新",
            f"{new_name} 被占用，已保持 {old_name}",
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

