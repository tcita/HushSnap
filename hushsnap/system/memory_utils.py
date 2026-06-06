"""Shared process-memory query helpers for debug-level memory tracking.

Provides a single, cached implementation of ``get_working_set_mb()`` so that
every memory-management checkpoint in the codebase reports consistent numbers
without re-declaring ctypes signatures on every call.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import sys
import threading

logger = logging.getLogger(__name__)

_HANDLE: ctypes.c_void_p | None = None
_INIT_LOCK = threading.Lock()
_SIZE_T = ctypes.c_size_t
_SSIZE_T = ctypes.c_ssize_t

# Cached function pointers
_GET_PROCESS_MEMORY_INFO = None
_SET_PROCESS_WORKING_SET_SIZE = None


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", _SIZE_T),
        ("WorkingSetSize", _SIZE_T),
        ("QuotaPeakPagedPoolUsage", _SIZE_T),
        ("QuotaPagedPoolUsage", _SIZE_T),
        ("QuotaPeakNonPagedPoolUsage", _SIZE_T),
        ("QuotaNonPagedPoolUsage", _SIZE_T),
        ("PagefileUsage", _SIZE_T),
        ("PeakPagefileUsage", _SIZE_T),
    ]


def _init() -> ctypes.c_void_p | None:
    """One-time ctypes binding. Returns the current process pseudo-handle."""
    global _HANDLE, _GET_PROCESS_MEMORY_INFO, _SET_PROCESS_WORKING_SET_SIZE
    if _HANDLE is not None:
        return _HANDLE

    if sys.platform != "win32":
        return None

    with _INIT_LOCK:
        if _HANDLE is not None:
            return _HANDLE

        try:
            kernel32 = ctypes.windll.kernel32

            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            _HANDLE = kernel32.GetCurrentProcess()

            psapi = ctypes.windll.psapi
            _GET_PROCESS_MEMORY_INFO = psapi.GetProcessMemoryInfo
            _GET_PROCESS_MEMORY_INFO.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
                ctypes.wintypes.DWORD,
            ]
            _GET_PROCESS_MEMORY_INFO.restype = ctypes.wintypes.BOOL

            _SET_PROCESS_WORKING_SET_SIZE = kernel32.SetProcessWorkingSetSize
            _SET_PROCESS_WORKING_SET_SIZE.argtypes = [
                ctypes.c_void_p,
                _SSIZE_T,
                _SSIZE_T,
            ]
            _SET_PROCESS_WORKING_SET_SIZE.restype = ctypes.c_int

            return _HANDLE
        except Exception:
            logger.debug("Failed to initialise Win32 memory APIs", exc_info=True)
            return None


def trim_working_set() -> bool:
    """Aggressively trim the process working set. Returns True on success.

    Safe to call from any thread; uses internal locks to ensure Win32 APIs
    are initialized correctly.
    """
    handle = _init()
    if handle is None or _SET_PROCESS_WORKING_SET_SIZE is None:
        return False

    try:
        # Use -1, -1 as specified in Win32 docs to swap as much as possible to the pagefile
        res = _SET_PROCESS_WORKING_SET_SIZE(handle, -1, -1)
        return res != 0
    except Exception:
        logger.debug("trim_working_set failed", exc_info=True)
        return False


def get_working_set_mb() -> float:
    """Return the current process WorkingSetSize in MiB, or -1.0 on failure."""
    handle = _init()
    if handle is None or _GET_PROCESS_MEMORY_INFO is None:
        return -1.0

    try:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        if _GET_PROCESS_MEMORY_INFO(handle, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / (1024.0 * 1024.0)
    except Exception:
        logger.debug("get_working_set_mb failed", exc_info=True)
    return -1.0


def get_memory_stats() -> dict[str, float]:
    """Return a dict of process memory counters in MiB.

    Keys: working_set_mb, peak_working_set_mb, pagefile_mb, peak_pagefile_mb.
    All values are -1.0 on failure / unsupported platform.
    """
    handle = _init()
    if handle is None or _GET_PROCESS_MEMORY_INFO is None:
        return {
            "working_set_mb": -1.0,
            "peak_working_set_mb": -1.0,
            "pagefile_mb": -1.0,
            "peak_pagefile_mb": -1.0,
        }

    try:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        if _GET_PROCESS_MEMORY_INFO(handle, ctypes.byref(counters), counters.cb):
            return {
                "working_set_mb": counters.WorkingSetSize / (1024.0 * 1024.0),
                "peak_working_set_mb": counters.PeakWorkingSetSize / (1024.0 * 1024.0),
                "pagefile_mb": counters.PagefileUsage / (1024.0 * 1024.0),
                "peak_pagefile_mb": counters.PeakPagefileUsage / (1024.0 * 1024.0),
            }
    except Exception:
        logger.debug("get_memory_stats failed", exc_info=True)
    return {
        "working_set_mb": -1.0,
        "peak_working_set_mb": -1.0,
        "pagefile_mb": -1.0,
        "peak_pagefile_mb": -1.0,
    }


def fmt_memory() -> str:
    """One-line human-readable memory summary for debug logs."""
    ws = get_working_set_mb()
    if ws < 0:
        return "WS=unavailable"
    return f"WS={ws:.1f} MB"
