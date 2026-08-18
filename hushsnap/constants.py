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


# --- 2. File and system constants ---
# Config filename (user-editable)
APP_CONFIG_FILENAME = "hushsnap_config.toml"
# State filename (internal persistence, not user-editable)
APP_STATE_FILENAME = "hushsnap_state.toml"

# Debug log filename
CAPTURE_DEBUG_LOG_FILENAME = "hushsnap.log"
# Tray icon filename
APP_ICON_FILENAME = "hushsnap.ico"
# Filename of the OCR server discovery file. The running app writes it into the
# user data dir (bound port + auth token); the CLI reads it to find the loopback
# server and authenticate.  Removed on clean app exit.
OCR_SERVER_FILENAME = "ocr_server.json"
# Error string carried by an OcrResponse when an async OCR request was superseded
# by a newer request before its result could be delivered.  Callers that submit
# with notify_if_dropped=True receive this instead of hanging; callers that don't
# keep the historical silent-drop behaviour (see OcrService.recognize_async).
OCR_SUPERSEDED_ERROR = "superseded_by_newer_request"

# --- 3. Logic thresholds and timing (ms/px) ---
# Hotkey re-registration debounce delay
RELOAD_TIMER_MS = 300
# OCR engine identifier.
OCR_ENGINE_PPOCR = "ppocr"

# Default OCR result text font size (px)
DEFAULT_OCR_FONT_SIZE = 16
# Small topmost audit delay in debug mode
DEBUG_TOPMOST_DELAY_MS = 120

# Pixel threshold treated as click (not drag)
CAPTURE_CLICK_THRESHOLD_PX = 8
# Minimum valid selection size
CAPTURE_SELECTION_MIN_PX = 10
# RGBA color for screenshot overlay mask
CAPTURE_OVERLAY_RGBA = (0, 0, 0, 80)
# Marker string used to identify the start of a new session in the log file.
SESSION_START_MARKER = "Logging initialized. Level:"

# --- 4. Floating Thumbnail constants ---
THUMBNAIL_WIDTH = 240
THUMBNAIL_HEIGHT = 150
THUMBNAIL_MARGIN = 20
THUMBNAIL_DISPLAY_MS = 5000
THUMBNAIL_ANIM_MS = 300
THUMBNAIL_CORNER_RADIUS = 12
THUMBNAIL_DRAG_OPACITY = 0.6
THUMBNAIL_DRAG_SCALE = 0.9
