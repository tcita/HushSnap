"""
HushSnap uninstall helper module.
Finds the Inno Setup uninstaller in app directory and guides uninstall flow.
"""

import subprocess
from PyQt6 import QtWidgets

from ..config import get_app_dir
from ..constants import UNINSTALLER_GLOB


def find_uninstaller(app_dir):
    """
    Search for uninstallers (unins*.exe) under the given directory.
    To avoid running stale files (e.g., old unins000.exe), candidates are sorted
    by modification time and the newest one is selected.
    
    Args:
        app_dir (Path): Search directory.
        
    Returns:
        Path: Newest uninstaller path, or None if not found.
    """
    uninstaller_candidates = []
    for candidate_path in app_dir.glob(UNINSTALLER_GLOB):
        try:
            stat = candidate_path.stat()
            uninstaller_candidates.append((stat.st_mtime, candidate_path))
        except Exception:
            # Fault tolerance: if stat fails, give this candidate lowest priority.
            uninstaller_candidates.append((0.0, candidate_path))

    if not uninstaller_candidates:
        return None

    # Sort by mtime descending and take the newest one.
    uninstaller_candidates.sort(key=lambda item: item[0], reverse=True)
    return uninstaller_candidates[0][1]


def launch_uninstaller(translate, on_quit):
    """
    Locate and launch uninstaller.
    Includes user confirmation and invokes quit callback after successful launch.
    
    Args:
        translate (callable): Translation function.
        on_quit (callable): Exit callback after launching uninstaller.
    """
    app_dir = get_app_dir()
    uninstaller_path = find_uninstaller(app_dir)
    if not uninstaller_path:
        # If no uninstaller is found, guide user to manual uninstall path.
        QtWidgets.QMessageBox.warning(
            None,
            translate("uninstaller_not_found_title"),
            translate("uninstaller_not_found_body"),
        )
        return

    # Confirm with dialog to avoid accidental click.
    confirm = QtWidgets.QMessageBox.question(
        None,
        translate("confirm_uninstall_title"),
        translate("confirm_uninstall_body"),
        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        QtWidgets.QMessageBox.StandardButton.No,
    )
    if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
        return

    try:
        # Start uninstaller process.
        subprocess.Popen([str(uninstaller_path)], cwd=str(uninstaller_path.parent))
        # Current app must exit while uninstaller runs to avoid file lock leftovers.
        on_quit()
    except Exception as exc:
        QtWidgets.QMessageBox.warning(None, translate("launch_uninstall_failed"), str(exc))
