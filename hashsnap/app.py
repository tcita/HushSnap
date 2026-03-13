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


def main():
    # 1. 检查多开
    instance_lock = is_already_running()
    if not instance_lock:
        return

    # 2. 初始化,后台运行
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 3. 加载配置与 UI 语言
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
        
        # 利用信号和闭包确保CaptureWindow关闭并销毁后  communicator.win 重置为 None
        communicator.win.destroyed.connect(lambda: setattr(communicator, "win", None))
        communicator.win.show()

    communicator.trigger.connect(launch_capture_window)

    def open_config_dir():
        try:
            os.startfile(config_path.parent)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                None,
                translate("open_dir_failed"),
                str(exc),
            )

    settings_controller = None

    def show_settings_dialog():
        if settings_controller is not None:
            settings_controller.show()

    tray_icon = create_tray(
        app,
        translate,
        communicator.trigger.emit,
        show_settings_dialog,
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

    def on_uninstall():
        launch_uninstaller(translate, app.quit)

    settings_controller = SettingsDialogController(
        translate,
        config_path,
        hotkey_manager,
        on_uninstall,
    )

    app.aboutToQuit.connect(hotkey_manager.unregister_current_hotkey)

    # 安装过滤器捕获热键
    native_hotkey_filter = HotkeyFilter(communicator.trigger)
    app.installNativeEventFilter(native_hotkey_filter)

    sys.exit(app.exec())