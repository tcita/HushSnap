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


class HotkeyManager(QtCore.QObject):
    """
    Manager for Windows global hotkeys.
    Supports registration, unregistration, dynamic updates, and auto-reload on config changes.
    """
    # Signal to notify the app about hotkey status updates (e.g., success, conflict)
    # Payload: (title_key, body_key, is_error, **kwargs)
    status_requested = QtCore.pyqtSignal(str, str, bool, dict)

    def __init__(self, translate, config_path, modifier, virtual_key, name):
        """
        Initialize hotkey manager.

        Args:
            translate (callable): Translation function for i18n text.
            config_path (Path): Config file path.
            modifier (int): Initial modifier mask.
            virtual_key (int): Initial virtual key code.
            name (str): Initial human-readable hotkey name.
        """
        super().__init__()
        self.translate = translate
        self.config_path = config_path
        self.hotkey_registered = False

        # Use GlobalAddAtom to generate a system-unique hotkey ID.
        # "HushSnap_Hotkey_Atom" generates an atom in the 0xC000-0xFFFF range,
        # which helps avoid ID collisions with other programs.
        self.hotkey_id = ctypes.windll.kernel32.GlobalAddAtomW("HushSnap_Hotkey_Atom")
        if not self.hotkey_id:
            # Fallback to a fixed ID if atom creation fails.
            self.hotkey_id = 0xBFFF

        self.current_hotkey_modifier = modifier
        self.current_hotkey_virtual_key = virtual_key
        self.current_hotkey_name = name

        # Track startup conflicts so we can prompt the user after settings is ready.
        self._startup_conflicts = []  # list of ("main", hotkey_name)

        self.tray_icon = None  # set by Application after construction
        self._watcher = None
        self._reload_timer = None
        self._config_file_path_str = str(config_path)
        self._config_dir_path_str = str(config_path.parent)

    def register_initial(self):
        """
        Initial hotkey registration at application startup.
        On conflict, defers the dialog — caller should invoke
        resolve_startup_conflicts() once the settings UI is ready.

        Returns:
            bool: True on success, False on conflict.
        """
        if not ctypes.windll.user32.RegisterHotKey(
            None,
            self.hotkey_id,
            self.current_hotkey_modifier,
            self.current_hotkey_virtual_key,
        ):
            self._startup_conflicts.append(("main", self.current_hotkey_name))
            self.hotkey_registered = False
            return False

        self.hotkey_registered = True
        return True

    def resolve_startup_conflicts(self, open_settings_callback):
        """Show a dialog for any hotkey conflicts detected at startup.
        Offers the user a choice: open Settings to rebind, or continue without.

        Args:
            open_settings_callback (callable): Called when the user chooses
                "Open Settings" so the app can show the settings dialog.
        """
        if not self._startup_conflicts:
            return

        # Build combined message.
        names = [name for _, name in self._startup_conflicts]
        if len(names) == 1:
            body = self.translate("startup_conflict_single", hotkey=names[0])
        else:
            body = self.translate("startup_conflict_multiple", hotkeys=", ".join(names))

        reply = QtWidgets.QMessageBox.question(
            None,
            self.translate("error"),
            body,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            open_settings_callback()

    def _request_status_msg(self, title_key, body_key, is_error=False, **kwargs):
        """Request a status toast from the main application."""
        self.status_requested.emit(title_key, body_key, is_error, kwargs)

    def unregister_current_hotkey(self):
        """
        Unregister hotkey from the Windows system.
        Keeps the Atom ID alive for potential re-registration.
        """
        if self.hotkey_registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.hotkey_id)
            self.hotkey_registered = False

    def release_resources(self):
        """
        Final cleanup: unregister hotkeys and permanently delete Atom IDs.
        Should only be called when the application is shutting down.
        """
        self.unregister_current_hotkey()

        if hasattr(self, "hotkey_id") and self.hotkey_id:
            ctypes.windll.kernel32.GlobalDeleteAtom(self.hotkey_id)
            self.hotkey_id = 0

    def register_hotkey(self, modifier, virtual_key, name):
        """
        Try registering a new main hotkey.

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
                self.tray_icon.update_shortcuts(self.current_hotkey_name)

    def _apply_hotkey_reload_core(self):
        self._ensure_watch_targets()
        try:
            new_modifier, new_virtual_key, new_name = parse_hotkey(
                read_hotkey_text_from_config(self.config_path)
            )
        except Exception as exc:
            logger.exception(f"Failed to reload hotkey from config {self.config_path}: {exc}")
            self._request_status_msg(
                "hotkey_not_updated_title",
                "hotkey_invalid_config",
                is_error=True,
                hotkey=self.current_hotkey_name,
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
                self._request_status_msg(
                    "hotkey_enabled_title",
                    "hotkey_enabled",
                    is_error=False,
                    hotkey=new_name,
                )
            else:
                self._request_status_msg(
                    "hotkey_not_updated_title",
                    "hotkey_still_occupied",
                    is_error=True,
                    hotkey=new_name,
                )
            return

        # Hotkey changed, run update flow.
        old_modifier, old_virtual_key, old_name = (
            self.current_hotkey_modifier,
            self.current_hotkey_virtual_key,
            self.current_hotkey_name,
        )
        self.unregister_current_hotkey()
        if self.register_hotkey(new_modifier, new_virtual_key, new_name):
            self._request_status_msg(
                "hotkey_updated_title",
                "hotkey_updated",
                is_error=False,
                old_hotkey=old_name,
                new_hotkey=new_name,
            )
            return

        # New hotkey registration failed, try restoring previous hotkey.
        if not self.register_hotkey(old_modifier, old_virtual_key, old_name):
            self._request_status_msg(
                "hotkey_error_title",
                "hotkey_recover_failed",
                is_error=True,
            )
            return

        # Restoration succeeded; notify user that new hotkey is occupied.
        self._request_status_msg(
            "hotkey_not_updated_title",
            "hotkey_kept_old",
            is_error=True,
            new_hotkey=new_name,
            old_hotkey=old_name,
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
