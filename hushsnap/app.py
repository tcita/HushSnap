import gc
import os
import shutil
import sys
import logging
import time
import tempfile
import subprocess
from pathlib import Path

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

# Module-level state set during main() so exception_hook can access them.
_log_file_path = None
_translate = None


def exception_hook(exctype, value, tb):
    """
    Global unhandled exception handler.
    Logs the error with stack trace and shows a message box to the user
    with options to save or copy the log file.
    """
    logger = logging.getLogger("HushSnap")
    # 1. Log the full traceback to the log file.
    logger.critical("Unhandled exception occurred:", exc_info=(exctype, value, tb))

    # 2. If a QApplication instance exists, show a graphical error dialog.
    if QtWidgets.QApplication.instance():
        t = _translate or (lambda key, **kw: ui_text("en", key, **kw))
        log_path_str = str(_log_file_path) if _log_file_path else ""

        msg_box = QtWidgets.QMessageBox()
        msg_box.setIcon(QtWidgets.QMessageBox.Icon.Critical)
        msg_box.setWindowTitle(t("crash_title"))
        msg_box.setText(t("crash_body", log_path=log_path_str))

        save_btn = msg_box.addButton(
            t("crash_save_log"), QtWidgets.QMessageBox.ButtonRole.AcceptRole
        )
        copy_btn = msg_box.addButton(
            t("crash_copy_log"), QtWidgets.QMessageBox.ButtonRole.ActionRole
        )
        close_btn = msg_box.addButton(
            t("crash_close"), QtWidgets.QMessageBox.ButtonRole.RejectRole
        )
        msg_box.setDefaultButton(save_btn)

        msg_box.exec()

        clicked = msg_box.clickedButton()
        if clicked == save_btn and _log_file_path:
            try:
                _save_log_to_desktop(_log_file_path)
            except Exception:
                logger.exception("Failed to save log to desktop")
        elif clicked == copy_btn and _log_file_path:
            try:
                _copy_log_tail(_log_file_path)
            except Exception:
                logger.exception("Failed to copy log to clipboard")

    # 3. Fallback to default Python exception behavior.
    sys.__excepthook__(exctype, value, tb)


def _save_log_to_desktop(log_path):
    """Copy the full log file to the user's desktop."""
    desktop = Path.home() / "Desktop"
    dest = desktop / "HushSnap_crash.log"
    shutil.copy2(log_path, dest)


# String that marks the start of a new HushSnap session in the log.
# Must match the first log message emitted in logging_config.setup_logging().
_SESSION_START_MARKER = "Logging initialized. Level:"

def _copy_log_tail(log_path):
    """Copy the current-session portion of the log to the system clipboard.

    Finds the last occurrence of the startup marker and copies everything
    from there to end-of-file — that way the user only shares this session's
    log, not previous runs that may still be in the rotated file.
    """
    app = QtWidgets.QApplication.instance()
    if not app:
        return
    clipboard = app.clipboard()
    if not clipboard:
        return
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        text = ""
    # Slice from the LAST session start to end-of-file.
    idx = text.rfind(_SESSION_START_MARKER)
    if idx != -1:
        text = text[idx:]
    clipboard.setText(text)


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
    global _log_file_path
    _log_file_path = user_data_dir / CAPTURE_DEBUG_LOG_FILENAME
    setup_logging(
        _log_file_path,
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

        global _translate
        _translate = translate

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
        """Thumbnail left-click: trigger OCR with smooth position transition."""
        # Switch thumbnail to loading state — it stays visible during OCR
        thumb_win = thumbnail_manager.current_window()
        if thumb_win:
            thumb_win.start_loading()

        # Capture thumbnail geometry so the OCR popup can morph from it
        pos, size = thumbnail_manager.current_window_rect()
        if pos and size:
            ocr_controller.set_popup_anchor(
                pos.x() + size.width() / 2,
                pos.y() + size.height() / 2,
                width=size.width(),
                height=size.height()
            )
        else:
            center = thumbnail_manager.current_window_center()
            if center is not None:
                ocr_controller.set_popup_anchor(*center)

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
    
    def handle_pin_requested(pil_img, pos, size):
        """Thumbnail 'Pin' action: animate from thumbnail to pinned window."""
        pinned_image_manager.pin_image(pil_img, morph_pos=pos, morph_size=size)

    thumbnail_manager.pin_requested.connect(handle_pin_requested)
    
    def handle_pinned_ocr_requested(pixmap, source_win):
        """Pinned image OCR: copy recognized text to clipboard, show toast on the window."""
        ocr_controller.copy_text_from_image(pixmap, source_win)

    pinned_image_manager.ocr_requested.connect(handle_pinned_ocr_requested)

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
        hotkey_manager.resolve_startup_conflicts(lambda: settings_controller.show(section="capture"))

    # Unregister hotkey and release system resources before app exit.
    app.aboutToQuit.connect(hotkey_manager.release_resources)

    # ── Memory Management (Idle Trim Patch) ──────────────────────────
    from .system.memory_utils import trim_working_set

    class IdleMemoryManager(QtCore.QObject):
        """Monitors global app state and trims memory when truly idle."""
        def __init__(self, tm, pm, oc):
            super().__init__()
            self.tm = tm # thumbnail_manager
            self.pm = pm # pinned_image_manager
            self.oc = oc # ocr_controller
            
            self.idle_timer = QtCore.QTimer()
            self.idle_timer.setSingleShot(True)
            self.idle_timer.timeout.connect(self._do_trim)

            self._already_trimmed = False  # don't re-trim until next activity

            # Check idle state every 5 seconds
            self.check_timer = QtCore.QTimer()
            self.check_timer.timeout.connect(self._check_and_start)
            self.check_timer.start(5000)

        def _is_truly_idle(self):
            """App is truly idle only when every visible UI element is gone
            and no background work is in progress."""
            return (
                not self.tm._windows
                and not self.pm._windows
                and not self.oc.is_busy()
                and not self.oc.popup.isVisible()
            )

        def _check_and_start(self):
            if self._is_truly_idle():
                if not self.idle_timer.isActive() and not self._already_trimmed:
                    # 20 seconds of total silence before we trim
                    self.idle_timer.start(20000)
            else:
                self.idle_timer.stop()
                self._already_trimmed = False  # activity detected, allow trim again later

        def _do_trim(self):
            # Final sanity check before the heavy lift
            if self._is_truly_idle():
                logging.info("[IdleMemoryManager] App is truly idle. Trimming working set...")
                trim_working_set()
                self._already_trimmed = True  # don't trim again until next activity

    idle_manager = IdleMemoryManager(thumbnail_manager, pinned_image_manager, ocr_controller)

    # ── Startup Clean ────────────────────────────────────────────────
    # Run a quick startup trim to clean up import-time overhead
    QtCore.QTimer.singleShot(5000, trim_working_set)

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
