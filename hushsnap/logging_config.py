"""
Logging settings shared by capture/debug flows.
"""

# User default: lightweight logs only.
LOG_MODE_LIGHT = "light"
LOG_MODE_DEBUG = "debug"
DEFAULT_LOG_MODE = LOG_MODE_LIGHT
LOG_MODE_ENV = "HUSHSNAP_LOG_MODE"  # values: light | debug
DEBUG_TOPMOST_ENV = "HUSHSNAP_DEBUG_TOPMOST"  # backward-compatible override


