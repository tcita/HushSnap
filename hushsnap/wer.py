"""
Windows Error Reporting (WER) integration.

Goal: make the app's crashes surface in Partner Center's Health report
accurately, and carry enough context to be useful when you inspect a
downloaded .cab.

Two pieces, all Windows-only (1809+; our MSIX MinVersion is 17763):
1. WerRegisterCustomMetadata  — attach key/value pairs (version, OCR
   engine, locale) to every crash report. Visible in Partner Center when
   you open a crash and download its .cab.
2. WerRegisterMemoryBlock +
   RaiseFailFastException      — on an unhandled *main-thread* Python
   exception we write the traceback into a pre-registered memory block
   (so it lands in the .cab) and raise a fail-fast exception. That turns
   a silent Python exit — which WER never sees — into a real
   WER-reportable crash.

Notably absent: WerRegisterAppLocalDump. It was tried and removed — see
the comment where it used to be called (app.py) and NATIVE_CRASH_DEBUGGING.md.
In short: for an MSIX-packaged app the API returns S_OK but WER ignores the
registered folder and writes dumps to the system-default
%LOCALAPPDATA%\\CrashDumps instead (MoAppCrash path). Not reliable enough
to keep.

Worker-thread exceptions deliberately do NOT fail-fast: a dead OCR thread
must not take down a still-functioning screenshot app. They are logged
only (see install_threading_excepthook).

Why we can't just rely on faulthandler: faulthandler catches *native*
crashes (segfaults) — those already trigger WER naturally — and writes a
traceback to our local log file. But an unhandled Python exception exits
the process cleanly with a non-zero code; WER ignores it, so Partner
Center never learns it happened. Fail-fast is what bridges that gap.

On non-Windows / older Windows these calls degrade to no-ops, so the
module is safe to import anywhere.
"""

import ctypes
import logging
import os
import sys
import threading
from ctypes import wintypes

logger = logging.getLogger(__name__)

# Max bytes WER will collect from a single registered memory block. Keep
# the traceback buffer comfortably under this so it survives intact.
_MAX_MEM_BLOCK = 65536
_TRACEBACK_BUFFER_SIZE = 8192

# Hold strong references so the registered memory is not GC'd before a
# crash — WER reads it at fault time, by address.
_traceback_buffer = None  # type: ctypes.Array | None


def _probe(func_name):
    """Return a callable for a WER/kernel function from kernelbase, falling
    back to kernel32. Returns None if unavailable (non-Windows or old OS)."""
    if os.name != "nt":
        return None
    for dll_name in ("kernelbase", "kernel32"):
        try:
            dll = ctypes.WinDLL(dll_name)
        except OSError:
            continue
        fn = getattr(dll, func_name, None)
        if fn is not None:
            return fn
    return None


_raise_fail_fast = _probe("RaiseFailFastException")
_wer_register_custom_metadata = _probe("WerRegisterCustomMetadata")
_wer_register_memory_block = _probe("WerRegisterMemoryBlock")


def register_metadata(key, value):
    """Attach a key/value pair to every WER report for this process.

    Keys/values must be short strings. Safe to call multiple times to add
    more pairs. No-op if the API is unavailable.
    """
    if _wer_register_custom_metadata is None:
        return False
    try:
        _wer_register_custom_metadata.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        _wer_register_custom_metadata.restype = ctypes.c_long  # HRESULT
        hr = _wer_register_custom_metadata(str(key), str(value))
        if hr < 0:
            logger.debug("WerRegisterCustomMetadata(%s) hr=0x%08x", key, hr & 0xFFFFFFFF)
            return False
        return True
    except Exception:
        logger.debug("WerRegisterCustomMetadata(%s) failed", key, exc_info=True)
        return False


# NOTE: WerRegisterAppLocalDump was previously exposed here and called from
# app.py to ask WER to drop a local minidump under <user_data_dir>/wer_dumps.
# It has been removed because it is unreliable for an MSIX-packaged app:
# the API returns S_OK, but on the MoAppCrash path WER ignores the registered
# folder and writes dumps to the system-default %LOCALAPPDATA%\CrashDumps
# instead. Verified empirically with a minimal packaged crash-trigger exe
# (both relative and absolute registered paths were ignored identically).
# The registry-based LocalDumps approach is also skipped by MoAppCrash — see
# scripts/NATIVE_CRASH_DEBUGGING.md. For reliable native crash capture, rely
# on the WinDbg JIT path on dev machines (also documented there).


def register_traceback_buffer(size=_TRACEBACK_BUFFER_SIZE):
    """Register a fixed memory block that WER will snapshot into the .cab on
    crash. Returns the buffer (keep the returned reference alive) or None.

    We write the current unhandled exception's traceback into this buffer
    immediately before raising fail-fast, so each downloaded .cab carries
    the exact Python traceback that caused it — even though all fail-fast
    crashes otherwise share one WER bucket.
    """
    global _traceback_buffer
    if _wer_register_memory_block is None:
        return None
    try:
        buf = ctypes.create_string_buffer(size)
        _wer_register_memory_block.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        _wer_register_memory_block.restype = ctypes.c_long
        hr = _wer_register_memory_block(buf, wintypes.DWORD(size))
        if hr < 0:
            logger.debug("WerRegisterMemoryBlock hr=0x%08x", hr & 0xFFFFFFFF)
            return None
        _traceback_buffer = buf  # keep alive for process lifetime
        return buf
    except Exception:
        logger.debug("WerRegisterMemoryBlock failed", exc_info=True)
        return None


def write_traceback(text):
    """Write `text` (UTF-8) into the registered traceback buffer, truncating
    to fit. Keeps the trailing (most recent) frames when truncating, since
    the crash site is at the end of a Python traceback. No-op if no buffer
    was registered."""
    buf = _traceback_buffer
    if buf is None:
        return
    try:
        data = text.encode("utf-8", "replace")
        limit = len(buf) - 1  # leave room for NUL
        if len(data) > limit:
            data = data[-limit:]
        buf.value = data
    except Exception:
        pass


def raise_fail_fast():
    """Raise a fail-fast exception. WER captures a crash dump and uploads it
    to Partner Center; the process does not continue. Falls back to a hard
    exit if the API is missing or somehow returns."""
    if _raise_fail_fast is not None:
        try:
            _raise_fail_fast.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
            _raise_fail_fast.restype = None
            # NULL exception record + NULL context + 0 flags: the crash is
            # bucketed as a generic fail-fast. Per-incident detail comes from
            # the registered memory block + metadata, not the bucket itself.
            _raise_fail_fast(None, None, 0)
        except Exception:
            logger.debug("RaiseFailFastException threw", exc_info=True)
    # Should be unreachable; if WER is disabled the call may return.
    os._exit(1)


def install_threading_excepthook():
    """Install a *non-fatal* handler for unhandled exceptions in worker
    threads. They are logged only — a dead background thread (e.g. an OCR
    worker) must not crash a still-running app, so we deliberately do NOT
    fail-fast here. This is the counterpart to the main-thread fail-fast.

    Only installs once; safe to call repeatedly.
    """
    if getattr(threading, "_hushsnap_threading_hook_installed", False):
        return

    def _hook(args):
        try:
            logging.getLogger("HushSnap").critical(
                "Unhandled exception in thread %r:",
                getattr(args.thread, "name", args.thread),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception:
            pass
        # Fall through to default behavior (prints to stderr) for parity with
        # the previous implicit default; we do NOT raise fail-fast.

    threading.excepthook = _hook
    threading._hushsnap_threading_hook_installed = True
