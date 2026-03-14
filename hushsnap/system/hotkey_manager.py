"""全局热键注册与监听管理。"""

import ctypes

from PyQt6 import QtCore, QtWidgets

from ..config import parse_hotkey, read_hotkey_text_from_config
from ..constants import (
    RELOAD_TIMER_MS,
    TRAY_MSG_LONG_MS,
    TRAY_MSG_MEDIUM_MS,
    TRAY_MSG_SHORT_MS,
)


class HotkeyManager:
    def __init__(self, tray_icon, translate, config_path, modifier, virtual_key, name):
        self.tray_icon = tray_icon
        self.translate = translate
        self.config_path = config_path
        self.hotkey_registered = False

        # 使用 GlobalAddAtom 生成系统级唯一的热键 ID
        # 字符串 "HushSnap_Hotkey_Atom" 用于生成该原子，
        # 在 0xC000 到 0xFFFF 范围内返回一个唯一的 ID。
        self.hotkey_id = ctypes.windll.kernel32.GlobalAddAtomW("HushSnap_Hotkey_Atom")
        if not self.hotkey_id:
            # 回退到固定 ID，以防 GlobalAddAtom 失败
            self.hotkey_id = 0xBFFF

        self.current_hotkey_modifier = modifier
        self.current_hotkey_virtual_key = virtual_key
        self.current_hotkey_name = name

        self._watcher = None
        self._reload_timer = None
        self._config_file_path_str = str(config_path)
        self._config_dir_path_str = str(config_path.parent)

    def register_initial(self):
        if not ctypes.windll.user32.RegisterHotKey(
            None,
            self.hotkey_id,
            self.current_hotkey_modifier,
            self.current_hotkey_virtual_key,
        ):
            QtWidgets.QMessageBox.warning(
                None,
                self.translate("error"),
                self.translate(
                    "hotkey_taken",
                    hotkey=self.current_hotkey_name,
                    config_path=self.config_path,
                ),
            )
            self.hotkey_registered = False
            return False

        self.hotkey_registered = True
        return True

    def unregister_current_hotkey(self):
        if self.hotkey_registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.hotkey_id)
            self.hotkey_registered = False
        
        # 清理原子 ID
        if hasattr(self, "hotkey_id") and self.hotkey_id:
            ctypes.windll.kernel32.GlobalDeleteAtom(self.hotkey_id)
            self.hotkey_id = 0

    def register_hotkey(self, modifier, virtual_key, name):
        if ctypes.windll.user32.RegisterHotKey(None, self.hotkey_id, modifier, virtual_key):
            self.hotkey_registered = True
            self.current_hotkey_modifier = modifier
            self.current_hotkey_virtual_key = virtual_key
            self.current_hotkey_name = name
            return True
        return False

    def _ensure_watch_targets(self):
        if self._config_dir_path_str not in self._watcher.directories():
            self._watcher.addPath(self._config_dir_path_str)
        if self.config_path.exists() and self._config_file_path_str not in self._watcher.files():
            self._watcher.addPath(self._config_file_path_str)

    def apply_hotkey_reload(self):
        self._ensure_watch_targets()
        try:
            new_modifier, new_virtual_key, new_name = parse_hotkey(
                read_hotkey_text_from_config(self.config_path)
            )
        except Exception as exc:
            self.tray_icon.showMessage(
                self.translate("hotkey_not_updated_title"),
                self.translate("hotkey_invalid_config", hotkey=self.current_hotkey_name, error=exc),
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                TRAY_MSG_MEDIUM_MS,
            )
            return

        if (
            new_modifier == self.current_hotkey_modifier
            and new_virtual_key == self.current_hotkey_virtual_key
        ):
            if self.hotkey_registered:
                return
            if self.register_hotkey(new_modifier, new_virtual_key, new_name):
                self.tray_icon.showMessage(
                    self.translate("hotkey_enabled_title"),
                    self.translate("hotkey_enabled", hotkey=new_name),
                    QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                    TRAY_MSG_SHORT_MS,
                )
            else:
                self.tray_icon.showMessage(
                    self.translate("hotkey_not_updated_title"),
                    self.translate("hotkey_still_occupied", hotkey=new_name),
                    QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                    TRAY_MSG_MEDIUM_MS,
                )
            return

        old_modifier, old_virtual_key, old_name = (
            self.current_hotkey_modifier,
            self.current_hotkey_virtual_key,
            self.current_hotkey_name,
        )
        self.unregister_current_hotkey()
        if self.register_hotkey(new_modifier, new_virtual_key, new_name):
            self.tray_icon.showMessage(
                self.translate("hotkey_updated_title"),
                self.translate("hotkey_updated", old_hotkey=old_name, new_hotkey=new_name),
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                TRAY_MSG_SHORT_MS,
            )
            return

        # 新热键注册失败，回滚旧热键。
        if not self.register_hotkey(old_modifier, old_virtual_key, old_name):
            self.tray_icon.showMessage(
                self.translate("hotkey_error_title"),
                self.translate("hotkey_recover_failed"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Critical,
                TRAY_MSG_LONG_MS,
            )
            return

        self.tray_icon.showMessage(
            self.translate("hotkey_not_updated_title"),
            self.translate("hotkey_kept_old", new_hotkey=new_name, old_hotkey=old_name),
            QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
            TRAY_MSG_MEDIUM_MS,
        )

    def schedule_hotkey_reload(self, _path):
        self._ensure_watch_targets()
        self._reload_timer.start()

    def start_watch(self, app):
        self._watcher = QtCore.QFileSystemWatcher(app)
        self._watcher.addPath(self._config_dir_path_str)
        if self.config_path.exists():
            self._watcher.addPath(self._config_file_path_str)

        self._reload_timer = QtCore.QTimer(app)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(RELOAD_TIMER_MS)

        self._watcher.fileChanged.connect(self.schedule_hotkey_reload)
        self._watcher.directoryChanged.connect(self.schedule_hotkey_reload)
        self._reload_timer.timeout.connect(self.apply_hotkey_reload)

