"""系统托盘菜单与动作。"""

from PyQt6 import QtGui, QtWidgets

from ..config import get_resource_dir
from ..constants import APP_ICON_FILENAME


def create_tray(app, translate, on_trigger, on_open_settings, on_open_config_dir, on_quit):
    tray_icon_image = QtGui.QIcon(str(get_resource_dir() / APP_ICON_FILENAME))
    app.setWindowIcon(tray_icon_image)
    tray_icon = QtWidgets.QSystemTrayIcon(tray_icon_image, app)
    tray_menu = QtWidgets.QMenu()
    tray_icon.setContextMenu(tray_menu)
    tray_icon.show()

    def on_tray_icon_activated(reason):
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen:
                dpr = screen.devicePixelRatio()
                pixmap = screen.grabWindow(0)
                pixmap.setDevicePixelRatio(dpr)
                on_trigger(pixmap)

    tray_icon.activated.connect(on_tray_icon_activated)

    settings_action = tray_menu.addAction(translate("menu_settings"))
    if on_open_settings is not None:
        settings_action.triggered.connect(on_open_settings)
    config_dir_action = tray_menu.addAction(translate("menu_open_install_dir"))
    config_dir_action.triggered.connect(on_open_config_dir)

    tray_menu.addSeparator()
    quit_action = tray_menu.addAction(translate("menu_quit"))
    quit_action.triggered.connect(on_quit)

    return tray_icon, settings_action

