"""
HushSnap 卸载辅助模块
负责在程序目录下查找 Inno Setup 生成的卸载程序，并引导用户进行卸载。
"""

import subprocess
from PyQt6 import QtWidgets

from ..config import get_app_dir
from ..constants import UNINSTALLER_GLOB


def find_uninstaller(app_dir):
    """
    在指定目录下搜索卸载程序（unins*.exe）。
    为了避免执行到旧的卸载文件（如 unins000.exe 可能不是最新的），
    该函数会根据文件修改时间排序，优先选择最新创建的卸载程序。
    
    Args:
        app_dir (Path): 搜索目录。
        
    Returns:
        Path: 找到的最新的卸载程序路径，未找到则返回 None。
    """
    uninstaller_candidates = []
    for candidate_path in app_dir.glob(UNINSTALLER_GLOB):
        try:
            stat = candidate_path.stat()
            uninstaller_candidates.append((stat.st_mtime, candidate_path))
        except Exception:
            # 容错：如果无法获取属性，则给予最低权重
            uninstaller_candidates.append((0.0, candidate_path))

    if not uninstaller_candidates:
        return None

    # 按修改时间倒序排列，取最新一个
    uninstaller_candidates.sort(key=lambda item: item[0], reverse=True)
    return uninstaller_candidates[0][1]


def launch_uninstaller(translate, on_quit):
    """
    查找并启动卸载程序。
    包含用户确认步骤，并在成功启动后调用退出回调以关闭当前程序。
    
    Args:
        translate (callable): 翻译函数。
        on_quit (callable): 启动卸载后的退出回调。
    """
    app_dir = get_app_dir()
    uninstaller_path = find_uninstaller(app_dir)
    if not uninstaller_path:
        # 如果没找到卸载程序，引导用户手动在控制面板操作
        QtWidgets.QMessageBox.warning(
            None,
            translate("uninstaller_not_found_title"),
            translate("uninstaller_not_found_body"),
        )
        return

    # 弹出对话框确认，防止误点
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
        # 启动卸载进程
        subprocess.Popen([str(uninstaller_path)], cwd=str(uninstaller_path.parent))
        # 卸载程序运行时，当前程序必须退出，否则文件会被占用导致卸载不彻底
        on_quit()
    except Exception as exc:
        QtWidgets.QMessageBox.warning(None, translate("launch_uninstall_failed"), str(exc))
