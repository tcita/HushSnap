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
        is_pkg = res != APPMODEL_ERROR_NO_PACKAGE
        logger.info(
            "startup: is_running_as_package()=%s (GetCurrentPackageFullName returned %d, APPMODEL_ERROR_NO_PACKAGE=%d)",
            is_pkg, res, APPMODEL_ERROR_NO_PACKAGE,
        )
        return is_pkg
    except (AttributeError, Exception) as e:
        logger.info("startup: is_running_as_package() exception: %s", e, exc_info=True)
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
    is_pkg = is_running_as_package()
    logger.info("startup: get_startup_state() — is_running_as_package=%s, sys.executable=%s, frozen=%s",
                is_pkg, sys.executable, getattr(sys, 'frozen', False))

    if is_pkg:
        logger.info("startup: get_startup_state() — entering MSIX/windRT path, task_id=%s", MSIX_STARTUP_TASK_ID)
        try:
            logger.info("startup: get_startup_state() — importing winrt.windows.applicationmodel...")
            import winrt.windows.applicationmodel as appmodel
            logger.info("startup: get_startup_state() — winrt import OK, calling StartupTask.get_async(%s)...", MSIX_STARTUP_TASK_ID)
            task = await appmodel.StartupTask.get_async(MSIX_STARTUP_TASK_ID)
            logger.info("startup: get_startup_state() — get_async returned, task=%s, task.state=%s",
                        task, getattr(task, 'state', '???',))
            result = task.state in (appmodel.StartupTaskState.ENABLED, appmodel.StartupTaskState.ENABLED_BY_POLICY)
            logger.info("startup: get_startup_state() — MSIX result=%s (state=%s)", result, task.state)
            return result
        except Exception as e:
            logger.error("startup: get_startup_state() — MSIX path FAILED: %s", e, exc_info=True)
            return False
    else:
        logger.info("startup: get_startup_state() — entering non-MSIX (registry) path")
        # Check Registry
        registry_enabled = False
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            reg_name = get_startup_reg_name()
            logger.info("startup: get_startup_state() — checking registry: path=%s, name=%s", key_path, reg_name)
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                    val = winreg.QueryValueEx(key, reg_name)
                    registry_enabled = True
                    logger.info("startup: get_startup_state() — registry key found, value=%s", val)
            except FileNotFoundError:
                logger.info("startup: get_startup_state() — registry key NOT found")
                pass
        except Exception as e:
            logger.error("startup: get_startup_state() — registry check failed: %s", e, exc_info=True)

        shortcut_path = _get_startup_shortcut_path()
        shortcut_enabled = shortcut_path.exists()
        logger.info("startup: get_startup_state() — shortcut path=%s, exists=%s", shortcut_path, shortcut_enabled)

        result = registry_enabled or shortcut_enabled
        logger.info("startup: get_startup_state() — non-MSIX result=%s (registry=%s, shortcut=%s)",
                    result, registry_enabled, shortcut_enabled)
        return result

async def set_startup_state(enable: bool) -> bool:
    """
    Enable or disable "Launch at startup".
    """
    is_pkg = is_running_as_package()
    logger.info("startup: set_startup_state(enable=%s) — is_running_as_package=%s", enable, is_pkg)

    if is_pkg:
        logger.info("startup: set_startup_state() — entering MSIX/windRT path")
        try:
            logger.info("startup: set_startup_state() — importing winrt.windows.applicationmodel...")
            import winrt.windows.applicationmodel as appmodel
            logger.info("startup: set_startup_state() — winrt import OK, calling StartupTask.get_async(%s)...", MSIX_STARTUP_TASK_ID)
            task = await appmodel.StartupTask.get_async(MSIX_STARTUP_TASK_ID)
            logger.info("startup: set_startup_state() — got task, current state=%s", getattr(task, 'state', '???'))

            if enable:
                logger.info("startup: set_startup_state() — calling task.request_enable_async()...")
                result = await task.request_enable_async()
                success = result in (appmodel.StartupTaskState.ENABLED, appmodel.StartupTaskState.ENABLED_BY_POLICY)
                logger.info("startup: set_startup_state() — enable result=%s (state=%s), success=%s",
                            result, result, success)
                return success
            else:
                logger.info("startup: set_startup_state() — calling task.disable()...")
                task.disable()
                logger.info("startup: set_startup_state() — disable() completed")
                return False
        except Exception as e:
            logger.error("startup: set_startup_state() — MSIX path FAILED: %s", e, exc_info=True)
            return False
    else:
        logger.info("startup: set_startup_state() — entering non-MSIX (registry) path")
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            reg_name = get_startup_reg_name()

            shortcut_path = _get_startup_shortcut_path()
            if shortcut_path.exists():
                try:
                    shortcut_path.unlink()
                    logger.info("startup: set_startup_state() — removed old shortcut: %s", shortcut_path)
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

                logger.info("startup: set_startup_state() — writing registry: path=%s, name=%s, cmd=%s",
                            key_path, reg_name, cmd)
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, reg_name, 0, winreg.REG_SZ, cmd)
                logger.info("startup: set_startup_state() — registry write OK, result=True")
                return True
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
                        winreg.DeleteValue(key, reg_name)
                    logger.info("startup: set_startup_state() — registry value deleted")
                except FileNotFoundError:
                    logger.info("startup: set_startup_state() — registry value not found (already deleted)")
                    pass
                return False
        except Exception as e:
            logger.error("startup: set_startup_state() — registry path FAILED: %s", e, exc_info=True)
            return False
