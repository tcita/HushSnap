import gc
import os
import sys
import logging
import time
import subprocess

from PyQt6 import QtWidgets, QtCore

from .capture_session import CaptureSession
from .config import (
    get_config_path,
    get_debug_enabled,
    get_user_data_dir,
    is_already_running,
    load_hotkey_setting,
    load_ocr_hotkey_setting,
    release_instance_lock,
    resolve_physical_path,
    resolve_ui_lang,
    ui_text,
)
from .hotkey import HotkeyFilter
from .ocr_controller import OcrController
from .system.hotkey_manager import HotkeyManager
from .ui.settings_dialog import SettingsDialogController
from .ui.tray import create_tray
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
    1. Resolve data & config paths, read debug flag from config.
    2. Initialize logging and data directory.
    3. Install global exception hook.
    4. Check single-instance state.
    5. Load user config and i18n resources.
    6. Wire hotkey listener and capture window launch logic.
    7. Build system tray icon and settings dialog.
    8. Start Qt event loop.
    """
    overall_start = time.perf_counter()

    # 1. Resolve paths and read debug flag from config.
    #    The old --debug CLI flag has been removed because MSIX packages
    #    cannot receive command-line arguments. Set ``debug = true`` in
    #    hushsnap_config.toml instead.
    user_data_dir = get_user_data_dir()
    config_path = get_config_path()
    force_debug = get_debug_enabled(config_path)
    save_ocr_debug_image = force_debug

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
    startup_profiler.log_elapsed("Config loaded and logging setup")

    # 3. Install global exception hook as early as possible after logging is ready.
    sys.excepthook = exception_hook

    with startup_profiler.step("Startup config loaded"):
        hotkey_modifier, hotkey_virtual_key, hotkey_name, _ = load_hotkey_setting()
        ocr_hotkey_modifier, ocr_hotkey_virtual_key, ocr_hotkey_name, _ = load_ocr_hotkey_setting(config_path)

    if force_debug:
        logger.info("DEBUG MODE ENABLED (via config).")
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
        app = QtWidgets.QApplication(sys.argv)

        # Give the process a distinct AppUserModelID so Windows shows the
        # application icon in the taskbar instead of the python.exe icon.
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HushSnap")
                logger.debug("AppUserModelID set to HushSnap")
            except Exception:
                pass

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

    def on_capture_completed(captured_pixmap):
        """Callback after screenshot is copied to clipboard."""
        ocr_controller.handle_capture_completed(captured_pixmap)

    def on_ocr_hotkey_triggered(screen_pixmap):
        """OCR screenshot: always run OCR regardless of global toggle state."""
        ocr_controller.enable_ocr_next_capture()
        capture_session.request_capture(screen_pixmap)

    def handle_taskbar_created():
        logger.info("Windows Explorer taskbar recreated. Restoring system tray icon.")
        try:
            # Reference tray_icon, which is bound in the outer scope of main()
            if tray_icon is not None:
                tray_icon.hide()
                tray_icon.show()
        except NameError:
            # tray_icon might not be defined yet during very early startup if a message is received
            pass
        except Exception as exc:
            logger.exception(f"Failed to restore system tray icon: {exc}")

    capture_session = CaptureSession(on_capture_completed)
    ocr_controller.set_capture_requester(capture_session.request_capture)

    # Install HotkeyFilter to intercept WM_HOTKEY before Qt window event delivery.
    native_hotkey_filter = HotkeyFilter(
        on_trigger=capture_session.request_capture,
        on_ocr_trigger=on_ocr_hotkey_triggered,
        on_taskbar_created=handle_taskbar_created,
    )
    app.installNativeEventFilter(native_hotkey_filter)

    def open_config_dir():
        """Open the local folder that contains the config file."""
        try:
            resolved = resolve_physical_path(config_path.parent)
            logging.getLogger(__name__).info(f"Opening config folder. Raw: {config_path.parent}, Resolved: {resolved}")
            os.startfile(resolved)
        except Exception as exc:
            logging.getLogger(__name__).exception(f"Failed to open config dir: {exc}")
            QtWidgets.QMessageBox.warning(
                None,
                translate("open_dir_failed"),
                translate("open_dir_failed_body"),
            )

    with startup_profiler.step("Shell integration initialized"):
        # Create system tray icon and right-click menu entry points.
        tray_icon, settings_action = create_tray(
            app,
            translate,
            capture_session.request_capture,  # Allow screenshot trigger from tray menu.
            None,
            open_config_dir,
            app.quit,
            on_ocr_trigger=on_ocr_hotkey_triggered,
            initial_hotkey=hotkey_name,
            initial_ocr_hotkey=ocr_hotkey_name,
        )

        ocr_controller.tray_icon = tray_icon

        # Show tray icon once OCR engine warmup completes.
        # The icon is created hidden; this connection makes it appear
        # when the app is truly ready — no extra notification needed.
        ocr_controller.bridge.warmup_finished.connect(tray_icon.show)

        # Hotkey manager handles registration/unregistration with Windows.
        hotkey_manager = HotkeyManager(
            tray_icon,
            translate,
            config_path,
            hotkey_modifier,
            hotkey_virtual_key,
            hotkey_name,
            ocr_modifier=ocr_hotkey_modifier,
            ocr_virtual_key=ocr_hotkey_virtual_key,
            ocr_name=ocr_hotkey_name,
        )
        hotkey_manager.register_initial()
        hotkey_manager.register_ocr_initial()
        hotkey_manager.start_watch(app) # Start config-change watcher.

        # Set hotkey IDs on the native event filter so it can route WM_HOTKEY events.
        native_hotkey_filter.hotkey_id = hotkey_manager.hotkey_id
        native_hotkey_filter.ocr_hotkey_id = hotkey_manager.ocr_hotkey_id

        # Initialize settings dialog controller.
        try:
            settings_controller = SettingsDialogController(
                translate,
                config_path,
                hotkey_manager,
                on_font_size_changed=ocr_controller.popup.apply_font_size,
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

            def handle_restart():
                logger.info("Language changed. Restarting application...")
                hotkey_manager.release_resources()
                # Use QTimer to ensure the event loop processes the close before restart
                QtCore.QTimer.singleShot(0, lambda: _restart_app(app, instance_lock))

            settings_controller.language_changed.connect(handle_restart)

        # Show conflict resolution dialog if any hotkey failed at startup.
        # This must happen after settings_controller is wired so "Open Settings" works.
        hotkey_manager.resolve_startup_conflicts(lambda: settings_controller.show())

    # Unregister hotkey and release system resources before app exit.
    app.aboutToQuit.connect(hotkey_manager.release_resources)

    startup_profiler.log_summary()

    sys.exit(app.exec())


def _restart_app(app, instance_lock):
    """Restart the current application process."""
    logger = logging.getLogger(__name__)
    logger.info("Application is restarting due to language change.")

    executable = sys.executable
    args = sys.argv[:]
    
    # CRITICAL: Release the single-instance lock so the NEW process can start.
    from .config import release_instance_lock
    release_instance_lock(instance_lock)
    
    app.quit()
    
    # On Windows, subprocess.Popen is more reliable for restarts as it ensures
    # the new process starts independently while this one finishes exiting.
    subprocess.Popen([executable] + args)
    sys.exit(0)
