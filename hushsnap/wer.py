"""
Thread-exception hook + native-module enumeration for crash diagnosis.

Partner Center crash counts from RaiseFailFastException are not useful
(retail Windows produces "Uncategorized" entries with no stacks or
.cab files), so the fail-fast approach was removed.  The real diagnostic
data is the log file (traceback + loaded-module list), which users can
send us via the crash dialog's "Save to Desktop" button.
"""

import ctypes
import logging
import os
import threading
from ctypes import wintypes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Native module enumeration (Toolhelp32 snapshot)
# ---------------------------------------------------------------------------

# MODULEENTRY32W (partial — only the fields we read)
_MAX_MODULE_NAME32 = 255
_MAX_PATH = 260


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize",        wintypes.DWORD),
        ("th32ModuleID",  wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage",  wintypes.DWORD),
        ("ProccntUsage",  wintypes.DWORD),
        ("modBaseAddr",   ctypes.c_void_p),
        ("modBaseSize",   wintypes.DWORD),
        ("hModule",       ctypes.c_void_p),
        ("szModule",      ctypes.c_wchar * (_MAX_MODULE_NAME32 + 1)),
        ("szExePath",     ctypes.c_wchar * _MAX_PATH),
    ]


TH32CS_SNAPMODULE = 0x00000008


def _toolhelp_module_snapshot():
    """Return a callable for CreateToolhelp32Snapshot from kernel32,
    or None on non-Windows."""
    if os.name != "nt":
        return None
    for dll_name in ("kernelbase", "kernel32"):
        try:
            dll = ctypes.WinDLL(dll_name)
        except OSError:
            continue
        fn = getattr(dll, "CreateToolhelp32Snapshot", None)
        if fn is not None:
            return fn
    return None


def log_loaded_modules(target_logger):
    """Enumerate loaded native modules via Toolhelp32 and write one
    compact line per module to *target_logger* at CRITICAL level.

    System modules (paths containing ``C:\\Windows\\``) are skipped to
    keep the output focused on application and third-party DLLs that are
    most relevant to crash diagnosis (e.g. MSVCP ABI mismatches, stale
    OpenCV artefacts, unexpected GPU driver loads).

    Any failure inside this function is swallowed — module enumeration
    must never cascade a crash already in progress.
    """
    try:
        snapshot = _toolhelp_module_snapshot()
        if snapshot is None:
            return

        snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        snapshot.restype = ctypes.c_void_p

        Module32First = ctypes.windll.kernel32.Module32FirstW
        Module32First.argtypes = [ctypes.c_void_p, ctypes.POINTER(MODULEENTRY32W)]
        Module32First.restype = wintypes.BOOL

        Module32Next = ctypes.windll.kernel32.Module32NextW
        Module32Next.argtypes = [ctypes.c_void_p, ctypes.POINTER(MODULEENTRY32W)]
        Module32Next.restype = wintypes.BOOL

        h = snapshot(TH32CS_SNAPMODULE, 0)
        if h == ctypes.c_void_p(-1).value:  # INVALID_HANDLE_VALUE
            return

        try:
            windows = "\\windows\\"
            lines = []
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(MODULEENTRY32W)

            if Module32First(h, ctypes.byref(entry)):
                while True:
                    path = entry.szExePath.lower()
                    if windows not in path:
                        lines.append(
                            f"  {entry.modBaseAddr:#018x}  {entry.modBaseSize:>8d}  {entry.szExePath}"
                        )
                    if not Module32Next(h, ctypes.byref(entry)):
                        break

            # Build one multi-line string and emit a single CRITICAL
            # call.  65 individual logger.critical() calls (1 summary
            # + 64 modules in a typical dev session) each walk the
            # full logging pipeline; combining them into one write
            # keeps the crash-handler pause as short as possible.
            target_logger.critical(
                "Loaded modules (non-system, %d loaded):\n%s",
                len(lines),
                "\n".join(lines),
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))
    except Exception:
        pass


def install_threading_excepthook():
    """Install a *non-fatal* handler for unhandled exceptions in worker
    threads.  They are logged only — a dead background thread (e.g. an
    OCR worker) must not crash a still-running app.

    Only installs once; safe to call repeatedly.
    """
    if getattr(threading, "_hushsnap_threading_hook_installed", False):
        return

    def _hook(args):
        try:
            crash_logger = logging.getLogger("HushSnap")
            crash_logger.critical(
                "Unhandled exception in thread %r:",
                getattr(args.thread, "name", args.thread),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            log_loaded_modules(crash_logger)
        except Exception:
            pass

    threading.excepthook = _hook
    threading._hushsnap_threading_hook_installed = True
