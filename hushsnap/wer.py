"""
Windows Error Reporting (WER) integration.

Only two pieces remain after empirically verifying that Partner Center
Health does NOT surface per-crash detail (call stacks, .cab downloads,
custom metadata, or registered memory blocks) for MSIX-packaged
FullTrust applications — regardless of signature type.  See the
test/crashlib-integration branch for the full experimental record.

1. raise_fail_fast             — turn an unhandled Python exception into
   a WER-reportable crash so Partner Center at least counts it.
   Without this a plain Python exit (non-zero rc) is invisible to WER.
2. install_threading_excepthook — log-only handler for worker-thread
   exceptions; a dead OCR thread must not take down the app.

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


def raise_fail_fast():
    """Raise a fail-fast exception.  WER captures a crash signal and
    uploads it to Partner Center; the process does not continue.  Falls
    back to os._exit(1) if the API is missing or somehow returns."""
    if _raise_fail_fast is not None:
        try:
            _raise_fail_fast.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
            _raise_fail_fast.restype = None
            _raise_fail_fast(None, None, 0)
        except Exception:
            logger.debug("RaiseFailFastException threw", exc_info=True)
    os._exit(1)


def install_threading_excepthook():
    """Install a *non-fatal* handler for unhandled exceptions in worker
    threads.  They are logged only — a dead background thread (e.g. an
    OCR worker) must not crash a still-running app.  This is the
    counterpart to the main-thread fail-fast.

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

    threading.excepthook = _hook
    threading._hushsnap_threading_hook_installed = True
