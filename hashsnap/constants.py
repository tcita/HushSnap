# --- 1. Windows 原生热键常量定义 ---
WM_HOTKEY = 0x0312
HOTKEY_ID = 1
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
DEFAULT_HOTKEY = "Alt+Q"

# --- 2. Logging switches (visible) ---
# User default: lightweight logs only.
LOG_MODE_LIGHT = "light"
LOG_MODE_DEBUG = "debug"
DEFAULT_LOG_MODE = LOG_MODE_LIGHT
LOG_MODE_ENV = "HASHSNAP_LOG_MODE"  # values: light | debug
DEBUG_TOPMOST_ENV = "HASHSNAP_DEBUG_TOPMOST"  # backward-compatible override

# --- 3. UI language switches ---
UI_LANG_ENV = "HASHSNAP_UI_LANG"  # values: auto | en | zh
UI_LANG_AUTO = "auto"
UI_LANG_EN = "en"
UI_LANG_ZH = "zh"
DEFAULT_UI_LANG = UI_LANG_AUTO

UI_TEXT = {
    UI_LANG_EN: {
        "error": "Error",
        "hotkey_taken": "{hotkey} is already in use.\nConfig: {config_path}",
        "uninstaller_not_found_title": "Uninstaller Not Found",
        "uninstaller_not_found_body": "No uninstaller was found in the app directory. Uninstall HashSnap from Control Panel > Programs and Features.",
        "confirm_uninstall_title": "Confirm Uninstall",
        "confirm_uninstall_body": "HashSnap uninstaller will be launched. Continue?",
        "launch_uninstall_failed": "Failed to Launch Uninstaller",
        "about_title": "About",
        "about_body": "HashSnap {version}\nCurrent hotkey: {hotkey}\nConfig: {config_path}",
        "about_info": "HashSnap runs in the system tray. Use Settings to change hotkey or uninstall.",
        "settings_title": "Settings",
        "settings_body": "Current hotkey: {hotkey}\nConfig: {config_path}",
        "settings_hotkey_label": "Hotkey",
        "settings_save_hotkey_btn": "Save Hotkey",
        "settings_open_config_btn": "Config Folder",
        "settings_hotkey_capture_placeholder": "Press shortcut...",
        "settings_hotkey_capture_invalid": "Use at least one modifier and one key.",
        "settings_hotkey_saved": "Hotkey saved: {hotkey}",
        "settings_hotkey_invalid": "Invalid hotkey: {error}",
        "settings_hotkey_save_failed": "Failed to save hotkey: {error}",
        "settings_hotkey_apply_failed": "Requested {new_hotkey}, but system kept {old_hotkey}.",
        "uninstall_btn": "Uninstall",
        "close_btn": "Close",
        "open_dir_failed": "Cannot Open Folder",
        "menu_settings": "Settings...",
        "menu_about": "About...",
        "menu_open_install_dir": "Open Install Folder",
        "menu_quit": "Quit",
        "hotkey_not_updated_title": "HashSnap Hotkey Not Updated",
        "hotkey_invalid_config": "Invalid config. Keep using {hotkey}\n{error}",
        "hotkey_enabled_title": "HashSnap Hotkey Enabled",
        "hotkey_enabled": "Enabled {hotkey}",
        "hotkey_still_occupied": "{hotkey} is still in use",
        "hotkey_updated_title": "HashSnap Hotkey Updated",
        "hotkey_updated": "{old_hotkey} -> {new_hotkey}",
        "hotkey_error_title": "HashSnap Hotkey Error",
        "hotkey_recover_failed": "New hotkey is unavailable, and old hotkey recovery failed.",
        "hotkey_kept_old": "{new_hotkey} is in use. Kept {old_hotkey}.",
    },
    UI_LANG_ZH: {
        "error": "错误",
        "hotkey_taken": "{hotkey} 热键已被占用！\n配置文件: {config_path}",
        "uninstaller_not_found_title": "未找到卸载程序",
        "uninstaller_not_found_body": "当前目录未检测到卸载程序，请在 控制面板 -> 程序和功能 中卸载 HashSnap。",
        "confirm_uninstall_title": "确认卸载",
        "confirm_uninstall_body": "将启动 HashSnap 卸载程序，是否继续？",
        "launch_uninstall_failed": "启动卸载失败",
        "about_title": "关于",
        "about_body": "HashSnap {version}\n当前热键: {hotkey}\n配置文件: {config_path}",
        "about_info": "HashSnap 常驻系统托盘。可在“设置”中修改热键或卸载。",
        "settings_title": "设置",
        "settings_body": "当前热键: {hotkey}\n配置文件: {config_path}",
        "settings_hotkey_label": "快捷键",
        "settings_save_hotkey_btn": "保存快捷键",
        "settings_open_config_btn": "配置目录",
        "settings_hotkey_capture_placeholder": "请按下快捷键...",
        "settings_hotkey_capture_invalid": "请至少包含一个修饰键和一个按键。",
        "settings_hotkey_saved": "快捷键已保存: {hotkey}",
        "settings_hotkey_invalid": "快捷键无效: {error}",
        "settings_hotkey_save_failed": "保存快捷键失败: {error}",
        "settings_hotkey_apply_failed": "尝试应用 {new_hotkey}，但系统保持为 {old_hotkey}。",
        "uninstall_btn": "卸载",
        "close_btn": "关闭",
        "open_dir_failed": "无法打开目录",
        "menu_settings": "设置...",
        "menu_about": "关于...",
        "menu_open_install_dir": "打开安装目录",
        "menu_quit": "退出",
        "hotkey_not_updated_title": "HashSnap 热键未更新",
        "hotkey_invalid_config": "配置无效，继续使用 {hotkey}\n{error}",
        "hotkey_enabled_title": "HashSnap 热键已启用",
        "hotkey_enabled": "已启用 {hotkey}",
        "hotkey_still_occupied": "{hotkey} 仍被占用",
        "hotkey_updated_title": "HashSnap 热键已更新",
        "hotkey_updated": "{old_hotkey} -> {new_hotkey}",
        "hotkey_error_title": "HashSnap 热键错误",
        "hotkey_recover_failed": "新热键不可用，且旧热键恢复失败。",
        "hotkey_kept_old": "{new_hotkey} 被占用，已保持 {old_hotkey}",
    },
}
