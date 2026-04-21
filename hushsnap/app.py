import os
import sys
import logging
import argparse
import time

from PyQt6 import QtWidgets

from .capture_session import CaptureSession
from .config import (
    is_already_running,
    load_hotkey_setting,
    resolve_ui_lang,
    ui_text,
)
from .hotkey import HotkeyFilter
from .ocr_controller import OcrController
from .system.hotkey_manager import HotkeyManager
from .system.uninstall import launch_uninstaller
from .ui.settings_dialog import SettingsDialogController
from .ui.tray import create_tray
from .config import get_user_data_dir
from .constants import CAPTURE_DEBUG_LOG_FILENAME
from .logging_config import setup_logging
from .startup_profiler import StartupProfiler


def exception_hook(exctype, value, tb):
    """
    Global unhandled exception handler.
    Logs the error with stack trace and shows a message box to the user.
    """
    logger = logging.getLogger("HushSnap")
    # 1. Log the full traceback to the log file.
    logger.critical("Unhandled exception occurred:", exc_info=(exctype, value, tb))
    
    # 2. If a QApplication instance exists, show a graphical error dialog.
    if QtWidgets.QApplication.instance():
        # Use a simple dialog as a last resort.
        QtWidgets.QMessageBox.critical(
            None,
            "HushSnap - Critical Error",
            "An unexpected error occurred and the application must close.\n\n"
            "The full error details have been saved to the log file.",
        )
    
    # 3. Fallback to default Python exception behavior.
    sys.__excepthook__(exctype, value, tb)


def main(boot_start_time=None):
    """
    Main application entry point.
    Flow:
    1. Parse CLI arguments for debug mode.
    2. Initialize logging and data directory.
    3. Install global exception hook.
    4. Check single-instance state.
    5. Load user config and i18n resources.
    6. Wire hotkey listener and capture window launch logic.
    7. Build system tray icon and settings dialog.
    8. Start Qt event loop.
    """
    overall_start = time.perf_counter()
    
    # 1. Parse CLI arguments
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--debug", action="store_true")
    args, qt_args = parser.parse_known_args(sys.argv[1:])
    force_debug = args.debug
    save_ocr_debug_image = force_debug
    user_data_dir = get_user_data_dir()
    
    # 2. Initialize logging
    setup_logging(
        user_data_dir / CAPTURE_DEBUG_LOG_FILENAME, 
        force_level=logging.DEBUG if force_debug else None
    )
    logger = logging.getLogger(__name__)
    startup_profiler = StartupProfiler(
        logger,
        overall_start,
        boot_start_time,
        detailed_enabled=force_debug,
    )
    startup_profiler.log_header()
    startup_profiler.log_elapsed("Args parsed and logging setup")

    # 3. Install global exception hook as early as possible after logging is ready.
    sys.excepthook = exception_hook

    with startup_profiler.step("Startup config loaded"):
        hotkey_modifier, hotkey_virtual_key, hotkey_name, config_path = load_hotkey_setting()
    
    if force_debug:
        logger.info("DEBUG MODE ENABLED.")
        print("\n" + "="*80)
        print(f"Config directory: {config_path.parent}")
        print("="*80 + "\n")


    # Enforce single instance via lock/mutex.
    with startup_profiler.step("Process bootstrap ready"):
        instance_lock = is_already_running()
    
    if not instance_lock:
        message = "HushSnap is already running. Exiting this launch."
        logger.warning(message)
        print(message)
        return

    with startup_profiler.step("UI services initialized"):
        # Create the Qt application instance with argv0 and any remaining CLI arguments.
        # (currently usually none unless Qt args are provided).
        app = QtWidgets.QApplication([sys.argv[0], *qt_args])

        # Keep the process alive after all windows are closed.
        app.setQuitOnLastWindowClosed(False)

        # Load config: current hotkey binding and language preference.
        ui_language = resolve_ui_lang(config_path)

        def translate(key, **kwargs):
            return ui_text(ui_language, key, **kwargs)

        ocr_controller = OcrController(
            app=app,
            translate=translate,
            config_path=config_path,
            user_data_dir=user_data_dir,
            save_debug_image=save_ocr_debug_image,
        )

    ocr_action = None

    def on_capture_completed(captured_pixmap):
        """
        Optional OCR flow after screenshot is already copied to clipboard.
        """
        ocr_controller.handle_capture_completed(captured_pixmap)

    capture_session = CaptureSession(on_capture_completed)

    # Install HotkeyFilter to intercept WM_HOTKEY before Qt window event delivery.
    native_hotkey_filter = HotkeyFilter(capture_session.request_capture)
    app.installNativeEventFilter(native_hotkey_filter)

    def open_config_dir():
        """Open the local folder that contains the config file."""
        try:
            os.startfile(config_path.parent)
        except Exception as exc:
            logging.getLogger(__name__).exception(f"Failed to open config dir: {exc}")
            QtWidgets.QMessageBox.warning(
                None,
                translate("open_dir_failed"),
                translate("open_dir_failed_body"),
            )

    def on_uninstall():
        """Uninstall callback."""
        launch_uninstaller(translate, app.quit)

    with startup_profiler.step("Shell integration initialized"):
        # Create system tray icon and right-click menu entry points.
        tray_icon, settings_action, ocr_action = create_tray(
            app,
            translate,
            capture_session.request_capture,  # Allow screenshot trigger from tray menu.
            None,
            open_config_dir,
            app.quit,
        )

        ocr_controller.attach_tray(tray_icon, ocr_action)

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
            logging.getLogger(__name__).exception(f"Failed to initialize settings dialog: {exc}")
            QtWidgets.QMessageBox.warning(
                None,
                translate("error"),
                translate("settings_init_failed"),
            )
            settings_action.setEnabled(False)
        else:
            # Connect tray "Settings" action to controller show method.
            settings_action.triggered.connect(settings_controller.show)

    # Unregister hotkey before app exit.
    app.aboutToQuit.connect(hotkey_manager.unregister_current_hotkey)

    startup_profiler.log_summary()

    # Enter event loop (build_installer.ps1 checks LASTEXITCODE).
    sys.exit(app.exec())
