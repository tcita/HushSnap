"""
HushSnap 配置管理模块
负责处理应用程序的配置读取、写入、路径解析、多语言支持以及单实例检测。
"""

import json
import os
import sys
import ctypes
from ctypes import wintypes
from pathlib import Path

from PyQt6 import QtCore

from .constants import (
    APP_CONFIG_FILENAME,
    DEFAULT_HOTKEY,
    INSTALLER_LANG_FILENAME,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    SINGLE_INSTANCE_MUTEX,
)
from .translations import (
    UI_LANG_AUTO,
    UI_LANG_EN,
    UI_LANG_ENV,
    UI_LANG_ZH,
    UI_TEXT,
)

# Windows API 封装，用于实现单实例互斥锁
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_create_mutex = _kernel32.CreateMutexW
_create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_create_mutex.restype = wintypes.HANDLE
_close_handle = _kernel32.CloseHandle
_close_handle.argtypes = (wintypes.HANDLE,)
_close_handle.restype = wintypes.BOOL
_ERROR_ALREADY_EXISTS = 183


# --- 路径常量定义 ---
_is_frozen = getattr(sys, "frozen", False)
# 程序的安装目录 (只读资源如图标所在处)
APP_DIR = Path(sys.executable).resolve().parent if _is_frozen else Path(__file__).resolve().parent.parent

def get_user_data_dir():
    """
    获取用户可写的数据目录 (%LOCALAPPDATA%\\HushSnap)
    
    Returns:
        Path: 用户数据目录的 Path 对象。
    """
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        path = Path(local_app_data) / "HushSnap"
    else:
        # 如果获取不到 LOCALAPPDATA，退避方案使用家目录下的隐藏文件夹
        path = Path.home() / ".hushsnap"

    # 确保目录存在，避免后续读写报错
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        # 如果创建失败（如权限问题），退避到系统临时目录
        import tempfile
        path = Path(tempfile.gettempdir()) / "HushSnap"
        path.mkdir(parents=True, exist_ok=True)
    return path

# 配置文件路径迁移到用户数据目录，避免安装目录的写权限问题
CONFIG_PATH = get_user_data_dir() / APP_CONFIG_FILENAME
# 资源目录 (如果是 PyInstaller 模式，则从 _MEIPASS 临时目录读取资源，否则就是 APP_DIR)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)) if _is_frozen else APP_DIR


def get_app_dir():
    """获取程序安装目录"""
    return APP_DIR


def get_resource_dir():
    """获取资源文件目录"""
    return RESOURCE_DIR


def get_config_path():
    """获取配置文件绝对路径"""
    return CONFIG_PATH


def _hotkey_warning_note():
    """返回配置文件中显示的热键提示说明"""
    return "Note: You can edit hotkey manually (including single-key). Some keys may conflict with system/apps; use at your own discretion."


