import ctypes
import os
import subprocess
import sys

from PyQt6 import QtCore, QtGui, QtWidgets

from .capture_window import CaptureWindow
from .config import (
    get_app_dir,
    get_resource_dir,
    is_already_running,
    load_hotkey_setting,
    parse_hotkey,
    read_hotkey_text_from_config,
    resolve_ui_lang,
    ui_text,
    update_hotkey_in_config,
)
from .constants import HOTKEY_ID
from .hotkey import Communicator, HotkeyFilter


def main(app_version):
    # 1. 检查多开
    instance_lock = is_already_running()
    if not instance_lock:
        return

    # 2. 正常初始化 App (PyQt6 自动处理高DPI)
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 3. 加载配置与 UI 语言
    hotkey_modifier, hotkey_virtual_key, hotkey_name, config_path = load_hotkey_setting()
    ui_language = resolve_ui_lang(config_path)

    def translate(key, **kwargs):
        return ui_text(ui_language, key, **kwargs)

    # 4. 系统托盘设置
    tray_icon_image = QtGui.QIcon(str(get_resource_dir() / "camera.ico"))
    app.setWindowIcon(tray_icon_image)
    tray_icon = QtWidgets.QSystemTrayIcon(tray_icon_image, app)
    tray_menu = QtWidgets.QMenu()
    tray_icon.setContextMenu(tray_menu)
    tray_icon.show()

    communicator = Communicator()
    communicator.win = None

    def on_tray_icon_activated(reason):
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            communicator.trigger.emit()

    tray_icon.activated.connect(on_tray_icon_activated)

    def launch_capture_window():
        try:
            if communicator.win and communicator.win.isVisible():
                return
        except RuntimeError:
            communicator.win = None

        screen = QtWidgets.QApplication.primaryScreen()
        # 抓取屏幕时显式设置 DPR，确保后续选区计算正确
        device_pixel_ratio = screen.devicePixelRatio()
        screen_pixmap = screen.grabWindow(0)
        screen_pixmap.setDevicePixelRatio(device_pixel_ratio)

        communicator.win = CaptureWindow(screen_pixmap)
        communicator.win.show()

    communicator.trigger.connect(launch_capture_window)

    # 5. 注册系统全局热键
    if not ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, hotkey_modifier, hotkey_virtual_key):
        QtWidgets.QMessageBox.warning(
            None,
            translate("error"),
            translate("hotkey_taken", hotkey=hotkey_name, config_path=config_path),
        )
        hotkey_registered = False
    else:
        hotkey_registered = True

    current_hotkey_modifier = hotkey_modifier
    current_hotkey_virtual_key = hotkey_virtual_key
    current_hotkey_name = hotkey_name
    settings_dialog = None
    settings_hotkey_label = None

    def refresh_settings_hotkey_label():
        nonlocal settings_hotkey_label
        if settings_hotkey_label is None:
            return
        try:
            settings_hotkey_label.setText(
                translate("settings_current_hotkey", hotkey=current_hotkey_name)
            )
        except RuntimeError:
            settings_hotkey_label = None

    def find_uninstaller():
        app_dir = get_app_dir()

        # Prefer the newest uninstaller to avoid launching stale unins000.exe.
        uninstaller_candidates = []
        for candidate_path in app_dir.glob("unins*.exe"):
            try:
                stat = candidate_path.stat()
                uninstaller_candidates.append((stat.st_mtime, candidate_path))
            except Exception:
                uninstaller_candidates.append((0.0, candidate_path))

        if not uninstaller_candidates:
            return None

        uninstaller_candidates.sort(key=lambda item: item[0], reverse=True)
        return uninstaller_candidates[0][1]

    def launch_uninstaller():
        uninstaller_path = find_uninstaller()
        if not uninstaller_path:
            QtWidgets.QMessageBox.warning(
                None,
                translate("uninstaller_not_found_title"),
                translate("uninstaller_not_found_body"),
            )
            return

        confirm = QtWidgets.QMessageBox.question(
            None,
            translate("confirm_uninstall_title"),
            translate("confirm_uninstall_body"),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        try:
            subprocess.Popen([str(uninstaller_path)], cwd=str(uninstaller_path.parent))
            app.quit()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(None, translate("launch_uninstall_failed"), str(exc))

    def open_config_dir():
        try:
            os.startfile(config_path.parent)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                None,
                translate("open_dir_failed"),
                str(exc),
            )

    def unregister_current_hotkey():
        nonlocal hotkey_registered
        if hotkey_registered:
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
            hotkey_registered = False

    def register_hotkey(modifier, virtual_key, name):
        nonlocal hotkey_registered, current_hotkey_modifier, current_hotkey_virtual_key, current_hotkey_name
        if ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, modifier, virtual_key):
            hotkey_registered = True
            current_hotkey_modifier = modifier
            current_hotkey_virtual_key = virtual_key
            current_hotkey_name = name
            refresh_settings_hotkey_label()
            return True
        return False

    # 6. 监听配置文件变化，变更时再重载热键（无轮询）
    watcher = QtCore.QFileSystemWatcher(app)
    config_file_path_str = str(config_path)
    config_dir_path_str = str(config_path.parent)
    watcher.addPath(config_dir_path_str)
    if config_path.exists():
        watcher.addPath(config_file_path_str)

    reload_timer = QtCore.QTimer(app)
    reload_timer.setSingleShot(True)
    reload_timer.setInterval(300)

    def ensure_watch_targets():
        if config_dir_path_str not in watcher.directories():
            watcher.addPath(config_dir_path_str)
        if config_path.exists() and config_file_path_str not in watcher.files():
            watcher.addPath(config_file_path_str)

    def apply_hotkey_reload():
        nonlocal current_hotkey_modifier, current_hotkey_virtual_key, current_hotkey_name
        ensure_watch_targets()
        try:
            new_modifier, new_virtual_key, new_name = parse_hotkey(read_hotkey_text_from_config(config_path))
        except Exception as exc:
            tray_icon.showMessage(
                translate("hotkey_not_updated_title"),
                translate("hotkey_invalid_config", hotkey=current_hotkey_name, error=exc),
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )
            return

        if new_modifier == current_hotkey_modifier and new_virtual_key == current_hotkey_virtual_key:
            if hotkey_registered:
                return
            if register_hotkey(new_modifier, new_virtual_key, new_name):
                tray_icon.showMessage(
                    translate("hotkey_enabled_title"),
                    translate("hotkey_enabled", hotkey=new_name),
                    QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                    2000,
                )
            else:
                tray_icon.showMessage(
                    translate("hotkey_not_updated_title"),
                    translate("hotkey_still_occupied", hotkey=new_name),
                    QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                    3000,
                )
            return

        old_modifier, old_virtual_key, old_name = (
            current_hotkey_modifier,
            current_hotkey_virtual_key,
            current_hotkey_name,
        )
        unregister_current_hotkey()
        if register_hotkey(new_modifier, new_virtual_key, new_name):
            tray_icon.showMessage(
                translate("hotkey_updated_title"),
                translate("hotkey_updated", old_hotkey=old_name, new_hotkey=new_name),
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
            return

        # 新热键注册失败，回滚旧热键。
        if not register_hotkey(old_modifier, old_virtual_key, old_name):
            tray_icon.showMessage(
                translate("hotkey_error_title"),
                translate("hotkey_recover_failed"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Critical,
                4000,
            )
            return

        tray_icon.showMessage(
            translate("hotkey_not_updated_title"),
            translate("hotkey_kept_old", new_hotkey=new_name, old_hotkey=old_name),
            QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
            3000,
        )

    def schedule_hotkey_reload(_path):
        ensure_watch_targets()
        reload_timer.start()

    watcher.fileChanged.connect(schedule_hotkey_reload)
    watcher.directoryChanged.connect(schedule_hotkey_reload)
    reload_timer.timeout.connect(apply_hotkey_reload)

    def _qt_key_to_hotkey_token(key):
        if QtCore.Qt.Key.Key_A <= key <= QtCore.Qt.Key.Key_Z:
            return chr(key)
        if QtCore.Qt.Key.Key_0 <= key <= QtCore.Qt.Key.Key_9:
            return chr(key)
        if QtCore.Qt.Key.Key_F1 <= key <= QtCore.Qt.Key.Key_F24:
            return f"F{key - QtCore.Qt.Key.Key_F1 + 1}"

        special_map = {
            QtCore.Qt.Key.Key_Escape: "ESC",
            QtCore.Qt.Key.Key_Tab: "TAB",
            QtCore.Qt.Key.Key_Enter: "ENTER",
            QtCore.Qt.Key.Key_Return: "ENTER",
            QtCore.Qt.Key.Key_Space: "SPACE",
            QtCore.Qt.Key.Key_Left: "LEFT",
            QtCore.Qt.Key.Key_Up: "UP",
            QtCore.Qt.Key.Key_Right: "RIGHT",
            QtCore.Qt.Key.Key_Down: "DOWN",
        }
        return special_map.get(key)

    def show_settings_dialog():
        nonlocal settings_dialog, settings_hotkey_label
        if settings_dialog is not None and settings_dialog.isVisible():
            settings_dialog.raise_()
            settings_dialog.activateWindow()
            return

        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle(translate("settings_title"))
        dialog.setModal(False)
        dialog.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        settings_dialog = dialog

        def clear_settings_dialog(_obj=None):
            nonlocal settings_dialog, settings_hotkey_label
            settings_dialog = None
            settings_hotkey_label = None

        dialog.destroyed.connect(clear_settings_dialog)

        layout = QtWidgets.QVBoxLayout(dialog)

        settings_hotkey_label = QtWidgets.QLabel("")
        settings_hotkey_label.setWordWrap(True)
        layout.addWidget(settings_hotkey_label)
        refresh_settings_hotkey_label()

        status_label = QtWidgets.QLabel("")
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        def set_status(message, is_error=False):
            status_label.setText(message)
            status_label.setStyleSheet("color: #B00020;" if is_error else "")

        def capture_hotkey_dialog():
            class HotkeyCaptureDialog(QtWidgets.QDialog):
                def __init__(self, parent=None):
                    super().__init__(parent)
                    self.captured_hotkey = None
                    self.setWindowTitle(translate("settings_hotkey_capture_title"))
                    self.setModal(True)
                    self.setMinimumWidth(340)
                    self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

                    layout = QtWidgets.QVBoxLayout(self)
                    self.hotkey_display = QtWidgets.QLineEdit("")
                    self.hotkey_display.setReadOnly(True)
                    self.hotkey_display.setPlaceholderText(
                        translate("settings_hotkey_capture_placeholder")
                    )
                    self.hotkey_display.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
                    layout.addWidget(self.hotkey_display)

                    self.feedback_label = QtWidgets.QLabel("")
                    self.feedback_label.setWordWrap(True)
                    layout.addWidget(self.feedback_label)

                    button_row = QtWidgets.QHBoxLayout()
                    button_row.addStretch(1)
                    self.save_button = QtWidgets.QPushButton(translate("settings_save_hotkey_btn"))
                    self.save_button.setEnabled(False)
                    self.save_button.clicked.connect(self.accept)
                    button_row.addWidget(self.save_button)
                    cancel_button = QtWidgets.QPushButton(
                        translate("settings_hotkey_capture_cancel_btn")
                    )
                    cancel_button.clicked.connect(self.reject)
                    button_row.addWidget(cancel_button)
                    layout.addLayout(button_row)

                    self._set_feedback(translate("settings_hotkey_capture_waiting"))
                    QtCore.QTimer.singleShot(0, self.setFocus)

                def _set_feedback(self, message, is_error=False):
                    self.feedback_label.setText(message)
                    self.feedback_label.setStyleSheet("color: #B00020;" if is_error else "")

                def keyPressEvent(self, event):
                    modifier_only_keys = {
                        QtCore.Qt.Key.Key_Control,
                        QtCore.Qt.Key.Key_Shift,
                        QtCore.Qt.Key.Key_Alt,
                        QtCore.Qt.Key.Key_Meta,
                        QtCore.Qt.Key.Key_Super_L,
                        QtCore.Qt.Key.Key_Super_R,
                    }

                    key = event.key()
                    if key in modifier_only_keys:
                        self.captured_hotkey = None
                        self.save_button.setEnabled(False)
                        self._set_feedback(translate("settings_hotkey_capture_invalid"), is_error=True)
                        event.accept()
                        return

                    key_token = _qt_key_to_hotkey_token(key)
                    if key_token is None:
                        self.captured_hotkey = None
                        self.save_button.setEnabled(False)
                        self._set_feedback(translate("settings_hotkey_capture_invalid"), is_error=True)
                        event.accept()
                        return

                    modifiers = event.modifiers()
                    modifier_tokens = []
                    if modifiers & QtCore.Qt.KeyboardModifier.ControlModifier:
                        modifier_tokens.append("Ctrl")
                    if modifiers & QtCore.Qt.KeyboardModifier.AltModifier:
                        modifier_tokens.append("Alt")
                    if modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier:
                        modifier_tokens.append("Shift")
                    if modifiers & QtCore.Qt.KeyboardModifier.MetaModifier:
                        modifier_tokens.append("Win")

                    requested_hotkey = "+".join(modifier_tokens + [key_token]) if modifier_tokens else key_token
                    try:
                        _, _, canonical_hotkey = parse_hotkey(requested_hotkey)
                    except Exception as exc:
                        self.captured_hotkey = None
                        self.save_button.setEnabled(False)
                        self._set_feedback(translate("settings_hotkey_invalid", error=exc), is_error=True)
                        event.accept()
                        return

                    self.captured_hotkey = canonical_hotkey
                    self.hotkey_display.setText(canonical_hotkey)
                    self._set_feedback(
                        translate("settings_hotkey_capture_captured", hotkey=canonical_hotkey),
                        is_error=False,
                    )
                    self.save_button.setEnabled(True)
                    event.accept()

            capture_dialog = HotkeyCaptureDialog(dialog)
            if capture_dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                return capture_dialog.captured_hotkey
            return None

        def change_hotkey_from_settings():
            canonical_hotkey = capture_hotkey_dialog()
            if not canonical_hotkey:
                return

            try:
                update_hotkey_in_config(config_path, canonical_hotkey)
            except Exception as exc:
                set_status(translate("settings_hotkey_save_failed", error=exc), is_error=True)
                return

            apply_hotkey_reload()
            refresh_settings_hotkey_label()

            if current_hotkey_name == canonical_hotkey:
                # Keep status area for errors only; current hotkey is shown by the dedicated label.
                set_status("", is_error=False)
            else:
                set_status(
                    translate(
                        "settings_hotkey_apply_failed",
                        old_hotkey=current_hotkey_name,
                        new_hotkey=canonical_hotkey,
                    ),
                    is_error=True,
                )

        button_row = QtWidgets.QHBoxLayout()
        change_hotkey_button = QtWidgets.QPushButton(translate("settings_change_hotkey_btn"))
        change_hotkey_button.clicked.connect(change_hotkey_from_settings)
        change_hotkey_button.setMaximumWidth(140)
        change_hotkey_button.setMaximumHeight(24)
        button_row.addWidget(change_hotkey_button)

        uninstall_button = QtWidgets.QPushButton(translate("uninstall_btn"))
        uninstall_button.clicked.connect(launch_uninstaller)
        uninstall_button.setMaximumWidth(84)
        uninstall_button.setMaximumHeight(24)
        uninstall_button.setStyleSheet(
            "QPushButton { background-color: #C62828; color: white; border: 1px solid #9E1F1F; padding: 2px 8px; }"
            "QPushButton:hover { background-color: #B71C1C; }"
        )
        button_row.addWidget(uninstall_button)

        layout.addLayout(button_row)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    settings_action = tray_menu.addAction(translate("menu_settings"))
    settings_action.triggered.connect(show_settings_dialog)
    config_dir_action = tray_menu.addAction(translate("menu_open_install_dir"))
    config_dir_action.triggered.connect(open_config_dir)

    tray_menu.addSeparator()
    quit_action = tray_menu.addAction(translate("menu_quit"))
    quit_action.triggered.connect(app.quit)

    app.aboutToQuit.connect(unregister_current_hotkey)

    # 安装过滤器捕获热键
    native_hotkey_filter = HotkeyFilter(communicator.trigger)
    app.installNativeEventFilter(native_hotkey_filter)

    sys.exit(app.exec())





