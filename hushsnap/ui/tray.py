"""
HushSnap system tray module.
Creates tray icon, context menu (right-click menu), and tray interaction logic.
"""

from PyQt6 import QtGui, QtWidgets

from ..config import get_resource_dir
from ..constants import APP_ICON_FILENAME


def create_tray(
    app,
    translate,
    on_trigger,
    on_open_settings,
    on_open_config_dir,
    on_quit,
):
    """
    Initialize and create the system tray icon and its menu.
    
    Args:
        app (QApplication): Application instance.
        translate (callable): Translation function.
        on_trigger (callable): Callback to trigger screenshot from tray.
        on_open_settings (callable): Callback to open settings window.
        on_open_config_dir (callable): Callback to open config directory.
        on_quit (callable): Callback to quit application.
        
    Returns:
        tuple: (tray_icon, settings_action, ocr_action) for later dynamic operations.
    """
    # Load tray icon.
    tray_icon_image = QtGui.QIcon(str(get_resource_dir() / APP_ICON_FILENAME))
    app.setWindowIcon(tray_icon_image)
    
    # Create tray icon object.
    tray_icon = QtWidgets.QSystemTrayIcon(tray_icon_image, app)
    # Create right-click context menu.
    tray_menu = QtWidgets.QMenu()
    tray_icon.setContextMenu(tray_menu)
    tray_icon.show()

    def on_tray_icon_activated(reason):
        """
        Handle tray icon activation.
        If user single-clicks (Trigger), start screenshot flow directly.
        """
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen:
                dpr = screen.devicePixelRatio()
                # Capture current screen state.
                pixmap = screen.grabWindow(0)
                pixmap.setDevicePixelRatio(dpr)
                # Trigger screenshot selection UI.
                on_trigger(pixmap)

    # Bind activation event (e.g. single click).
    tray_icon.activated.connect(on_tray_icon_activated)

    # Add menu actions.
    settings_action = tray_menu.addAction(translate("menu_settings"))
    if on_open_settings is not None:
        settings_action.triggered.connect(on_open_settings)

    ocr_action = tray_menu.addAction(translate("menu_ocr_recognize"))
    ocr_action.setCheckable(True)
    ocr_action.setChecked(False)
    
    config_dir_action = tray_menu.addAction(translate("menu_open_install_dir"))
    config_dir_action.triggered.connect(on_open_config_dir)

    tray_menu.addSeparator()
    
    quit_action = tray_menu.addAction(translate("menu_quit"))
    quit_action.triggered.connect(on_quit)

    return tray_icon, settings_action, ocr_action