def _ensure_default_config_exists(config_path):
    """
    如果配置文件不存在，则创建一个包含默认设置的初始文件。
    
    Args:
        config_path (Path): 配置文件路径。
    """
    if config_path.exists():
        return
    try:
        config_data = {
            "hotkey": DEFAULT_HOTKEY,
            "language": UI_LANG_AUTO,
            "_hotkey_note": _hotkey_warning_note(),
        }
        config_path.write_text(
            json.dumps(config_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _parse_virtual_key(token):
    """
    将热键文本中的按键部分解析为 Windows 虚拟键码。
    
    Args:
        token (str): 按键名称（如 'A', 'F1', 'ESC'）。
        
    Returns:
        int: 虚拟键码，如果不支持则返回 None。
    """
    normalized_token = token.strip().upper()
    # 单个字符按键 (A-Z, 0-9)
    if len(normalized_token) == 1 and "A" <= normalized_token <= "Z":
        return ord(normalized_token)
    if len(normalized_token) == 1 and "0" <= normalized_token <= "9":
        return ord(normalized_token)
    # 功能键 (F1-F24)
    if normalized_token.startswith("F") and normalized_token[1:].isdigit():
        function_key_index = int(normalized_token[1:])
        if 1 <= function_key_index <= 24:
            return 0x6F + function_key_index

    # 特殊命名按键映射
    named_key_map = {
        "ESC": 0x1B,
        "ESCAPE": 0x1B,
        "TAB": 0x09,
        "ENTER": 0x0D,
        "RETURN": 0x0D,
        "SPACE": 0x20,
        "LEFT": 0x25,
        "UP": 0x26,
        "RIGHT": 0x27,
        "DOWN": 0x28,
    }
    return named_key_map.get(normalized_token)


def parse_hotkey(hotkey_text):
    """
    解析热键字符串（如 'Ctrl+Alt+A'）为修饰符掩码和虚拟键码。
    
    Args:
        hotkey_text (str): 热键文本。
        
    Returns:
        tuple: (modifier_mask, virtual_key, canonical_hotkey_text)
        
    Raises:
        ValueError: 如果热键格式不正确或包含不支持的按键。
    """
    hotkey_parts = [part.strip() for part in hotkey_text.split("+") if part.strip()]
    if len(hotkey_parts) < 1:
        raise ValueError("Hotkey must include at least one key.")

    if len(hotkey_parts) == 1:
        modifier_tokens = []
        key_token = hotkey_parts[0]
    else:
        modifier_tokens = hotkey_parts[:-1]
        key_token = hotkey_parts[-1]

    # 解析修饰符 (Ctrl, Alt, Shift, Win)
    modifier_mask = 0
    for raw_modifier in modifier_tokens:
        normalized_modifier = raw_modifier.lower()
        if normalized_modifier == "alt":
            modifier_mask |= MOD_ALT
        elif normalized_modifier in ("ctrl", "control"):
            modifier_mask |= MOD_CONTROL
        elif normalized_modifier == "shift":
            modifier_mask |= MOD_SHIFT
        elif normalized_modifier in ("win", "windows"):
            modifier_mask |= MOD_WIN
        else:
            raise ValueError(f"Unknown modifier: {raw_modifier}")

    # 解析主键
    virtual_key = _parse_virtual_key(key_token)
    if virtual_key is None:
        raise ValueError(f"Unsupported key: {key_token}")

    # 构建标准化的热键字符串显示
    canonical_modifiers = []
    if modifier_mask & MOD_CONTROL:
        canonical_modifiers.append("Ctrl")
    if modifier_mask & MOD_ALT:
        canonical_modifiers.append("Alt")
    if modifier_mask & MOD_SHIFT:
        canonical_modifiers.append("Shift")
    if modifier_mask & MOD_WIN:
        canonical_modifiers.append("Win")

    if canonical_modifiers:
        canonical_hotkey = "+".join(canonical_modifiers + [key_token.upper()])
    else:
        canonical_hotkey = key_token.upper()
    return modifier_mask, virtual_key, canonical_hotkey


def _load_config_data(config_path):
    """从磁盘加载 JSON 配置数据"""
    try:
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(config_data, dict):
            return config_data
    except Exception:
        pass
    return {}


def _write_config_data(config_path, config_data):
    """将配置数据写入磁盘 JSON 文件"""
    config_path.write_text(
        json.dumps(config_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _ensure_hotkey_note_field(config_path):
    """确保配置文件中存在热键说明字段，以便用户手动编辑时看到提示"""
    config_data = _load_config_data(config_path)
    note = _hotkey_warning_note()
    if config_data.get("_hotkey_note") == note:
        return
    config_data["_hotkey_note"] = note
    _write_config_data(config_path, config_data)


def read_hotkey_text_from_config(config_path):
    """从配置文件中读取热键文本串"""
    config_data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config_data, dict):
        raise ValueError("Config must be a JSON object.")
    hotkey_value = config_data.get("hotkey")
    if not isinstance(hotkey_value, str) or not hotkey_value.strip():
        raise ValueError("hotkey must be a non-empty string.")
    return hotkey_value.strip()


def update_hotkey_in_config(config_path, hotkey_text):
    """更新并保存新的热键到配置文件"""
    config_data = _load_config_data(config_path)
    config_data["hotkey"] = hotkey_text

    language_value = config_data.get("language")
    if not isinstance(language_value, str) or not language_value.strip():
        config_data["language"] = UI_LANG_AUTO
    config_data["_hotkey_note"] = _hotkey_warning_note()

    _write_config_data(config_path, config_data)


def load_hotkey_setting():
    """
    加载热键设置的入口函数，处理文件初始化和容错逻辑。
    
    Returns:
        tuple: (modifier_mask, virtual_key, canonical_hotkey, config_path)
    """
    config_path = get_config_path()
    _ensure_default_config_exists(config_path)
    _ensure_hotkey_note_field(config_path)

    try:
        modifier_mask, virtual_key, canonical_hotkey = parse_hotkey(
            read_hotkey_text_from_config(config_path)
        )
        return modifier_mask, virtual_key, canonical_hotkey, config_path
    except Exception:
        # 如果解析失败，返回系统默认热键作为兜底
        modifier_mask, virtual_key, canonical_hotkey = parse_hotkey(DEFAULT_HOTKEY)
        return modifier_mask, virtual_key, canonical_hotkey, config_path


def _read_ui_lang_from_config(config_path):
    """从配置文件读取语言设置"""
    try:
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config_data, dict):
            return UI_LANG_AUTO
        configured_language = config_data.get("language", UI_LANG_AUTO)
        if isinstance(configured_language, str):
            normalized_language = configured_language.strip().lower()
            if normalized_language in {UI_LANG_AUTO, UI_LANG_EN, UI_LANG_ZH}:
                return normalized_language
    except Exception:
        pass
    return UI_LANG_AUTO


def _read_ui_lang_from_installer_hint(config_path):
    """
    读取安装程序留下的语言提示文件。
    用于在首次运行时，跟随用户在安装界面选择的语言。
    """
    hint_path = config_path.parent / INSTALLER_LANG_FILENAME
    try:
        hint_value = hint_path.read_text(encoding="utf-8").strip().lower()
    except Exception:
        return None

    if hint_value in {UI_LANG_EN, UI_LANG_ZH}:
        return hint_value
    if hint_value.startswith("zh") or "chinese" in hint_value:
        return UI_LANG_ZH
    if hint_value.startswith("en"):
        return UI_LANG_EN
    return None


def resolve_ui_lang(config_path):
    """
    解析最终应当显示的 UI 语言。
    优先级：环境变量 > 配置文件 > 安装程序提示 > 系统区域设置。
    """
    env_language = os.environ.get(UI_LANG_ENV, "").strip().lower()
    if env_language in {UI_LANG_EN, UI_LANG_ZH}:
        return env_language

    config_language = _read_ui_lang_from_config(config_path)
    if config_language in {UI_LANG_EN, UI_LANG_ZH}:
        return config_language

    installer_hint_language = _read_ui_lang_from_installer_hint(config_path)
    if installer_hint_language in {UI_LANG_EN, UI_LANG_ZH}:
        return installer_hint_language

    # 最终根据系统 Locale 自动判定
    locale_name = QtCore.QLocale.system().name().lower()
    return UI_LANG_ZH if locale_name.startswith("zh") else UI_LANG_EN


def ui_text(lang, key, **kwargs):
    """
    根据语言代码和键值获取对应的 UI 文本。
    
    Args:
        lang (str): 语言代码 (en/zh)。
        key (str): 文本键名。
        **kwargs: 用于格式化字符串的参数。
        
    Returns:
        str: 翻译后的文本。
    """
    lang_table = UI_TEXT.get(lang, UI_TEXT[UI_LANG_EN])
    text_template = lang_table.get(key, UI_TEXT[UI_LANG_EN].get(key, key))
    return text_template.format(**kwargs)


def is_already_running():
    """
    通过 Windows 命名互斥锁检测程序是否已经在运行。
    用于实现单实例启动限制。
    
    Returns:
        handle: 互斥锁句柄（如果成功创建且唯一），否则返回 None。
    """
    mutex_name = SINGLE_INSTANCE_MUTEX
    handle = _create_mutex(None, False, mutex_name)
    if not handle:
        return None

    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        _close_handle(handle)
        return None

    return handle
