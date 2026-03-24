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
    Main application entry point.
    Flow:
    1. Initialize logging and data directory.
    2. Check single-instance state.
    3. Load user config and i18n resources.
    4. Wire hotkey listener and capture window launch logic.
    5. Build system tray icon and settings dialog.
    6. Start Qt event loop.
    """
    # Initialize logging so runtime information is written to the local user data directory.
    setup_logging(get_user_data_dir() / CAPTURE_DEBUG_LOG_FILENAME)

    # Enforce single instance via lock/mutex.
    instance_lock = is_already_running()
    if not instance_lock:
        return

    # Initialize Qt environment for background-style behavior.
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Load config: current hotkey binding and language preference.
    hotkey_modifier, hotkey_virtual_key, hotkey_name, config_path = load_hotkey_setting()
    ui_language = resolve_ui_lang(config_path)

    def translate(key, **kwargs):
        return ui_text(ui_language, key, **kwargs)

    # Hotkey event -> Qt signal (communicator) -> UI callback
    communicator = Communicator()
    communicator.win = None 

    def launch_capture_window(screen_pixmap):
        """
        Callback that launches the capture window.
        :param screen_pixmap: Pre-captured fullscreen bitmap from HotkeyFilter.
        """
        if communicator.win:
            return 

        # Create capture window instance.
        communicator.win = CaptureWindow(screen_pixmap)

        # Reset communicator.win to None when CaptureWindow is destroyed.
        communicator.win.destroyed.connect(lambda: setattr(communicator, "win", None))
        communicator.win.show()


    # Connect launch_capture_window to communicator's trigger signal.
    communicator.trigger.connect(launch_capture_window)

    # Install HotkeyFilter to intercept WM_HOTKEY before Qt window event delivery.
    native_hotkey_filter = HotkeyFilter(communicator.trigger)
    app.installNativeEventFilter(native_hotkey_filter)

    def open_config_dir():
        """Open the local folder that contains the config file."""
        try:
            os.startfile(config_path.parent)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                None,
                translate("open_dir_failed"),
                str(exc),
            )

    def on_uninstall():
        """Uninstall callback."""
        launch_uninstaller(translate, app.quit)

    # Create system tray icon and right-click menu entry points.
    tray_icon, settings_action = create_tray(
        app,
        translate,
        communicator.trigger.emit, # Allow screenshot trigger from tray menu.
        None,
        open_config_dir,
        app.quit,
    )

    # Hotkey manager handles registration/unregistration with Windows.
    hotkey_manager = HotkeyManager(
        tray_icon,
        translate,
        config_path,
        hotkey_modifier,
        hotkey_virtual_key,
        hotkey_name,
    )
    hotkey_manager.register_initial()
    hotkey_manager.start_watch(app) # Start config-change watcher.

    # Initialize settings dialog controller.
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
        # Connect tray "Settings" action to controller show method.
        settings_action.triggered.connect(settings_controller.show)

    # Unregister hotkey before app exit.
    app.aboutToQuit.connect(hotkey_manager.unregister_current_hotkey)

    # Enter event loop (build_installer.ps1 checks LASTEXITCODE).
    sys.exit(app.exec())




