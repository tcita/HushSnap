"""
HushSnap startup management module.
Handles "Launch at startup" functionality for both MSIX and traditional installations.
"""

import logging
import os
import sys
import ctypes
import winreg
from pathlib import Path
from ..config import get_startup_reg_name

logger = logging.getLogger(__name__)

# Task ID must match TaskId in AppxManifest_template.xml
MSIX_STARTUP_TASK_ID = "HushSnapStartup"

# Win32 error: the process is not running inside an AppX/MSIX package container
APPMODEL_ERROR_NO_PACKAGE = 15700

def is_running_as_package() -> bool:
    """
    Check if the application is running within an MSIX/AppX package container.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        length = ctypes.c_uint32(0)
        res = kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
        return res != APPMODEL_ERROR_NO_PACKAGE
    except (AttributeError, Exception):
        return False

def _get_startup_shortcut_path() -> Path:
    """Get the version-isolated shortcut path in the user's Startup folder."""
    appdata = os.getenv("APPDATA")
    if not appdata:
        return Path()
    name = get_startup_reg_name()  # "HushSnap" or "HushSnap_Dev"
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{name}.lnk"

async def get_startup_state() -> bool:
    """
    Get the current "Launch at startup" state.
    """
    if is_running_as_package():
        try:
            import winrt.windows.applicationmodel as appmodel
            task = await appmodel.StartupTask.get_async(MSIX_STARTUP_TASK_ID)
            result = task.state in (appmodel.StartupTaskState.ENABLED, appmodel.StartupTaskState.ENABLED_BY_POLICY)
            logger.info("startup: get_startup_state() MSIX result=%s (StartupTask state=%s)", result, task.state)
            return result
        except Exception as e:
            logger.error("startup: get_startup_state() MSIX path failed: %s", e, exc_info=True)
            return False
    else:
        registry_enabled = False
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            reg_name = get_startup_reg_name()
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                    winreg.QueryValueEx(key, reg_name)
                    registry_enabled = True
            except FileNotFoundError:
                pass
        except Exception as e:
            logger.error("startup: get_startup_state() registry check failed: %s", e, exc_info=True)

        shortcut_enabled = _get_startup_shortcut_path().exists()
        result = registry_enabled or shortcut_enabled
        logger.info("startup: get_startup_state() registry result=%s (registry=%s, shortcut=%s)",
                    result, registry_enabled, shortcut_enabled)
        return result

async def set_startup_state(enable: bool) -> bool:
    """
    Enable or disable "Launch at startup".
    """
    if is_running_as_package():
        try:
            import winrt.windows.applicationmodel as appmodel
            task = await appmodel.StartupTask.get_async(MSIX_STARTUP_TASK_ID)

            if enable:
                result = await task.request_enable_async()
                success = result in (appmodel.StartupTaskState.ENABLED, appmodel.StartupTaskState.ENABLED_BY_POLICY)
                logger.info("startup: set_startup_state(enable=True) MSIX result=%s (new state=%s)", success, result)
                return success
            else:
                task.disable()
                logger.info("startup: set_startup_state(enable=False) MSIX disabled")
                return False
        except Exception as e:
            logger.error("startup: set_startup_state(enable=%s) MSIX path failed: %s", enable, e, exc_info=True)
            return False
    else:
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            reg_name = get_startup_reg_name()

            shortcut_path = _get_startup_shortcut_path()
            if shortcut_path.exists():
                try:
                    shortcut_path.unlink()
                except Exception:
                    logger.debug("startup: failed to remove old startup shortcut", exc_info=True)

            if enable:
                if getattr(sys, 'frozen', False):
                    cmd = f'"{sys.executable}"'
                else:
                    python_dir = Path(sys.executable).parent
                    pythonw = python_dir / "pythonw.exe"
                    if not pythonw.exists():
                        pythonw = python_dir / "python.exe"
                    entry = (Path(__file__).resolve().parent.parent.parent / "HushSnap.py").resolve()
                    cmd = f'"{pythonw}" "{entry}"'

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, reg_name, 0, winreg.REG_SZ, cmd)
                logger.info("startup: set_startup_state(enable=True) registry written (name=%s)", reg_name)
                return True
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
                        winreg.DeleteValue(key, reg_name)
                except FileNotFoundError:
                    pass
                logger.info("startup: set_startup_state(enable=False) registry removed (name=%s)", reg_name)
                return False
        except Exception as e:
            logger.error("startup: set_startup_state(enable=%s) registry path failed: %s", enable, e, exc_info=True)
            return False
