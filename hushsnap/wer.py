"""
Windows Error Reporting (WER) integration.

Goal: make the app's crashes surface in Partner Center's Health report
accurately, and carry enough context to be useful when you inspect a
downloaded .cab.

Three pieces, all Windows-only (1809+; our MSIX MinVersion is 17763):
1. WerRegisterCustomMetadata  — attach key/value pairs (version, OCR
   engine, locale) to every crash report. Visible in Partner Center when
   you open a crash and download its .cab.
2. WerRegisterAppLocalDump    — WER also drops a local minidump copy on
   the user's machine, alongside our own procdump monitoring.
3. WerRegisterMemoryBlock +
   RaiseFailFastException      — on an unhandled *main-thread* Python
   exception we write the traceback into a pre-registered memory block
   (so it lands in the .cab) and raise a fail-fast exception. That turns
   a silent Python exit — which WER never sees — into a real
   WER-reportable crash.

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
_wer_register_app_local_dump = _probe("WerRegisterAppLocalDump")
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


def register_local_dump(folder):
    """Ask WER to keep a local minidump copy under `folder` for this process."""
    if _wer_register_app_local_dump is None:
        return False
    try:
        os.makedirs(folder, exist_ok=True)
        _wer_register_app_local_dump.argtypes = [wintypes.LPCWSTR]
        _wer_register_app_local_dump.restype = ctypes.c_long
        hr = _wer_register_app_local_dump(str(folder))
        if hr < 0:
            logger.debug("WerRegisterAppLocalDump hr=0x%08x", hr & 0xFFFFFFFF)
            return False
        return True
    except Exception:
        logger.debug("WerRegisterAppLocalDump failed", exc_info=True)
        return False


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
    exit if the API is missing or somehow returns.

    If a debugger is attached (procdump's -e monitoring on a dev machine),
    skip fail-fast and let the debugger handle the unhandled exception
    instead. Reason: the FIRST end-to-end test of this path (6/28-22:29)
    showed RaiseFailFastException racing procdump — procdump intercepted
    the exception, tried to write a dump in the already-corrupted process
    context, and died with 0xc0000409 (stack-buffer-overrun), producing a
    0-byte dump AND killing the monitor for any subsequent crash. A later
    run (22:48) coexisted fine, proving the outcome is non-deterministic —
    same code path, opposite result. We can't tolerate the monitor being
    taken out, so when a debugger is present we defer to it entirely.

    Cost: on a dev machine, a Python unhandled exception won't reach WER
    (no Partner Center entry for that incident). That's acceptable — on a
    dev machine you have the local log + procdump's full dump directly,
    and don't need the Partner Center round-trip. User machines have no
    debugger, so IsDebuggerPresent() is always False there and WER
    reporting works exactly as intended. Verified: procdump -e attach
    flips IsDebuggerPresent() to True within ~1.2s of process start.

    Native crashes (access violations etc.) don't go through Python's
    excepthook at all, so this branch never affects them — procdump
    catches those unconditionally regardless of this check.
    """
    try:
        if ctypes.windll.kernel32.IsDebuggerPresent():
            logger.debug("Debugger attached — skipping fail-fast, letting "
                         "debugger (procdump) handle the crash. WER will not "
                         "report this incident; see local procdump dump.")
            # The attached debugger (procdump) captures the dump when the
            # unhandled exception reaches it. Fall through to a hard exit so
            # the process ends without raising fail-fast.
            os._exit(1)
    except Exception:
        # If the check itself fails, be conservative and fail-fast as before.
        pass

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
