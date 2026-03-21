import os
import sys

from PyQt6 import QtWidgets

from .capture_window import CaptureWindow
from .config import (
    is_already_running,
    load_hotkey_setting,
    resolve_ui_lang,
    ui_text,
)
from .hotkey import Communicator, HotkeyFilter
from .system.hotkey_manager import HotkeyManager
from .system.uninstall import launch_uninstaller
from .ui.settings_dialog import SettingsDialogController
from .ui.tray import create_tray
from .config import get_user_data_dir
from .constants import CAPTURE_DEBUG_LOG_FILENAME
from .logging_config import setup_logging


"""
负责初始化应用环境、配置加载、热键注册及托盘菜单的构建。
"""

import os
import sys

from PyQt6 import QtWidgets

from .capture_window import CaptureWindow
from .config import (
    is_already_running,
    load_hotkey_setting,
    resolve_ui_lang,
    ui_text,
)
from .hotkey import Communicator, HotkeyFilter
from .system.hotkey_manager import HotkeyManager
from .system.uninstall import launch_uninstaller
from .ui.settings_dialog import SettingsDialogController
from .ui.tray import create_tray
from .config import get_user_data_dir
from .constants import CAPTURE_DEBUG_LOG_FILENAME
from .logging_config import setup_logging


def main():
    """
    应用程序主入口函数。
    执行流程：
    1. 初始化日志与数据目录。
    2. 检查单实例运行状态。
    3. 加载用户配置与多语言资源。
    4. 建立热键监听与截图窗口唤起逻辑。
    5. 构建系统托盘图标与设置对话框。
    6. 启动 Qt 事件循环。
    """
    # 初始化日志系统：确保所有运行信息都被记录到本地用户数据目录
    setup_logging(get_user_data_dir() / CAPTURE_DEBUG_LOG_FILENAME)

    # 检查多开：利用文件锁或系统互斥量确保同时只有一个实例在运行
    instance_lock = is_already_running()
    if not instance_lock:
        return

    # 初始化 Qt 环境：设置为后台运行模式
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 加载配置：获取当前绑定的热键组合及语言偏好
    hotkey_modifier, hotkey_virtual_key, hotkey_name, config_path = load_hotkey_setting()
    ui_language = resolve_ui_lang(config_path)

    def translate(key, **kwargs):
        return ui_text(ui_language, key, **kwargs)

    # 热键事件 -> Qt 信号(communicator) -> UI 回调
    communicator = Communicator()
    communicator.win = None 

    def launch_capture_window(screen_pixmap):
        """
        唤起截图窗口的回调逻辑。
        :param screen_pixmap: 预先捕获的全屏位图（由 HotkeyFilter 提供）
        """
        if communicator.win:
            return 

        # 创建截图窗口实例
        communicator.win = CaptureWindow(screen_pixmap)

        # CaptureWindow销毁后发送信号,将communicator.win 重置为 None
        communicator.win.destroyed.connect(lambda: setattr(communicator, "win", None))
        communicator.win.show()


    # 将launch_capture_window注册到 communicator 对象的 trigger 信号的监听列表里
    communicator.trigger.connect(launch_capture_window)

    # 安装hotkey.py中的HotkeyFilter,在 Qt 事件到达窗口前拦截 Win32 热键消息 (WM_HOTKEY)
    native_hotkey_filter = HotkeyFilter(communicator.trigger)
    app.installNativeEventFilter(native_hotkey_filter)

    def open_config_dir():
        """打开配置文件所在的本地文件夹。"""
        try:
            os.startfile(config_path.parent)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                None,
                translate("open_dir_failed"),
                str(exc),
            )

    def on_uninstall():
        """卸载回调：清理注册表与文件系统。"""
        launch_uninstaller(translate, app.quit)

    # 创建系统托盘图标：提供右键菜单访问入口
    tray_icon, settings_action = create_tray(
        app,
        translate,
        communicator.trigger.emit, # 允许从托盘菜单触发截图
        None,
        open_config_dir,
        app.quit,
    )

    # 热键管理器：负责向 Windows 系统注册/注销热键
    hotkey_manager = HotkeyManager(
        tray_icon,
        translate,
        config_path,
        hotkey_modifier,
        hotkey_virtual_key,
        hotkey_name,
    )
    hotkey_manager.register_initial()
    hotkey_manager.start_watch(app) # 启动配置变更监听

    # 初始化设置对话框控制器
    try:
        settings_controller = SettingsDialogController(
            translate,
            config_path,
            hotkey_manager,
            on_uninstall,
        )
    except Exception as exc:
        QtWidgets.QMessageBox.warning(
            None,
            translate("error"),
            translate("settings_init_failed", error=exc),
        )
        settings_action.setEnabled(False)
    else:
        # 将托盘菜单的“设置”项连接到控制器的显示方法
        settings_action.triggered.connect(settings_controller.show)

    # 程序退出前注销热键
    app.aboutToQuit.connect(hotkey_manager.unregister_current_hotkey)

    # 进入事件循环,构建脚本build_installer.ps1会检查状态码LASTEXITCODE
    sys.exit(app.exec())




