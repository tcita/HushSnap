import os
import sys
import logging
import argparse
import time

from PyQt6 import QtWidgets

from .capture_window import CaptureWindow
from .config import (
    is_already_running,
    load_hotkey_setting,
    resolve_ui_lang,
    ui_text,
    get_ocr_lang_from_config,
    update_ocr_lang_in_config,
    get_ocr_enabled_from_config,
    update_ocr_enabled_in_config,
)
from .hotkey import HotkeyFilter
from .signal_bridge import SignalBridge
from .system.hotkey_manager import HotkeyManager
from .system.uninstall import launch_uninstaller
from .ocr_service import OcrService, OcrRequest
from .ui.ocr_popup import OcrPopup
from .ui.settings_dialog import SettingsDialogController
from .ui.tray import create_tray
from .config import get_user_data_dir
from .constants import CAPTURE_DEBUG_LOG_FILENAME, TRAY_MSG_MEDIUM_MS
from .logging_config import setup_logging


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
    boot_duration = (overall_start - boot_start_time) if boot_start_time else 0.0
    
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
    logger.info(f"--- STARTUP PERFORMANCE AUDIT ---")
    logger.info(f"OS/Import overhead: {boot_duration:.4f}s")
    logger.debug(f"STEP 1&2: Args parsed and logging setup. Elapsed inside main: {time.perf_counter() - overall_start:.4f}s")

    # 3. Install global exception hook as early as possible after logging is ready.
    sys.excepthook = exception_hook

    step_start = time.perf_counter()
    hotkey_modifier, hotkey_virtual_key, hotkey_name, config_path = load_hotkey_setting()
    logger.debug(f"STEP 4: Hotkey setting loaded. Duration: {time.perf_counter() - step_start:.4f}s")
    
    if force_debug:
        logger.info("DEBUG MODE ENABLED.")
        print("\n" + "="*80)
        print(f"Config directory: {config_path.parent}")
        print("="*80 + "\n")


    # Enforce single instance via lock/mutex.
    step_start = time.perf_counter()
    instance_lock = is_already_running()
    logger.debug(f"STEP 5: Single instance check. Duration: {time.perf_counter() - step_start:.4f}s")
    
    if not instance_lock:
        message = "HushSnap is already running. Exiting this launch."
        logger.warning(message)
        print(message)
        return

    # Create the Qt application instance with argv0 and any remaining CLI arguments.
    # (currently usually none unless Qt args are provided).
    step_start = time.perf_counter()
    app = QtWidgets.QApplication([sys.argv[0], *qt_args])
    logger.debug(f"STEP 6: QApplication created. Duration: {time.perf_counter() - step_start:.4f}s")

    # Keep the process alive after all windows are closed.
    app.setQuitOnLastWindowClosed(False)

    # Load config: current hotkey binding and language preference.
    step_start = time.perf_counter()
    ui_language = resolve_ui_lang(config_path)
    logger.debug(f"STEP 7: UI language resolved. Duration: {time.perf_counter() - step_start:.4f}s")

    def translate(key, **kwargs):
        return ui_text(ui_language, key, **kwargs)

    # Hotkey event -> Qt signal bridge -> UI callback
    capture_bridge = SignalBridge()
    capture_bridge.win = None

    ocr_bridge = SignalBridge()
    step_start = time.perf_counter()
    ocr_popup = OcrPopup(translate)
    ocr_service = OcrService()
    logger.debug(f"STEP 8: OCR Popup & Service initialized. Duration: {time.perf_counter() - step_start:.4f}s")
    
    # Set initial language from config
    initial_lang = get_ocr_lang_from_config(config_path)
    lang_idx = ocr_popup.lang_combo.findData(initial_lang)
    if lang_idx >= 0:
        ocr_popup.lang_combo.setCurrentIndex(lang_idx)

    ocr_action = None

    def on_capture_completed(captured_pixmap):
        """
        Optional OCR flow after screenshot is already copied to clipboard.
        """
        if ocr_action is None or not ocr_action.isChecked():
            return

        pixmap_for_ocr = captured_pixmap.copy()
        current_lang = ocr_popup.lang_combo.itemData(ocr_popup.lang_combo.currentIndex())
        debug_dir = user_data_dir if save_ocr_debug_image else None

        request = OcrRequest(
            pixmap=pixmap_for_ocr,
            language_tag=current_lang,
            debug_dir=debug_dir,
        )
        ocr_service.recognize_async(
            request,
            lambda response: ocr_bridge.signal.emit((response.text, response.error, response.pixmap)),
        )

    def on_ocr_finished(payload):
        text, error, pixmap = payload
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
        current_lang = ocr_popup.lang_combo.itemData(ocr_popup.lang_combo.currentIndex())

        if not recognized:
            tray_icon.showMessage(
                translate("ocr_empty_title"),
                translate("ocr_empty_body"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                TRAY_MSG_MEDIUM_MS,
            )
            # Keep popup accessible even for empty OCR so users can switch
            # language and re-run OCR on the same captured image.
            ocr_popup.show_text(
                translate("ocr_empty_popup_hint"),
                pixmap=pixmap,
                lang=current_lang,
            )
            return

        ocr_popup.show_text(
            recognized, 
            pixmap=pixmap, 
            lang=current_lang,
        )

    def on_ocr_lang_changed(lang):
        """Triggered when user changes language in the OCR popup."""
        # Persist the choice
        update_ocr_lang_in_config(config_path, lang)
        
        pixmap = ocr_popup.last_pixmap
        if not pixmap or pixmap.isNull():
            return
        debug_dir = user_data_dir if save_ocr_debug_image else None
            
        request = OcrRequest(
            pixmap=pixmap,
            language_tag=lang,
            debug_dir=debug_dir,
        )
        ocr_service.recognize_async(
            request,
            lambda response: ocr_bridge.signal.emit((response.text, response.error, response.pixmap)),
        )

    ocr_popup.language_changed.connect(on_ocr_lang_changed)

    def launch_capture_window(screen_pixmap):
        """
        Callback that launches the capture window.
        :param screen_pixmap: Pre-captured fullscreen bitmap from HotkeyFilter.
        """
        if capture_bridge.win:
            return

        # Create capture window instance.
        capture_bridge.win = CaptureWindow(screen_pixmap, on_captured=on_capture_completed)

        # Reset capture_bridge.win to None when CaptureWindow is destroyed.
        capture_bridge.win.destroyed.connect(lambda: setattr(capture_bridge, "win", None))
        capture_bridge.win.show()


    # Connect launch_capture_window to capture bridge signal.
    capture_bridge.signal.connect(launch_capture_window)

    # Install HotkeyFilter to intercept WM_HOTKEY before Qt window event delivery.
    native_hotkey_filter = HotkeyFilter(capture_bridge.signal)
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
    step_start = time.perf_counter()
    tray_icon, settings_action, ocr_action = create_tray(
        app,
        translate,
        capture_bridge.signal.emit,  # Allow screenshot trigger from tray menu.
        None,
        open_config_dir,
        app.quit,
    )
    logger.debug(f"STEP 10: Tray icon created. Duration: {time.perf_counter() - step_start:.4f}s")

    # Restore OCR toggle state from persisted config.
    ocr_action.setChecked(get_ocr_enabled_from_config(config_path))

    ocr_bridge.signal.connect(on_ocr_finished)

    def on_ocr_toggled(enabled):
        update_ocr_enabled_in_config(config_path, enabled)
        tray_icon.showMessage(
            translate("ocr_toggle_title"),
            translate("ocr_enabled_body") if enabled else translate("ocr_disabled_body"),
            QtWidgets.QSystemTrayIcon.MessageIcon.Information,
            TRAY_MSG_MEDIUM_MS,
        )

    ocr_action.toggled.connect(on_ocr_toggled)

    # Hotkey manager handles registration/unregistration with Windows.
    step_start = time.perf_counter()
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
    logger.debug(f"STEP 11: HotkeyManager init & registered. Duration: {time.perf_counter() - step_start:.4f}s")

    # Initialize settings dialog controller.
    step_start = time.perf_counter()
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
    logger.debug(f"STEP 12: SettingsDialogController initialized. Duration: {time.perf_counter() - step_start:.4f}s")

    # Unregister hotkey before app exit.
    app.aboutToQuit.connect(hotkey_manager.unregister_current_hotkey)

    logic_init_duration = time.perf_counter() - overall_start
    total_wall_time = time.perf_counter() - boot_start_time if boot_start_time else logic_init_duration
    logger.info(f"Application logic init: {logic_init_duration:.4f}s")
    logger.info(f"Initialization complete. Total wall-clock startup time: {total_wall_time:.4f}s")

    # Enter event loop (build_installer.ps1 checks LASTEXITCODE).
    sys.exit(app.exec())
