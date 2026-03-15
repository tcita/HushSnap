"""
HushSnap 热键管理器模块
负责 Windows 全局热键的注册、注销、冲突处理以及配置文件的动态监听与自动重载。
"""

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
    """
    管理 Windows 全局热键的类。
    支持热键的注册、注销、动态更新以及配置文件变动后的自动重载。
    """
    def __init__(self, tray_icon, translate, config_path, modifier, virtual_key, name):
        """
        初始化热键管理器。
        
        Args:
            tray_icon (QSystemTrayIcon): 托盘图标实例，用于弹出提示消息。
            translate (callable): 翻译函数，用于多语言显示。
            config_path (Path): 配置文件路径。
            modifier (int): 初始修饰键掩码。
            virtual_key (int): 初始虚拟键码。
            name (str): 初始热键的人类可读名称。
        """
        self.tray_icon = tray_icon
        self.translate = translate
        self.config_path = config_path
        self.hotkey_registered = False

        # 使用 GlobalAddAtom 生成系统级唯一的热键 ID。
        # 字符串 "HushSnap_Hotkey_Atom" 用于生成该原子，
        # 在 0xC000 到 0xFFFF 范围内返回一个唯一的 ID。
        # 这样可以避免与系统中其他程序注册的热键 ID 冲突。
        self.hotkey_id = ctypes.windll.kernel32.GlobalAddAtomW("HushSnap_Hotkey_Atom")
        if not self.hotkey_id:
            # 如果原子生成失败，回退到固定的 ID。
            self.hotkey_id = 0xBFFF

        self.current_hotkey_modifier = modifier
        self.current_hotkey_virtual_key = virtual_key
        self.current_hotkey_name = name

        self._watcher = None
        self._reload_timer = None
        self._config_file_path_str = str(config_path)
        self._config_dir_path_str = str(config_path.parent)

    def register_initial(self):
        """
        程序启动时的首次热键注册。
        
        Returns:
            bool: 注册成功返回 True，否则弹出警告并返回 False。
        """
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
        """注销当前已注册的热键，并清理系统资源。"""
        if self.hotkey_registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.hotkey_id)
            self.hotkey_registered = False
        
        # 清理原子 ID
        if hasattr(self, "hotkey_id") and self.hotkey_id:
            ctypes.windll.kernel32.GlobalDeleteAtom(self.hotkey_id)
            self.hotkey_id = 0

    def register_hotkey(self, modifier, virtual_key, name):
        """
        尝试注册一个新的热键。
        
        Args:
            modifier (int): 修饰键掩码。
            virtual_key (int): 虚拟键码。
            name (str): 热键名称。
            
        Returns:
            bool: 注册成功返回 True，否则返回 False。
        """
        if ctypes.windll.user32.RegisterHotKey(None, self.hotkey_id, modifier, virtual_key):
            self.hotkey_registered = True
            self.current_hotkey_modifier = modifier
            self.current_hotkey_virtual_key = virtual_key
            self.current_hotkey_name = name
            return True
        return False

    def _ensure_watch_targets(self):
        """确保 QFileSystemWatcher 监听的目标路径仍然有效（防止文件被删除又重建导致监听失效）。"""
        if self._config_dir_path_str not in self._watcher.directories():
            self._watcher.addPath(self._config_dir_path_str)
        if self.config_path.exists() and self._config_file_path_str not in self._watcher.files():
            self._watcher.addPath(self._config_file_path_str)

    def apply_hotkey_reload(self):
        """
        执行热键重载逻辑。
        从配置文件读取新设置，并尝试注销旧热键、注册新热键。
        如果新热键失败，会尝试回滚到旧热键。
        """
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

        # 如果热键设置没变且已激活，直接跳过
        if (
            new_modifier == self.current_hotkey_modifier
            and new_virtual_key == self.current_hotkey_virtual_key
        ):
            if self.hotkey_registered:
                return
            # 如果之前处于未激活状态（如因冲突失败），尝试重新激活
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

        # 热键设置已更改，执行更新流程
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

        # 新热键注册失败，尝试恢复旧热键。
        if not self.register_hotkey(old_modifier, old_virtual_key, old_name):
            self.tray_icon.showMessage(
                self.translate("hotkey_error_title"),
                self.translate("hotkey_recover_failed"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Critical,
                TRAY_MSG_LONG_MS,
            )
            return

        # 恢复成功，但告知用户新热键被占用了
        self.tray_icon.showMessage(
            self.translate("hotkey_not_updated_title"),
            self.translate("hotkey_kept_old", new_hotkey=new_name, old_hotkey=old_name),
            QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
            TRAY_MSG_MEDIUM_MS,
        )

    def schedule_hotkey_reload(self, _path):
        """调度热键重载（通过定时器实现防抖，避免编辑器保存时的多次触发）。"""
        self._ensure_watch_targets()
        self._reload_timer.start()

    def start_watch(self, app):
        """
        开始监听配置文件变动。
        
        Args:
            app (QApplication): Qt 应用程序实例。
        """
        self._watcher = QtCore.QFileSystemWatcher(app)
        self._watcher.addPath(self._config_dir_path_str)
        if self.config_path.exists():
            self._watcher.addPath(self._config_file_path_str)

        # 定时器用于“防抖”重载逻辑
        self._reload_timer = QtCore.QTimer(app)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(RELOAD_TIMER_MS)

        self._watcher.fileChanged.connect(self.schedule_hotkey_reload)
        self._watcher.directoryChanged.connect(self.schedule_hotkey_reload)
        self._reload_timer.timeout.connect(self.apply_hotkey_reload)
