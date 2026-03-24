"""
HushSnap logging configuration module.
Initializes global logging with file rotation and env-driven log levels.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Environment variable that controls the logging level (e.g. $env:HUSHSNAP_LOG_LEVEL = "DEBUG")
LOG_LEVEL_ENV = "HUSHSNAP_LOG_LEVEL"
# Default logging level
DEFAULT_LEVEL = logging.INFO

def setup_logging(log_file_path: Path):
    """
    Initialize the global logging system.
    Includes:
    - Automatic file rotation: 5MB per file, keep 1 backup.
    - Dynamic level from environment variable (default INFO).
    - Formatted output with timestamp, level, module, and line number.
    
    Args:
        log_file_path (Path): Full path to the log file.
    """
    # 1. Resolve the log level (default INFO).
    level_str = os.environ.get(LOG_LEVEL_ENV, "INFO").upper().strip()
    # Convert level name to logging constant (DEBUG, INFO, etc.).
    level = getattr(logging, level_str, DEFAULT_LEVEL)


    # 2. Define formatter.
    # Format: [time] [level] [module:line] message
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 3. Configure rotating file handler.
    try:
        # Ensure the log directory exists.
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # RotatingFileHandler: roll over when file exceeds maxBytes.
        file_handler = RotatingFileHandler(
            log_file_path, 
            maxBytes=5*1024*1024, # 5MB
            backupCount=1,        # Keep one backup.
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)

        # 4. Configure root logger.
        root = logging.getLogger()
        root.setLevel(level)
        
        # Clear existing handlers to avoid duplicate output (common in tests/reload).
        if root.hasHandlers():
            root.handlers.clear()
        
        # Attach file handler to root logger.
        root.addHandler(file_handler)
        
        logging.info(f"Logging initialized. Level: {logging.getLevelName(level)}, Path: {log_file_path}")
    except Exception as e:
        # Fallback: if logging initialization fails, write to a fallback file.
        try:
            fallback_path = Path.home() / "AppData" / "Local" / "HushSnap" / "log_init_error.log"
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            with open(fallback_path, "a", encoding="utf-8") as f:
                f.write(f"Failed to setup file logging: {e}\n")
        except Exception:
            # Last-resort fallback: never crash because logging setup failed.
            pass

def get_logger(name: str):

    return logging.getLogger(name)
