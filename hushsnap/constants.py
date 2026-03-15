"""
HushSnap 常量定义模块
存储全局通用的 Windows 消息 ID、热键修饰符、文件名、业务逻辑阈值等。
"""

# --- 1. Windows 原生热键相关常量 ---
# Windows 热键消息 ID
WM_HOTKEY = 0x0312
# 热键修饰符掩码 (RegisterHotKey API 使用)
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
# 默认激活热键
DEFAULT_HOTKEY = "Alt+Q"

# --- 2. 文件与系统相关常量 ---
# 配置文件名
APP_CONFIG_FILENAME = "hushsnap_config.json"
# 安装程序传递的语言提示文件名
INSTALLER_LANG_FILENAME = "hushsnap_installer_lang.txt"
# 调试日志文件名
CAPTURE_DEBUG_LOG_FILENAME = "hushsnap_capture_debug.log"
# 托盘图标文件名
APP_ICON_FILENAME = "camera.ico"
# 卸载程序查找通配符
UNINSTALLER_GLOB = "unins*.exe"
# 单实例运行检测使用的命名互斥锁
SINGLE_INSTANCE_MUTEX = "Local\\hushsnap.SingleInstance"

# --- 3. 业务逻辑阈值与时间 (毫秒/像素) ---
# 热键重新注册定时器延迟
RELOAD_TIMER_MS = 300
# 托盘消息显示时长 (短)
TRAY_MSG_SHORT_MS = 2000
# 托盘消息显示时长 (中)
TRAY_MSG_MEDIUM_MS = 3000
# 托盘消息显示时长 (长)
TRAY_MSG_LONG_MS = 4000
# 调试模式下窗口置顶的微小延迟
DEBUG_TOPMOST_DELAY_MS = 120

# 判定为点击而非拖拽的最小像素阈值
CAPTURE_CLICK_THRESHOLD_PX = 8
# 判定为有效选取区域的最小尺寸
CAPTURE_SELECTION_MIN_PX = 10
# 截图蒙版遮罩的 RGBA 颜色
CAPTURE_OVERLAY_RGBA = (0, 0, 0, 80)
# 日志时间格式
CAPTURE_LOG_TS_FMT = "%Y-%m-%d %H:%M:%S"
