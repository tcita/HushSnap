"""核心业务常量与系统默认值。"""

# --- 1. Windows 原生热键常量 ---
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
DEFAULT_HOTKEY = "Alt+Q"

# --- 2. 文件与路径名 ---
APP_CONFIG_FILENAME = "hushsnap_config.json"
INSTALLER_LANG_FILENAME = "hushsnap_installer_lang.txt"
CAPTURE_DEBUG_LOG_FILENAME = "hushsnap_capture_debug.log"
APP_ICON_FILENAME = "camera.ico"
UNINSTALLER_GLOB = "unins*.exe"
SINGLE_INSTANCE_MUTEX = "Local\\hushsnap.SingleInstance"

# --- 3. 核心业务阈值与时间 ---
RELOAD_TIMER_MS = 300
TRAY_MSG_SHORT_MS = 2000
TRAY_MSG_MEDIUM_MS = 3000
TRAY_MSG_LONG_MS = 4000
DEBUG_TOPMOST_DELAY_MS = 120

CAPTURE_CLICK_THRESHOLD_PX = 8
CAPTURE_SELECTION_MIN_PX = 10
CAPTURE_OVERLAY_RGBA = (0, 0, 0, 80)
CAPTURE_LOG_TS_FMT = "%Y-%m-%d %H:%M:%S"
