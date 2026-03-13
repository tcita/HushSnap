"""卸载流程与引导。"""

import subprocess

from PyQt6 import QtWidgets

from ..config import get_app_dir
from ..constants import UNINSTALLER_GLOB


def find_uninstaller(app_dir):
    # Prefer the newest uninstaller to avoid launching stale unins000.exe.
    uninstaller_candidates = []
    for candidate_path in app_dir.glob(UNINSTALLER_GLOB):
        try:
            stat = candidate_path.stat()
            uninstaller_candidates.append((stat.st_mtime, candidate_path))
        except Exception:
            uninstaller_candidates.append((0.0, candidate_path))

    if not uninstaller_candidates:
        return None

    uninstaller_candidates.sort(key=lambda item: item[0], reverse=True)
    return uninstaller_candidates[0][1]


def launch_uninstaller(translate, on_quit):
    app_dir = get_app_dir()
    uninstaller_path = find_uninstaller(app_dir)
    if not uninstaller_path:
        QtWidgets.QMessageBox.warning(
            None,
            translate("uninstaller_not_found_title"),
            translate("uninstaller_not_found_body"),
        )
        return

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
        subprocess.Popen([str(uninstaller_path)], cwd=str(uninstaller_path.parent))
        on_quit()
    except Exception as exc:
        QtWidgets.QMessageBox.warning(None, translate("launch_uninstall_failed"), str(exc))

