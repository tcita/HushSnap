"""应用入口与主流程协调。"""

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
from .config import get_app_dir
from .constants import CAPTURE_DEBUG_LOG_FILENAME
from .logging_config import setup_logging


def main():
    # 初始化日志系统
    setup_logging(get_app_dir() / CAPTURE_DEBUG_LOG_FILENAME)

    # 检查多开
    instance_lock = is_already_running()
    if not instance_lock:
        return

    # 初始化,后台运行
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 加载配置与 UI 语言
    hotkey_modifier, hotkey_virtual_key, hotkey_name, config_path = load_hotkey_setting()
    ui_language = resolve_ui_lang(config_path)

    def translate(key, **kwargs):
        return ui_text(ui_language, key, **kwargs)

    communicator = Communicator()
    communicator.win = None

    def launch_capture_window():
        if communicator.win:
            return

        screen = QtWidgets.QApplication.primaryScreen()
        # 显式地设置 DPR(windows系统上的屏幕缩放倍数)，确保后续截屏选区计算正确
        device_pixel_ratio = screen.devicePixelRatio()
        screen_pixmap = screen.grabWindow(0)
        screen_pixmap.setDevicePixelRatio(device_pixel_ratio)
        communicator.win = CaptureWindow(screen_pixmap)

        # 利用观察者模式(Qt信号槽)使用闭包确保CaptureWindow关闭并销毁后,将communicator.win 重置为 None
        communicator.win.destroyed.connect(lambda: setattr(communicator, "win", None))
        communicator.win.show()

    communicator.trigger.connect(launch_capture_window)

    # 安装过滤器捕获热键
    native_hotkey_filter = HotkeyFilter(communicator.trigger)
    app.installNativeEventFilter(native_hotkey_filter)

    def open_config_dir():
        try:
            os.startfile(config_path.parent)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                None,
                translate("open_dir_failed"),
                str(exc),
            )

    def on_uninstall():
        launch_uninstaller(translate, app.quit)

    tray_icon, settings_action = create_tray(
        app,
        translate,
        communicator.trigger.emit,
        None,
        open_config_dir,
        app.quit,
    )

    hotkey_manager = HotkeyManager(
        tray_icon,
        translate,
        config_path,
        hotkey_modifier,
        hotkey_virtual_key,
        hotkey_name,
    )
    hotkey_manager.register_initial()
    hotkey_manager.start_watch(app)

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
        settings_action.triggered.connect(settings_controller.show)

    app.aboutToQuit.connect(hotkey_manager.unregister_current_hotkey)


    sys.exit(app.exec())




