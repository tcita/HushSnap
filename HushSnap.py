"""Application entry point: launch the main app."""

import os
# OpenBLAS pre-allocates ~32 MB of virtual memory per thread. At the
# default MAX_THREADS=24 that is ~768 MB committed but mostly untouched.
# OCR workloads do array conversion only, not BLAS compute, so a single
# thread keeps virtual memory minimal with no performance impact.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

# Enable faulthandler at the absolute earliest point so a native crash
# (segfault / access violation) prints the Python stack trace instead of
# dying silently. setup_logging() later redirects it into the log file once
# the file path is known.
#
# In a PyInstaller --windowed (no-console) build sys.stderr is None, and
# faulthandler.enable() with no ``file`` argument then raises
# RuntimeError("sys.stderr is None") — i.e. this very crash-capture line
# would crash the packaged app at boot. Fall back to a temp crash file so
# very-early native crashes are still recorded; setup_logging() re-points
# faulthandler at the real log file shortly after. Never let this block boot.
#
# SKIP faulthandler entirely when a JIT debugger (WinDbg) is registered on
# this machine: faulthandler's fatal-exception handler re-raises after
# dumping the Python stack, and on Windows that re-raise does not reliably
# reach the JIT debugger — the process dies before WinDbg can attach,
# producing a silent exit with no debugger prompt. With no faulthandler
# installed, the AV flows through WER's unhandled-exception dispatch straight
# to WinDbg, which freezes the process at the fault site. Installing WinDbg
# + ``windbg -I`` is a deliberate admin action no ordinary user takes, so
# this single gate keeps the behavior off on production machines. Production
# machines have no JIT debugger registered, so faulthandler stays enabled
# there (preserving the Python stack in the log for silent MSIX crashes).
import faulthandler
from hushsnap.config import jit_debugger_configured

if not jit_debugger_configured():
    try:
        faulthandler.enable()
    except RuntimeError:
        import tempfile
        _early_crash = open(
            os.path.join(tempfile.gettempdir(), "HushSnap_early_crash.log"),
            "a", encoding="utf-8",
        )
        faulthandler.enable(file=_early_crash)
    except Exception:
        pass

import time
# Record the absolute earliest possible start time in the Python process.
BOOT_START_TIME = time.perf_counter()

from hushsnap.app import main


if __name__ == "__main__":
    main(boot_start_time=BOOT_START_TIME)
