import ctypes
import gc
import os
import shutil
import sys
import logging
import time
import subprocess
import tempfile
from pathlib import Path

from PyQt6 import QtWidgets, QtCore

from .capture_session import CaptureSession
from .config import (
    get_app_id,
    get_auto_ocr_after_capture,
    get_config_path,
    get_debug_enabled,
    get_onboarding_toast_shown,
    get_show_capture_dimension_label,
    get_user_data_dir,
    is_already_running,
    load_hotkey_setting,
    release_instance_lock,
    resolve_physical_path,
    resolve_ui_lang,
    set_onboarding_toast_shown,
    ui_text,
)
from .ocr_controller import OcrController
from .system.hotkey_manager import HotkeyManager
from .ui.settings_dialog import SettingsDialogController
from .ui.thumbnail import thumbnail_manager, show_thumbnail, qpixmap_to_pil
from .ui.pinned_image import pinned_image_manager
from .ui.tray import create_tray
from .ui.toast import show_toast
from .hotkey import HotkeyFilter
from .constants import CAPTURE_DEBUG_LOG_FILENAME
from .logging_config import setup_logging
from .startup_profiler import StartupProfiler
from . import wer

# Module-level state set during Application.run() so exception_hook can access
# them.  exception_hook is installed via sys.excepthook before __init__ completes,
# so it cannot rely on instance attributes.  These are the only module-level
# globals in the hot path — please don't add more.
_log_file_path = None
_translate = None


def _flush_log_handlers():
    """Flush every RotatingFileHandler owned by the root logger so that a
    subsequent copy of the log file sees all buffered writes on disk."""
    root = logging.getLogger()
    for h in root.handlers:
        try:
            h.flush()
        except Exception:
            pass


