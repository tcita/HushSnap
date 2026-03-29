import os
import sys
import logging
import threading

from PyQt6 import QtCore, QtWidgets

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
from .system.windows_ocr import recognize_text_from_pixmap
from .ui.ocr_popup import OcrPopup
from .ui.settings_dialog import SettingsDialogController
from .ui.tray import create_tray
from .config import get_user_data_dir
from .constants import CAPTURE_DEBUG_LOG_FILENAME, TRAY_MSG_MEDIUM_MS
from .logging_config import setup_logging


class OcrResultBridge(QtCore.QObject):
    """Thread-safe bridge for OCR worker result back to Qt main thread."""

    finished = QtCore.pyqtSignal(str, str)


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


def main():
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
    # 1. Parse CLI arguments
    force_debug = "--debug" in sys.argv
    user_data_dir = get_user_data_dir()
    
    # 2. Initialize logging
    setup_logging(
        user_data_dir / CAPTURE_DEBUG_LOG_FILENAME, 
        force_level=logging.DEBUG if force_debug else None
    )
    logger = logging.getLogger(__name__)

    # 3. Install global exception hook as early as possible after logging is ready.
    sys.excepthook = exception_hook

    if force_debug:
        logger.info(f"DEBUG MODE ENABLED. Opening log directory: {user_data_dir}")
        try:
            os.startfile(user_data_dir)
        except Exception as e:
            logger.error(f"Failed to open log directory: {e}")

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

    ocr_bridge = OcrResultBridge()
    ocr_popup = OcrPopup(translate)
    ocr_action = None

    def on_capture_completed(captured_pixmap):
        """
        Optional OCR flow after screenshot is already copied to clipboard.
        """
        if ocr_action is None or not ocr_action.isChecked():
            return

        pixmap_for_ocr = captured_pixmap.copy()

        def worker():
            try:
                text = recognize_text_from_pixmap(pixmap_for_ocr)
                ocr_bridge.finished.emit(text, "")
            except Exception as exc:
                logger.exception(f"OCR failed: {exc}")
                ocr_bridge.finished.emit("", str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def on_ocr_finished(text, error):
        if ocr_action is None or not ocr_action.isChecked():
            return

        if error:
            tray_icon.showMessage(
                translate("ocr_failed_title"),
                translate("ocr_failed_body"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                TRAY_MSG_MEDIUM_MS,
            )
            return

        recognized = (text or "").strip()
        if not recognized:
            tray_icon.showMessage(
                translate("ocr_empty_title"),
                translate("ocr_empty_body"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                TRAY_MSG_MEDIUM_MS,
            )
            return

        ocr_popup.show_text(recognized)

    def launch_capture_window(screen_pixmap):
        """
        Callback that launches the capture window.
        :param screen_pixmap: Pre-captured fullscreen bitmap from HotkeyFilter.
        """
        if communicator.win:
            return

        # Create capture window instance.
        communicator.win = CaptureWindow(screen_pixmap, on_captured=on_capture_completed)

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
            logging.getLogger(__name__).exception(f"Failed to open config dir: {exc}")
            QtWidgets.QMessageBox.warning(
                None,
                translate("open_dir_failed"),
                translate("open_dir_failed_body"),
            )

    def on_uninstall():
        """Uninstall callback."""
        launch_uninstaller(translate, app.quit)

    # Create system tray icon and right-click menu entry points.
    tray_icon, settings_action, ocr_action = create_tray(
        app,
        translate,
        communicator.trigger.emit,  # Allow screenshot trigger from tray menu.
        None,
        open_config_dir,
        app.quit,
    )
    ocr_bridge.finished.connect(on_ocr_finished)

    def on_ocr_toggled(enabled):
        tray_icon.showMessage(
            translate("ocr_toggle_title"),
            translate("ocr_enabled_body") if enabled else translate("ocr_disabled_body"),
            QtWidgets.QSystemTrayIcon.MessageIcon.Information,
            TRAY_MSG_MEDIUM_MS,
        )

    ocr_action.toggled.connect(on_ocr_toggled)

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

    # Enter event loop (build_installer.ps1 checks LASTEXITCODE).
    sys.exit(app.exec())
