"""
HushSnap startup management module.
Handles "Launch at startup" functionality for both MSIX and traditional installations.
"""

import logging
import os
import sys
import ctypes
import asyncio
from pathlib import Path

logger = logging.getLogger(__name__)

# Task ID must match TaskId in AppxManifest_template.xml
MSIX_STARTUP_TASK_ID = "HushSnapStartup"

def is_running_as_package() -> bool:
    """
    Check if the application is running within an MSIX/AppX package container.
    """
    try:
        # GetCurrentPackageFullName returns APPMODEL_ERROR_NO_PACKAGE (15700L) if not in a package.
        kernel32 = ctypes.windll.kernel32
        length = ctypes.c_uint32(0)
        res = kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
        # 15700 is APPMODEL_ERROR_NO_PACKAGE
        return res != 15700
    except (AttributeError, Exception):
        return False

def _get_startup_shortcut_path() -> Path:
    """Get the path to the HushSnap shortcut in the user's Startup folder."""
    appdata = os.getenv("APPDATA")
    if not appdata:
        return Path()
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "HushSnap.lnk"

async def get_startup_state() -> bool:
    """
    Get the current "Launch at startup" state.
    
    Returns:
        bool: True if enabled, False otherwise.
    """
    if is_running_as_package():
        try:
            import winrt.windows.applicationmodel as appmodel
            task = await appmodel.StartupTask.get_async(MSIX_STARTUP_TASK_ID)
            # ENABLED = 2, ENABLED_BY_POLICY = 4
            return task.state in (appmodel.StartupTaskState.ENABLED, appmodel.StartupTaskState.ENABLED_BY_POLICY)
        except Exception as e:
            logger.error(f"Failed to get MSIX startup state: {e}")
            return False
    else:
        # Fallback for non-MSIX installations: check Registry and Startup folder shortcut.
        
        # 1. Check Registry
        registry_enabled = False
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                    winreg.QueryValueEx(key, "HushSnap")
                    registry_enabled = True
            except FileNotFoundError:
                pass
        except Exception as e:
            logger.error(f"Failed to check registry startup state: {e}")

        # 2. Check Startup folder shortcut
        shortcut_path = _get_startup_shortcut_path()
        shortcut_enabled = shortcut_path.exists()
        
        return registry_enabled or shortcut_enabled

async def set_startup_state(enable: bool) -> bool:
    """
    Enable or disable "Launch at startup".
    
    Args:
        enable (bool): True to enable, False to disable.
        
    Returns:
        bool: Resulting state.
    """
    if is_running_as_package():
        try:
            import winrt.windows.applicationmodel as appmodel
            task = await appmodel.StartupTask.get_async(MSIX_STARTUP_TASK_ID)
            if enable:
                result = await task.request_enable_async()
                return result in (appmodel.StartupTaskState.ENABLED, appmodel.StartupTaskState.ENABLED_BY_POLICY)
            else:
                task.disable()
                return False
        except Exception as e:
            logger.error(f"Failed to set MSIX startup state: {e}")
            return False
    else:
        # Fallback for traditional installations: use Registry but ALSO clean up shortcut
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            
            # Clean up shortcut if we are disabling or if we want to "take over" via registry
            shortcut_path = _get_startup_shortcut_path()
            if shortcut_path.exists():
                try:
                    shortcut_path.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete startup shortcut: {e}")

            if enable:
                if getattr(sys, 'frozen', False):
                    # PyInstaller bundle: sys.executable is HushSnap.exe
                    cmd = f'"{sys.executable}"'
                else:
                    # Running from source: need pythonw.exe + path to HushSnap.py
                    _python_dir = Path(sys.executable).parent
                    _pythonw = _python_dir / "pythonw.exe"
                    if not _pythonw.exists():
                        _pythonw = _python_dir / "python.exe"
                    _entry = (Path(__file__).resolve().parent.parent.parent / "HushSnap.py").resolve()
                    cmd = f'"{_pythonw}" "{_entry}"'
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "HushSnap", 0, winreg.REG_SZ, cmd)
                return True
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
                        winreg.DeleteValue(key, "HushSnap")
                except FileNotFoundError:
                    pass
                return False
        except Exception as e:
            logger.error(f"Failed to set registry startup state: {e}")
            return False