def exception_hook(exctype, value, tb):
    """
    Global unhandled exception handler.
    Logs the error with stack trace and shows a message box to the user
    with options to save or open the log file.
    """
    logger = logging.getLogger("HushSnap")
    # 1. Log the full traceback to the log file.
    logger.critical("Unhandled exception occurred:", exc_info=(exctype, value, tb))

    # 1b. Dump loaded native module list for post-mortem diagnosis
    #     (ABI mismatches, stale DLLs, unexpected driver loads).
    try:
        wer.log_loaded_modules(logger)
    except Exception:
        pass

    # 1c. Flush everything to disk so the copy / open below sees the
    #     complete log including the traceback and module list just written.
    _flush_log_handlers()

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
        open_btn = msg_box.addButton(
            t("crash_open_log"), QtWidgets.QMessageBox.ButtonRole.ActionRole
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
        elif clicked == open_btn and _log_file_path:
            try:
                _open_log_file(_log_file_path)
            except Exception:
                logger.exception("Failed to open log file")

    # 3. Clean exit.  Partner Center's crash count has no diagnostic
    #    value (retail Windows produces "Uncategorized" with no stacks
    #    or .cab files), and RaiseFailFastException adds WER overhead
    #    that keeps the cursor spinning after the dialog closes.
    #    os._exit() stops the process immediately — the log is already
    #    flushed above, so no data loss.
    os._exit(1)


def _save_log_to_desktop(log_path):
    """Copy the full log file to the user's desktop.
    Uses ``shutil.copy`` (not ``copy2``) — metadata preservation is
    pointless in a crash handler and ``os.utime`` on the destination
    can be unexpectedly slow when Desktop is OneDrive-backed."""
    desktop = Path.home() / "Desktop"
    dest = desktop / "HushSnap_crash.log"
    shutil.copy(log_path, dest)


def _open_log_file(log_path):
    """Open the log file with the OS default text editor so the user can
    inspect it and copy the relevant portion for feedback.  More reliable
    than clipboard manipulation during a crash state."""
    if sys.platform == "win32":
        os.startfile(log_path)
    else:
        import subprocess as _sp
        _sp.Popen(["xdg-open", str(log_path)])


class Application(QtCore.QObject):
    """
    Central controller for the HushSnap application.
    Orchestrates startup, signal connections, and component lifecycles.
    """
    def __init__(self, boot_start_time=None):
        super().__init__()
        self.boot_start_time = boot_start_time
        self.overall_start = time.perf_counter()
        
        # 1. Environment & Paths
        self.user_data_dir = get_user_data_dir()
        self.config_path = get_config_path()
        self.force_debug = get_debug_enabled(self.config_path)
        
        # 2. Logging Setup
        global _log_file_path
        _log_file_path = self.user_data_dir / CAPTURE_DEBUG_LOG_FILENAME
        setup_logging(
            _log_file_path,
            force_level=logging.DEBUG if self.force_debug else None
        )
        self.logger = logging.getLogger(__name__)

        # 3. Profiling & Diagnostics
        self.startup_profiler = StartupProfiler(
            self.logger,
            self.overall_start,
            self.boot_start_time,
            detailed_enabled=self.force_debug,
        )
        sys.excepthook = exception_hook
        # Worker-thread exceptions: log only, do NOT crash the app (an OCR
        # thread dying must not take down screenshots). See wer.py.
        wer.install_threading_excepthook()

        # 4. State
        self.instance_lock = None
        self.qt_app = None
        self.ui_language = "en"
        self.tray_icon = None
        self.native_hotkey_filter = None
        
        # Components
        self.ocr_controller = None
        self.capture_session = None
        self.hotkey_manager = None
        self.settings_controller = None
        self._editor_window = None  # most recent visible image-editor reference
        self._editor_windows = []  # all active image-editor references

    def run(self):
        """Execute the full application lifecycle."""
        self.startup_profiler.log_header()
        self.startup_profiler.log_elapsed("Config loaded and logging setup")

        # --- Prerequisite checks ---
        self._purge_drag_cache()
        
        with self.startup_profiler.step("Process bootstrap ready"):
            self.instance_lock = is_already_running()
        
        if not self.instance_lock:
            self.logger.warning("HushSnap is already running. Exiting launch.")
            try:
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "HushSnap is already running.\n\n"
                    "Check the system tray or close the other instance before launching again.",
                    "HushSnap",
                    0x00002000,  # MB_OK | MB_TOPMOST (no icon → no sound)
                )
            except Exception:
                pass
            return

        # --- Component Initialization ---
        self._init_qt_app()
        self._init_logic_controllers()
        self._init_ui_shell()

        # --- Final Polish ---
        self.startup_profiler.log_summary()

        # Show a one-time "ready" toast on the first launch after install so
        # the user learns the capture hotkey. Never shown again afterwards.
        # Wait for OCR engine load to finish (same signal that reveals the tray
        # icon) so the toast appears after the app is fully ready, then add a
        # short settle delay for the tray icon to render.
        if not get_onboarding_toast_shown():
            self.ocr_controller.bridge.load_finished.connect(
                lambda: QtCore.QTimer.singleShot(300, self._show_startup_ready_toast)
            )

        return self.qt_app.exec()

    def translate(self, key, **kwargs):
        """App-wide translation helper."""
        return ui_text(self.ui_language, key, **kwargs)

    def _show_startup_ready_toast(self):
        """One-time startup "ready" toast, shown on first launch after install."""
        # Record immediately so a crash/quit right after showing won't replay it.
        set_onboarding_toast_shown()
        try:
            hotkey_name = self.hotkey_manager.current_hotkey_name
        except Exception:
            hotkey_name = ""
        show_toast(
            self.translate("startup_ready_toast", hotkey=hotkey_name),
            duration_ms=3000,
        )

    def _purge_drag_cache(self):
        try:
            cache_dir = self.user_data_dir / "drag_cache"
            if cache_dir.exists() and cache_dir.is_dir():
                shutil.rmtree(cache_dir, ignore_errors=True)
                self.logger.debug(f"Previous drag cache purged at startup: {cache_dir}")
        except Exception:
            self.logger.debug("Failed to purge drag cache at startup", exc_info=True)

    def _init_qt_app(self):
        with self.startup_profiler.step("UI services initialized"):
            self.qt_app = QtWidgets.QApplication(sys.argv)
            app_id = get_app_id()
            self.qt_app.setApplicationName(app_id)
            self.qt_app.setOrganizationName("HushSnap")
            self.qt_app.setQuitOnLastWindowClosed(False)

            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
                except Exception:
                    self.logger.debug("Failed to set AppUserModelID", exc_info=True)

            self.ui_language = resolve_ui_lang(self.config_path)
            global _translate
            _translate = self.translate

            # ── Suppress harmless Qt 6.10.x internal QFont::setPointSize(-1) warnings ──
            # Qt 6 changed QFont internals: the sentinel value -1 ("use default") is now
            # passed through setPointSize() during CSS font-family resolution and widget
            # font cascade, which logs a qWarning.  This is a Qt framework implementation
            # detail — the font resolves correctly regardless, but the log noise is
            # distracting.  Filter it here rather than chasing every internal call site.
            _prev = []  # mutable capture to avoid closure-before-assignment
            _prev.append(QtCore.qInstallMessageHandler(
                lambda t, c, m: (_prev[0](t, c, m) if _prev[0] else None)
                if "setPointSize" not in m
                else None
            ))

    def _init_logic_controllers(self):
        # 1. OCR Controller
        self.ocr_controller = OcrController(
            app=self.qt_app,
            translate=self.translate,
            config_path=self.config_path,
            user_data_dir=self.user_data_dir,
            save_debug_image=self.force_debug,
        )

        # 2. Capture Session
        self.capture_session = CaptureSession(
            self._on_capture_completed,
            show_dimension_label=get_show_capture_dimension_label(),
        )
        self.ocr_controller.set_capture_requester(self.capture_session.request_capture)

        # 3. Hotkey Manager
        with self.startup_profiler.step("Startup config loaded"):
            modifier, vk, name, _ = load_hotkey_setting()
            
        self.hotkey_manager = HotkeyManager(
            self.translate,
            self.config_path,
            modifier,
            vk,
            name,
        )
        self.hotkey_manager.status_requested.connect(self._handle_status_toast)
        self.hotkey_manager.register_initial()
        self.hotkey_manager.start_watch(self.qt_app)
        self.qt_app.aboutToQuit.connect(self.hotkey_manager.release_resources)

        # 4. Native Event Filter (Hotkey hook)
        from .hotkey import HotkeyFilter
        self.native_hotkey_filter = HotkeyFilter(
            on_trigger=self.capture_session.request_capture,
            on_taskbar_created=self._handle_taskbar_created,
        )
        self.native_hotkey_filter.hotkey_id = self.hotkey_manager.hotkey_id
        self.qt_app.installNativeEventFilter(self.native_hotkey_filter)

        # 5. UI Wiring (Thumbnail & Pinned Image)
        thumbnail_manager.clicked.connect(self._handle_thumbnail_clicked)
        thumbnail_manager.save_to_desktop.connect(self._handle_save_to_desktop)
        thumbnail_manager.edit_requested.connect(self._handle_open_editor)
        thumbnail_manager.ocr_copy_requested.connect(self._handle_thumbnail_ocr_copy)
        thumbnail_manager.open_in_viewer.connect(self._handle_open_in_viewer)
        thumbnail_manager.pin_requested.connect(
            lambda pil, pos, size: pinned_image_manager.pin_image(
                pil, 
                morph_pos=pos, 
                morph_size=size,
                logical_size=pil.info.get("logical_size")
            )
        )
        
        pinned_image_manager.ocr_requested.connect(
            lambda pix, win: self.ocr_controller.copy_text_from_image(pix, win)
        )
        pinned_image_manager.edit_requested.connect(self._handle_open_editor)

    def _init_ui_shell(self):
        with self.startup_profiler.step("Shell integration initialized"):
            from .ui.tray import create_tray
            self.tray_icon, settings_action = create_tray(
                self.qt_app,
                self.translate,
                self.capture_session.request_capture,
                None,
                self._open_config_dir,
                self.qt_app.quit,
                initial_hotkey=self.hotkey_manager.current_hotkey_name,
            )

            self.ocr_controller.tray_icon = self.tray_icon
            self.ocr_controller.bridge.load_finished.connect(self.tray_icon.show)
            self.hotkey_manager.tray_icon = self.tray_icon

            # Settings Dialog
            try:
                self.settings_controller = SettingsDialogController(
                    self.translate,
                    self.config_path,
                    self.hotkey_manager,
                    on_font_size_changed=self.ocr_controller.apply_font_sizes,
                    on_dim_label_changed=lambda enabled: setattr(
                        self.capture_session, "show_dimension_label", enabled
                    ),
                )
                settings_action.triggered.connect(self.settings_controller.show)
                self.settings_controller.language_changed.connect(self._handle_restart_requested)
                
                # Deferred conflict resolution
                self.hotkey_manager.resolve_startup_conflicts(
                    lambda: self.settings_controller.show(section="capture")
                )
            except Exception:
                self.logger.exception("Failed to initialize settings dialog")
                settings_action.setEnabled(False)

    def _on_capture_completed(self, captured_pixmap, logical_size):
        """Callback after screenshot is copied to clipboard."""
        self.logger.debug("[OCR_CHAIN] capture completed callback")
        if not self.ocr_controller.needs_ocr:
            try:
                pil_img = qpixmap_to_pil(captured_pixmap)
                # Store selection-based logical size as source of truth
                pil_img.info["logical_size"] = logical_size
                # No deferred show: a previous revision deferred show_thumbnail
                # by 50ms "so CaptureWindow is fully destroyed before the
                # thumbnail is shown" (commit 03cb8ea, avoiding an MSIX DWM
                # focus race that flashed-then-dismissed the thumbnail). That
                # race is prevented by ThumbnailWindow's
                # WA_ShowWithoutActivating + WA_NativeWindow attributes (added
                # in the same commit), not by the delay: a 300-cycle MSIX
                # stress test (high-pressure + clean) showed the CaptureWindow
                # destroyed() signal never fires within the 50ms window, and
                # zero flash-dismiss events. The 50ms only added ~57ms of
                # thumbnail-appear latency, so it is removed.
                show_thumbnail(pil_img)
                self.logger.debug("[OCR_CHAIN] thumbnail shown")
            except Exception:
                self.logger.exception("Failed to show thumbnail")

            if get_auto_ocr_after_capture():
                self.logger.debug("[OCR_CHAIN] auto-OCR triggered")
                self.ocr_controller.auto_ocr_to_clipboard(captured_pixmap)

        self.ocr_controller.handle_capture_completed(captured_pixmap)

    def _handle_thumbnail_clicked(self, pil_img):
        self.logger.debug("[OCR_CHAIN] thumbnail click handled")
        from .ui.thumbnail import thumbnail_manager
        thumb_win = thumbnail_manager.current_window()
        if thumb_win:
            thumb_win.start_loading()

        pos, size = thumbnail_manager.current_window_rect()
        if pos and size:
            self.ocr_controller.set_popup_anchor(
                pos.x() + size.width() / 2,
                pos.y() + size.height() / 2,
                width=size.width(),
                height=size.height()
            )
        else:
            center = thumbnail_manager.current_window_center()
            if center is not None:
                self.ocr_controller.set_popup_anchor(*center)

        # If auto-OCR is already running, redirect its result to the popup
        # instead of starting a second inference.  Otherwise the user waits
        # for both OCR runs to finish sequentially, perceiving a multi-second
        # gap.  The auto-OCR is always for the current thumbnail's screenshot
        # (only one thumbnail exists at a time), so no image-identity check
        # is needed — the flag alone is sufficient.
        if self.ocr_controller._auto_ocr_in_flight:
            self.logger.debug("[OCR_CHAIN] redirecting in-flight auto-OCR to popup")
            self.ocr_controller.redirect_auto_ocr_to_popup()
            return

        from PyQt6 import QtGui
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimage = QtGui.QImage(
            data, pil_img.size[0], pil_img.size[1],
            QtGui.QImage.Format.Format_RGBA8888,
        ).copy()
        self.ocr_controller.start_request(QtGui.QPixmap.fromImage(qimage))

    def _handle_thumbnail_ocr_copy(self, pil_img):
        """Silent OCR: recognize text and copy to the clipboard without the popup.

        Routes through ocr_controller.copy_text_from_image - the same no-popup
        path the pinned-image menu uses - so the result goes straight to the
        clipboard with a toast, instead of opening the editable OCR popup that a
        left-click produces.  The thumbnail switches to its loading state and is
        dismissed once OCR completes.
        """
        self.logger.debug("[OCR_CHAIN] thumbnail silent-OCR copy handled")
        from .ui.thumbnail import thumbnail_manager
        thumb_win = thumbnail_manager.current_window()
        if thumb_win is None:
            return
        thumb_win.start_loading()

        from PyQt6 import QtGui
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimage = QtGui.QImage(
            data, pil_img.size[0], pil_img.size[1],
            QtGui.QImage.Format.Format_RGBA8888,
        ).copy()
        pixmap = QtGui.QPixmap.fromImage(qimage)

        def _dismiss():
            try:
                thumb_win.dismiss()
            except RuntimeError:
                pass

        self.ocr_controller.copy_text_from_image(pixmap, thumb_win, on_done=_dismiss)

    def _handle_open_editor(self, pil_img):
        """Open the lightweight image editor for the given PIL image."""
        try:
            from .ui.image_editor import show_image_editor
            win = show_image_editor(pil_img, self.translate, self.ui_language)
            self._editor_windows.append(win)
            self._editor_window = win
            win.destroyed.connect(lambda _obj=None, w=win: self._untrack_editor_window(w))
        except Exception:
            self.logger.exception("Failed to open image editor")

    def _untrack_editor_window(self, win):
        self._editor_windows = [w for w in self._editor_windows if w is not win]
        if self._editor_window is win:
            self._editor_window = self._last_visible_editor_window()

    def _last_visible_editor_window(self):
        for win in reversed(self._editor_windows):
            try:
                if win.isVisible():
                    return win
            except RuntimeError:
                continue
        return None

    def _has_visible_editor_window(self):
        any_visible = False
        live_windows = []
        for win in self._editor_windows:
            try:
                if win.isVisible():
                    any_visible = True
                live_windows.append(win)
            except RuntimeError:
                continue
        self._editor_windows = live_windows
        self._editor_window = self._last_visible_editor_window()
        return any_visible

    def _handle_save_to_desktop(self, pil_img):
        try:
            desktop = Path.home() / "Desktop"
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            ms = int(time.time() * 1000) % 1000
            base = f"HushSnap_{timestamp}_{ms:03d}"
            file_path = desktop / f"{base}.png"
            counter = 1
            while file_path.exists():
                file_path = desktop / f"{base}({counter}).png"
                counter += 1
            pil_img.save(file_path)
            show_toast(self.translate("pin_saved_to_desktop"))
        except Exception:
            self.logger.exception("Failed to save image to desktop")

    def _handle_open_in_viewer(self, pil_img):
        """Open the capture in the system's default image viewer via a temp file.

        A temp PNG is written (not to the Desktop - that is Save to Desktop's
        job) and handed to ``os.startfile``. Stale temp files from prior opens
        are swept first; a file still locked by an open viewer is skipped.
        """
        try:
            tmp_dir = Path(tempfile.gettempdir())
            for old in tmp_dir.glob("HushSnap_view_*.png"):
                try:
                    old.unlink()
                except OSError:
                    pass
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            ms = int(time.time() * 1000) % 1000
            file_path = tmp_dir / f"HushSnap_view_{timestamp}_{ms:03d}.png"
            pil_img.save(file_path)
            os.startfile(str(file_path))
        except Exception:
            self.logger.exception("Failed to open image in default viewer")

    def _handle_status_toast(self, title_key, body_key, is_error, kwargs):
        from .ui.toast import show_toast
        self.logger.debug(f"[handle_status_toast] title={title_key}, body={body_key}, is_error={is_error}")
        msg = self.translate(body_key, **kwargs)
        show_toast(msg, duration_ms=3000 if is_error else 2000, is_error=is_error)

    def _handle_taskbar_created(self):
        self.logger.debug("Windows Explorer taskbar recreated. Restoring system tray icon.")
        if self.tray_icon:
            try:
                self.tray_icon.hide()
                self.tray_icon.show()
            except Exception:
                self.logger.exception("Failed to restore system tray icon")

    def _handle_restart_requested(self):
        self.logger.info("Restart requested. Cleaning up...")
        if self.hotkey_manager:
            # Stop the config watcher first: release_resources() unregisters
            # the hotkey, and the config write that triggered this restart
            # would otherwise fire a delayed reload that re-registers it and
            # pops a spurious "Enabled <hotkey>" toast over the restart toast.
            self.hotkey_manager.stop_watch()
            self.hotkey_manager.release_resources()
        # Show a toast first so the restart reads as an intentional action
        # rather than a sudden crash. The delay also gives the toast time to
        # render before the process exits — restart must wait at least as long
        # as the toast duration, otherwise the process exits mid-display.
        from .ui.toast import show_toast
        show_toast(self.translate("language_changed_body"), duration_ms=3000)
        QtCore.QTimer.singleShot(3000, lambda: _restart_app(self.qt_app, self.instance_lock))

    def _open_config_dir(self):
        try:
            resolved = resolve_physical_path(self.config_path.parent)
            os.startfile(resolved)
        except Exception:
            self.logger.exception("Failed to open config dir")
            QtWidgets.QMessageBox.warning(
                None, self.translate("open_dir_failed"), self.translate("open_dir_failed_body")
            )



def main(boot_start_time=None):
    """Application entry point."""
    application = Application(boot_start_time=boot_start_time)
    sys.exit(application.run())


def _restart_app(app, instance_lock):
    """Restart the current application process."""
    logger = logging.getLogger(__name__)
    logger.info("Application is restarting.")

    executable = sys.executable
    args = sys.argv[:]
    
    from .config import release_instance_lock
    release_instance_lock(instance_lock)
    
    app.quit()
    subprocess.Popen([executable] + args)
    sys.exit(0)
