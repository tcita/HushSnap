"""
HushSnap hotkey manager module.
Handles global hotkey register/unregister, conflict handling, and dynamic config reload.
"""

import ctypes
import logging
from PyQt6 import QtCore, QtWidgets

from ..config import parse_hotkey, read_hotkey_text_from_config
from ..constants import (
    RELOAD_TIMER_MS,
    TRAY_MSG_LONG_MS,
    TRAY_MSG_MEDIUM_MS,
    TRAY_MSG_SHORT_MS,
    TRAY_NOTIFICATIONS_ENABLED,
)

logger = logging.getLogger(__name__)


class HotkeyManager:
    """
    Manager for Windows global hotkeys.
    Supports registration, unregistration, dynamic updates, and auto-reload on config changes.
    """
    def __init__(self, tray_icon, translate, config_path, modifier, virtual_key, name,
                 ocr_modifier=None, ocr_virtual_key=None, ocr_name=None):
        """
        Initialize hotkey manager.

        Args:
            tray_icon (QSystemTrayIcon): Tray icon instance for notifications.
            translate (callable): Translation function for i18n text.
            config_path (Path): Config file path.
            modifier (int): Initial modifier mask.
            virtual_key (int): Initial virtual key code.
            name (str): Initial human-readable hotkey name.
            ocr_modifier (int): OCR screenshot hotkey modifier mask.
            ocr_virtual_key (int): OCR screenshot hotkey virtual key code.
            ocr_name (str): OCR screenshot hotkey human-readable name.
        """
        self.tray_icon = tray_icon
        self.translate = translate
        self.config_path = config_path
        self.hotkey_registered = False
        self.ocr_hotkey_registered = False

        # Use GlobalAddAtom to generate a system-unique hotkey ID.
        # "HushSnap_Hotkey_Atom" generates an atom in the 0xC000-0xFFFF range,
        # which helps avoid ID collisions with other programs.
        self.hotkey_id = ctypes.windll.kernel32.GlobalAddAtomW("HushSnap_Hotkey_Atom")
        if not self.hotkey_id:
            # Fallback to a fixed ID if atom creation fails.
            self.hotkey_id = 0xBFFF

        # Second hotkey ID for OCR screenshot.
        self.ocr_hotkey_id = ctypes.windll.kernel32.GlobalAddAtomW("HushSnap_OCR_Hotkey_Atom")
        if not self.ocr_hotkey_id:
            self.ocr_hotkey_id = 0xBFFE

        self.current_hotkey_modifier = modifier
        self.current_hotkey_virtual_key = virtual_key
        self.current_hotkey_name = name

        self.current_ocr_hotkey_modifier = ocr_modifier
        self.current_ocr_hotkey_virtual_key = ocr_virtual_key
        self.current_ocr_hotkey_name = ocr_name

        self._watcher = None
        self._reload_timer = None
        self._config_file_path_str = str(config_path)
        self._config_dir_path_str = str(config_path.parent)

    def register_initial(self):
        """
        Initial hotkey registration at application startup.
        
        Returns:
            bool: True on success; otherwise show warning and return False.
        """
        if not ctypes.windll.user32.RegisterHotKey(
            None,
            self.hotkey_id,
            self.current_hotkey_modifier,
            self.current_hotkey_virtual_key,
        ):
            QtWidgets.QMessageBox.warning(
                None,
                self.translate("error"),
                self.translate(
                    "hotkey_taken",
                    hotkey=self.current_hotkey_name,
                    config_path=self.config_path,
                ),
            )
            self.hotkey_registered = False
            return False

        self.hotkey_registered = True
        return True

    def register_ocr_initial(self):
        """Register the OCR screenshot hotkey (Alt+Shift+Q by default)."""
        if self.current_ocr_hotkey_virtual_key is None:
            return False
        if not ctypes.windll.user32.RegisterHotKey(
            None,
            self.ocr_hotkey_id,
            self.current_ocr_hotkey_modifier,
            self.current_ocr_hotkey_virtual_key,
        ):
            logger.warning(
                "OCR hotkey %s could not be registered (may be in use by another app).",
                self.current_ocr_hotkey_name,
            )
            self.ocr_hotkey_registered = False
            return False

        self.ocr_hotkey_registered = True
        return True

    def _show_tray_message(self, title, body, icon, timeout):
        """Show a tray notification. Only informational messages respect the global toggle."""
        if self.tray_icon is None:
            return
        if not TRAY_NOTIFICATIONS_ENABLED and icon == QtWidgets.QSystemTrayIcon.MessageIcon.Information:
            return
        self.tray_icon.showMessage(title, body, icon, timeout)

    def unregister_current_hotkey(self):
        """
        Unregister both hotkeys from the Windows system.
        Keeps the Atom IDs alive for potential re-registration.
        """
        if self.hotkey_registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.hotkey_id)
            self.hotkey_registered = False

        if self.ocr_hotkey_registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.ocr_hotkey_id)
            self.ocr_hotkey_registered = False

    def release_resources(self):
        """
        Final cleanup: unregister hotkeys and permanently delete Atom IDs.
        Should only be called when the application is shutting down.
        """
        self.unregister_current_hotkey()

        if hasattr(self, "hotkey_id") and self.hotkey_id:
            ctypes.windll.kernel32.GlobalDeleteAtom(self.hotkey_id)
            self.hotkey_id = 0
        if hasattr(self, "ocr_hotkey_id") and self.ocr_hotkey_id:
            ctypes.windll.kernel32.GlobalDeleteAtom(self.ocr_hotkey_id)
            self.ocr_hotkey_id = 0

    def register_hotkey(self, modifier, virtual_key, name):
        """
        Try registering a new hotkey.
        
        Args:
            modifier (int): Modifier mask.
            virtual_key (int): Virtual key code.
            name (str): Hotkey name.
            
        Returns:
            bool: True if registration succeeds, else False.
        """
        if ctypes.windll.user32.RegisterHotKey(None, self.hotkey_id, modifier, virtual_key):
            self.hotkey_registered = True
            self.current_hotkey_modifier = modifier
            self.current_hotkey_virtual_key = virtual_key
            self.current_hotkey_name = name
            return True
        return False

    def _ensure_watch_targets(self):
        """Ensure QFileSystemWatcher targets stay valid after file delete/recreate cycles."""
        if self._config_dir_path_str not in self._watcher.directories():
            self._watcher.addPath(self._config_dir_path_str)
        if self.config_path.exists() and self._config_file_path_str not in self._watcher.files():
            self._watcher.addPath(self._config_file_path_str)

    def _reload_ocr_hotkey_from_config(self):
        """Read OCR hotkey from config and re-register if changed."""
        from ..config import read_ocr_hotkey_text_from_config
        if self.current_ocr_hotkey_virtual_key is None:
            return
        try:
            new_modifier, new_virtual_key, new_name = parse_hotkey(
                read_ocr_hotkey_text_from_config(self.config_path)
            )
        except Exception:
            return

        if (
            new_modifier == self.current_ocr_hotkey_modifier
            and new_virtual_key == self.current_ocr_hotkey_virtual_key
            and self.ocr_hotkey_registered
        ):
            return

        if self.ocr_hotkey_registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.ocr_hotkey_id)
            self.ocr_hotkey_registered = False

        self.current_ocr_hotkey_modifier = new_modifier
        self.current_ocr_hotkey_virtual_key = new_virtual_key
        self.current_ocr_hotkey_name = new_name

        if ctypes.windll.user32.RegisterHotKey(
            None, self.ocr_hotkey_id, new_modifier, new_virtual_key
        ):
            self.ocr_hotkey_registered = True

    def apply_ocr_hotkey_reload(self):
        """Public method to reload OCR hotkey from config (called by settings dialog)."""
        self._reload_ocr_hotkey_from_config()
        if hasattr(self.tray_icon, "update_shortcuts"):
            self.tray_icon.update_shortcuts(self.current_hotkey_name, self.current_ocr_hotkey_name)

    def apply_hotkey_reload(self):
        """
        Execute hotkey reload flow.
        Read new config, unregister old hotkey, and register new one.
        If new registration fails, attempt rollback to old hotkey.
        """
        try:
            self._apply_hotkey_reload_core()
        finally:
            if hasattr(self.tray_icon, "update_shortcuts"):
                self.tray_icon.update_shortcuts(self.current_hotkey_name, self.current_ocr_hotkey_name)

    def _apply_hotkey_reload_core(self):
        # Always sync OCR hotkey first (may have changed independently).
        self._reload_ocr_hotkey_from_config()

        self._ensure_watch_targets()
        try:
            new_modifier, new_virtual_key, new_name = parse_hotkey(
                read_hotkey_text_from_config(self.config_path)
            )
        except Exception as exc:
            logger.exception(f"Failed to reload hotkey from config {self.config_path}: {exc}")
            self._show_tray_message(
                self.translate("hotkey_not_updated_title"),
                self.translate("hotkey_invalid_config", hotkey=self.current_hotkey_name),
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                TRAY_MSG_MEDIUM_MS,
            )
            return

        # Skip if hotkey settings are unchanged and currently active.
        if (
            new_modifier == self.current_hotkey_modifier
            and new_virtual_key == self.current_hotkey_virtual_key
        ):
            if self.hotkey_registered:
                return
            # If previously inactive (e.g., conflict), try to reactivate.
            if self.register_hotkey(new_modifier, new_virtual_key, new_name):
                self._show_tray_message(
                    self.translate("hotkey_enabled_title"),
                    self.translate("hotkey_enabled", hotkey=new_name),
                    QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                    TRAY_MSG_SHORT_MS,
                )
            else:
                self._show_tray_message(
                    self.translate("hotkey_not_updated_title"),
                    self.translate("hotkey_still_occupied", hotkey=new_name),
                    QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                    TRAY_MSG_MEDIUM_MS,
                )
            return

        # Hotkey changed, run update flow.
        old_modifier, old_virtual_key, old_name = (
            self.current_hotkey_modifier,
            self.current_hotkey_virtual_key,
            self.current_hotkey_name,
        )
        self.unregister_current_hotkey()
        re_registered_ocr = self.register_ocr_initial()
        if self.register_hotkey(new_modifier, new_virtual_key, new_name):
            self._show_tray_message(
                self.translate("hotkey_updated_title"),
                self.translate("hotkey_updated", old_hotkey=old_name, new_hotkey=new_name),
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                TRAY_MSG_SHORT_MS,
            )
            return

        # New hotkey registration failed, try restoring previous hotkey.
        if not self.register_hotkey(old_modifier, old_virtual_key, old_name):
            self._show_tray_message(
                self.translate("hotkey_error_title"),
                self.translate("hotkey_recover_failed"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Critical,
                TRAY_MSG_LONG_MS,
            )
            return

        # Re-register OCR hotkey after rollback.
        if not re_registered_ocr:
            self.register_ocr_initial()

        # Restoration succeeded; notify user that new hotkey is occupied.
        self._show_tray_message(
            self.translate("hotkey_not_updated_title"),
            self.translate("hotkey_kept_old", new_hotkey=new_name, old_hotkey=old_name),
            QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
            TRAY_MSG_MEDIUM_MS,
        )

    def schedule_hotkey_reload(self, _path):
        """Schedule hotkey reload with debounce to avoid repeated triggers during file save."""
        self._ensure_watch_targets()
        self._reload_timer.start()

    def start_watch(self, app):
        """
        Start watching config file changes.
        
        Args:
            app (QApplication): Qt application instance.
        """
        self._watcher = QtCore.QFileSystemWatcher(app)
        self._watcher.addPath(self._config_dir_path_str)
        if self.config_path.exists():
            self._watcher.addPath(self._config_file_path_str)

        # Timer used for debounce reload logic.
        self._reload_timer = QtCore.QTimer(app)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(RELOAD_TIMER_MS)

        self._watcher.fileChanged.connect(self.schedule_hotkey_reload)
        self._watcher.directoryChanged.connect(self.schedule_hotkey_reload)
        self._reload_timer.timeout.connect(self.apply_hotkey_reload)
