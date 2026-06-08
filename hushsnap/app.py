import gc
import os
import sys
import logging
import time
import tempfile
import subprocess

from PyQt6 import QtWidgets, QtCore

from .capture_session import CaptureSession
from .config import (
    get_app_id,
    get_config_path,
    get_debug_enabled,
    get_user_data_dir,
    is_already_running,
    load_hotkey_setting,
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
from .ui.thumbnail import show_thumbnail, qpixmap_to_pil
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
        
        # Set internal Qt application identity
        app_id = get_app_id()
        app.setApplicationName(app_id)
        app.setOrganizationName("HushSnap")

        # Give the process a distinct AppUserModelID so Windows shows the
        # application icon in the taskbar instead of the python.exe icon.
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
                logger.debug(f"AppUserModelID set to {app_id}")
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
        # Only show thumbnail if this is NOT an OCR capture to avoid distraction.
        # Defer via singleShot so CaptureWindow has fully closed before the
        # thumbnail appears — avoids a DWM focus-race in the MSIX container
        # that causes the thumbnail to flash and immediately dismiss.
        if not ocr_controller.next_capture_should_ocr:
            try:
                pil_img = qpixmap_to_pil(captured_pixmap)
                QtCore.QTimer.singleShot(50, lambda img=pil_img: _show_thumbnail_safe(img))
            except Exception:
                logging.getLogger(__name__).exception("Failed to show thumbnail")

        ocr_controller.handle_capture_completed(captured_pixmap)

    def _show_thumbnail_safe(pil_img):
        try:
            show_thumbnail(pil_img)
        except Exception:
            logging.getLogger(__name__).exception("Failed to show thumbnail (deferred)")

    # --- Thumbnail Interaction Handlers ---
    from .ui.thumbnail import thumbnail_manager

    def handle_thumbnail_clicked(pil_img):
        """Thumbnail left-click: trigger OCR."""
        # Convert PIL Image to QPixmap for the OCR pipeline
        from PyQt6 import QtGui
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimage = QtGui.QImage(
            data, pil_img.size[0], pil_img.size[1],
            QtGui.QImage.Format.Format_RGBA8888,
        ).copy()
        qpixmap = QtGui.QPixmap.fromImage(qimage)
        ocr_controller.start_request(qpixmap)

    def handle_open_viewer(pil_img):
        """Thumbnail context menu: Open with default image viewer."""
        try:
            temp_path = os.path.join(tempfile.gettempdir(), f"view_{int(time.time())}.png")
            pil_img.save(temp_path)
            os.startfile(temp_path)
        except Exception:
            logging.getLogger(__name__).exception("Failed to open image in viewer")

    def handle_save_to_desktop(pil_img):
        """Thumbnail context menu: Save to Desktop."""
        try:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            timestamp = time.strftime("%m%d_%H-%M-%S")
            base = f"_{timestamp}"
            file_path = os.path.join(desktop, f"{base}.png")
            counter = 1
            while os.path.exists(file_path):
                file_path = os.path.join(desktop, f"{base}({counter}).png")
                counter += 1
            pil_img.save(file_path)
        except Exception:
            logging.getLogger(__name__).exception("Failed to save image to desktop")

    def handle_thumbnail_save(pil_img):
        """Thumbnail context menu: Save As..."""
        try:
            default_name = f"_{time.strftime('%m%d_%H-%M-%S')}.png"
            file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                None, translate("thumbnail_save_as"), default_name, "Images (*.png *.jpg *.bmp)"
            )
            if file_path:
                pil_img.save(file_path)
                os.startfile(os.path.dirname(file_path))
        except Exception:
            logging.getLogger(__name__).exception("Failed to save image")

    thumbnail_manager.clicked.connect(handle_thumbnail_clicked)
    thumbnail_manager.open_viewer.connect(handle_open_viewer)
    thumbnail_manager.save_to_desktop.connect(handle_save_to_desktop)
    thumbnail_manager.save_requested.connect(handle_thumbnail_save)

    from .ui.pinned_image import pinned_image_manager
    thumbnail_manager.pin_requested.connect(pinned_image_manager.pin_image)
    pinned_image_manager.ocr_requested.connect(ocr_controller.start_request)

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
            initial_hotkey=hotkey_name,
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
        )
        hotkey_manager.register_initial()
        hotkey_manager.start_watch(app) # Start config-change watcher.

        # Set hotkey IDs on the native event filter so it can route WM_HOTKEY events.
        native_hotkey_filter.hotkey_id = hotkey_manager.hotkey_id

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
