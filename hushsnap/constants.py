"""
HushSnap constants module.
Stores shared Windows message IDs, hotkey modifiers, filenames, and logic thresholds.
"""

# --- 1. Native Windows hotkey constants ---
# Windows hotkey message ID
WM_HOTKEY = 0x0312
# Hotkey modifier masks (used by RegisterHotKey)
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
# Default activation hotkey
DEFAULT_HOTKEY = "Alt+Q"
# OCR screenshot hotkey (always runs OCR regardless of toggle state)
DEFAULT_OCR_HOTKEY = "Alt+Shift+Q"

# --- 2. File and system constants ---
# Config filename (user-editable)
APP_CONFIG_FILENAME = "hushsnap_config.toml"
# State filename (internal persistence, not user-editable)
APP_STATE_FILENAME = "hushsnap_state.toml"
# Installer-provided language hint filename
INSTALLER_LANG_FILENAME = "hushsnap_installer_lang.txt"
# Debug log filename
CAPTURE_DEBUG_LOG_FILENAME = "hushsnap_capture_debug.log"
# Tray icon filename
APP_ICON_FILENAME = "ico.ico"
# Uninstaller lookup glob
UNINSTALLER_GLOB = "unins*.exe"
# Named mutex for single-instance enforcement
SINGLE_INSTANCE_MUTEX = "Local\\hushsnap.SingleInstance"

# --- 3. Logic thresholds and timing (ms/px) ---
# Hotkey re-registration debounce delay
RELOAD_TIMER_MS = 300
# Tray message duration (short)
TRAY_MSG_SHORT_MS = 2000
# Tray message duration (medium)
TRAY_MSG_MEDIUM_MS = 3000
# Tray message duration (long)
TRAY_MSG_LONG_MS = 4000
# Disable tray balloon notifications entirely to avoid Windows notification sounds.
TRAY_NOTIFICATIONS_ENABLED = False
# OCR engine identifiers.
OCR_ENGINE_WINDOWS = "windows"
OCR_ENGINE_RAPID = "rapidocr"
# Small topmost audit delay in debug mode
DEBUG_TOPMOST_DELAY_MS = 120

# Pixel threshold treated as click (not drag)
CAPTURE_CLICK_THRESHOLD_PX = 8
# Minimum valid selection size
CAPTURE_SELECTION_MIN_PX = 10
# RGBA color for screenshot overlay mask
CAPTURE_OVERLAY_RGBA = (0, 0, 0, 80)
# Log timestamp format
CAPTURE_LOG_TS_FMT = "%Y-%m-%d %H:%M:%S"
