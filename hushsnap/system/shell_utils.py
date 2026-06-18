"""
Windows shell utility module.
Provides helpers for interacting with Explorer, e.g. revealing a saved file.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# COM apartment model: single-threaded (STA). PyQt6's GUI thread is already
# initialized STA on Windows, so CoInitializeEx returns S_FALSE (already
# initialized) — harmless. We deliberately do NOT call CoUninitialize, since
# that would tear down Qt's own apartment.
_COINIT_APARTMENTTHREADED = 0x2


def reveal_in_explorer(path) -> bool:
    """Open the parent folder of *path* in Explorer with the file selected.

    Uses SHOpenFolderAndSelectItems (PIDL-based) rather than the
    ``explorer /select,"<path>"`` shell command. The latter routes through
    Explorer's command-line parser, which mishandles paths containing spaces
    on some Windows versions (opens "This PC" instead of selecting the file)
    and always returns exit code 1 regardless of success. The PIDL path has
    neither problem.

    Per MSDN, calling with ``cidl=0`` and a fully-specified item PIDL makes
    the API open that item's parent folder and select the item — exactly the
    "reveal" behavior we want, with no manual PIDL surgery.

    Best-effort: any failure is logged at debug level and swallowed.
    Revealing a file is a convenience on top of an already-completed save,
    so it must never raise or block the save flow. Returns True on success.
    """
    p = Path(path)
    if not p.is_file():
        logger.debug("reveal_in_explorer: file does not exist, skipping: %s", p)
        return False

    try:
        import ctypes
        from ctypes import wintypes  # noqa: F401 — ensures wintypes is available

        ole32 = ctypes.windll.ole32
        shell32 = ctypes.windll.shell32

        # Explicit signatures: PIDLs are 64-bit pointers on x64, so the
        # pointer params must be c_void_p (not the default c_int) to avoid
        # truncation. CoTaskMemFree lives in ole32, not shell32.
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        ole32.CoInitializeEx.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        shell32.SHParseDisplayName.argtypes = [
            ctypes.c_wchar_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        shell32.SHParseDisplayName.restype = ctypes.c_long
        shell32.SHOpenFolderAndSelectItems.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong,
        ]
        shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long

        # Ensure COM is initialized on this thread (no-op if Qt already did it).
        ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)

        # Resolve to an absolute, physical path (handles MSIX sandbox / symlinks).
        abs_path = str(p.resolve())

        pidl = ctypes.c_void_p()
        # SHParseDisplayName(pszPath, pbc, ppidl, sfgaoIn, psfgaoOut)
        hr = shell32.SHParseDisplayName(
            abs_path, None, ctypes.byref(pidl), 0, None,
        )
        if hr != 0 or not pidl.value:
            logger.debug(
                "reveal_in_explorer: SHParseDisplayName hr=0x%08x for %s",
                hr & 0xFFFFFFFF, abs_path,
            )
            return False

        try:
            # cidl=0 → pidl points to a fully-specified single item; the API
            # opens its parent folder and selects it. (Per MSDN.)
            hr_open = shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
            if hr_open != 0:
                logger.debug(
                    "reveal_in_explorer: SHOpenFolderAndSelectItems hr=0x%08x",
                    hr_open & 0xFFFFFFFF,
                )
                return False
            return True
        finally:
            ole32.CoTaskMemFree(pidl)
    except Exception:
        logger.debug("reveal_in_explorer: failed", exc_info=True)
        return False
