"""
HushSnap logging configuration module.
Initializes global logging with file rotation and env-driven log levels.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .constants import SESSION_START_MARKER

# Default logging level
DEFAULT_LEVEL = logging.INFO

def setup_logging(log_file_path: Path, force_level=None):
    """
    Initialize the global logging system.
    Includes:
    - Automatic file rotation: 5MB per file, keep 1 backup.
    - Level is DEBUG if force_level is set, otherwise INFO.
    - Formatted output with timestamp, level, module, and line number.
    
    Args:
        log_file_path (Path): Full path to the log file.
        force_level (int, optional): Explicit logging level (e.g. logging.DEBUG).
    """
    level = force_level if force_level is not None else DEFAULT_LEVEL

    # Format: [time] [level] [module:line] message
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


    try:
        # Ensure the log directory exists.
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 1. File Handler: RotatingFileHandler (roll over when file exceeds maxBytes)
        file_handler = RotatingFileHandler(
            log_file_path, 
            maxBytes=5*1024*1024, # 5MB
            backupCount=1,        # Keep one backup.
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)

        # 2. Console Handler: StreamHandler (output to stdout for real-time monitoring)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)

        # Ensure that the logging initialization is idempotent.
        logging.basicConfig(
            level=level, 
            handlers=[file_handler, console_handler], 
            force=True
        )
        
        # Suppress noisy DEBUG logs from third-party libraries
        logging.getLogger('PIL').setLevel(logging.WARNING)
        # RapidOCR prints INFO chatter (model load steps, per-image timing)
        # via its own console handler with propagate=False, so it never
        # reaches our file handler — gate the logger itself to keep the
        # console readable. Real failures still surface via ocr_controller's
        # own error logging with tracebacks.
        logging.getLogger('RapidOCR').setLevel(logging.WARNING)

        logging.info(f"{SESSION_START_MARKER} {logging.getLevelName(level)}, Path: {log_file_path}")

        # Redirect faulthandler's native-crash traceback into the log file.
        # MSIX sandboxed runs have no visible stderr, so without this a
        # segfault dies silently with no trace in the log. The file handle
        # is kept open for the process lifetime; flushing happens on crash.
        #
        # Skip when native-debug-deferred-to-JIT is active: on such machines
        # faulthandler is intentionally left off (see HushSnap.py) so native
        # crashes reach WinDbg via WER instead of being re-raised-and-exited
        # by faulthandler. Re-enabling here would undo that, so honor the same
        # gate (requires BOTH a registered JIT debugger AND HUSHSNAP_NATIVE_DEBUG).
        try:
            from .config import native_debug_deferred_to_jit
            if not native_debug_deferred_to_jit():
                import faulthandler
                faulthandler.enable(file=open(log_file_path, "a", encoding="utf-8"))
        except Exception:
            pass
    
    # Fallback: if logging initialization fails, write to a fallback file.
    except Exception as e:
        try:
            import tempfile, traceback
            fallback_dir = Path(tempfile.gettempdir()) / "HushSnap"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            fallback_path = fallback_dir / f"log_init_error_{os.getpid()}.log"
            print(f"CRITICAL: Logging failed. Error details saved to: {fallback_path}", file=sys.stderr)
            
            with open(fallback_path, "a", encoding="utf-8") as f:
                f.write(f"Failed to setup file logging: {e}\n")
                traceback.print_exc(file=f)
        except Exception as fe:
            print(
                    f"Logging initialization failed.\n"
                    f"  Original error: {e}\n"
                    f"  Fallback error: {fe}",
                    file=sys.stderr
                )

def get_logger(name: str):

    return logging.getLogger(name)
