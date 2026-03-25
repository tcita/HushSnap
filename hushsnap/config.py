"""
HushSnap configuration management module.
Handles config read/write, path resolution, i18n, and single-instance detection.
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

# Windows API wrappers for the single-instance mutex.
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_create_mutex = _kernel32.CreateMutexW
_create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_create_mutex.restype = wintypes.HANDLE
_close_handle = _kernel32.CloseHandle
_close_handle.argtypes = (wintypes.HANDLE,)
_close_handle.restype = wintypes.BOOL
_ERROR_ALREADY_EXISTS = 183


# --- Path constants ---
_is_frozen = getattr(sys, "frozen", False)
# Application install directory (contains read-only assets like icon files).
APP_DIR = Path(sys.executable).resolve().parent if _is_frozen else Path(__file__).resolve().parent.parent

def get_user_data_dir():
    """
    Get the user-writable data directory (%LOCALAPPDATA%\\HushSnap).
    
    Returns:
        Path: Path object for the user data directory.
    """
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        path = Path(local_app_data) / "HushSnap"
    else:
        # Fallback: use a hidden folder under home if LOCALAPPDATA is unavailable.
        path = Path.home() / ".hushsnap"

    # Ensure directory exists to avoid subsequent read/write errors.
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fallback to system temp directory if creation fails (e.g. permission issues).
        import tempfile
        path = Path(tempfile.gettempdir()) / "HushSnap"
        path.mkdir(parents=True, exist_ok=True)
    return path

# Store config in user data directory to avoid install-directory write permission issues.
CONFIG_PATH = get_user_data_dir() / APP_CONFIG_FILENAME
# Resource directory (for PyInstaller, read from _MEIPASS; otherwise APP_DIR).
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)) if _is_frozen else APP_DIR


def get_app_dir():
    """Get the application install directory."""
    return APP_DIR


def get_resource_dir():
    """Get the resource directory."""
    return RESOURCE_DIR


def get_config_path():
    """Get the absolute config file path."""
    return CONFIG_PATH


def _hotkey_warning_note():
    """Return the hotkey guidance text shown in the config file."""
    return "Note: You can edit hotkey manually (including single-key). Some keys may conflict with system/apps; use at your own discretion."


def _ensure_default_config_exists(config_path):
    """
    Create an initial config file with defaults if it does not exist.
    
    Args:
        config_path (Path): Config file path.
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
    Parse the key token in hotkey text into a Windows virtual-key code.
    
    Args:
        token (str): Key name (e.g. 'A', 'F1', 'ESC').
        
    Returns:
        int: Virtual-key code, or None if unsupported.
    """
    normalized_token = token.strip().upper()
    # Single-character keys (A-Z, 0-9)
    if len(normalized_token) == 1 and "A" <= normalized_token <= "Z":
        return ord(normalized_token)
    if len(normalized_token) == 1 and "0" <= normalized_token <= "9":
        return ord(normalized_token)
    # Function keys (F1-F24)
    if normalized_token.startswith("F") and normalized_token[1:].isdigit():
        function_key_index = int(normalized_token[1:])
        if 1 <= function_key_index <= 24:
            return 0x6F + function_key_index

    # Named special key mapping
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
    Parse hotkey string (e.g. 'Ctrl+Alt+A') into modifier mask and virtual-key code.
    
    Args:
        hotkey_text (str): Hotkey text.
        
    Returns:
        tuple: (modifier_mask, virtual_key, canonical_hotkey_text)
        
    Raises:
        ValueError: If hotkey format is invalid or contains unsupported keys.
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

    # Parse modifiers (Ctrl, Alt, Shift, Win)
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

    # Parse primary key
    virtual_key = _parse_virtual_key(key_token)
    if virtual_key is None:
        raise ValueError(f"Unsupported key: {key_token}")

    # Build canonical hotkey display string
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
    """Load JSON config data from disk."""
    try:
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(config_data, dict):
            return config_data
    except Exception:
        pass
    return {}


def _write_config_data(config_path, config_data):
    """Write config data to disk as JSON."""
    config_path.write_text(
        json.dumps(config_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _ensure_hotkey_note_field(config_path):
    """Ensure the hotkey note field exists so manual editors can see guidance."""
    config_data = _load_config_data(config_path)
    note = _hotkey_warning_note()
    if config_data.get("_hotkey_note") == note:
        return
    config_data["_hotkey_note"] = note
    _write_config_data(config_path, config_data)


def read_hotkey_text_from_config(config_path):
    """Read hotkey text from config file."""
    config_data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config_data, dict):
        raise ValueError("Config must be a JSON object.")
    hotkey_value = config_data.get("hotkey")
    if not isinstance(hotkey_value, str) or not hotkey_value.strip():
        raise ValueError("hotkey must be a non-empty string.")
    return hotkey_value.strip()


def update_hotkey_in_config(config_path, hotkey_text):
    """Update and persist the new hotkey in the config file."""
    config_data = _load_config_data(config_path)
    config_data["hotkey"] = hotkey_text

    language_value = config_data.get("language")
    if not isinstance(language_value, str) or not language_value.strip():
        config_data["language"] = UI_LANG_AUTO
    config_data["_hotkey_note"] = _hotkey_warning_note()

    _write_config_data(config_path, config_data)


def load_hotkey_setting():
    """
    Entry point for loading hotkey settings, with initialization and fault tolerance.
    
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
        # Fallback to default system hotkey if parsing fails.
        modifier_mask, virtual_key, canonical_hotkey = parse_hotkey(DEFAULT_HOTKEY)
        return modifier_mask, virtual_key, canonical_hotkey, config_path


def _read_ui_lang_from_config(config_path):
    """Read UI language setting from config."""
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
    Read language hint written by installer.
    Used on first run to follow language selected in installer UI.
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
    Resolve the final UI language.
    Priority: environment variable > config file > installer hint > system locale.
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

    # Final fallback: infer from system locale.
    locale_name = QtCore.QLocale.system().name().lower()
    return UI_LANG_ZH if locale_name.startswith("zh") else UI_LANG_EN


def ui_text(lang, key, **kwargs):
    """
    Get UI text by language code and key.
    
    Args:
        lang (str): Language code (en/zh).
        key (str): Text key.
        **kwargs: Parameters for string formatting.
        
    Returns:
        str: Translated text.
    """
    lang_table = UI_TEXT.get(lang, UI_TEXT[UI_LANG_EN])
    text_template = lang_table.get(key, UI_TEXT[UI_LANG_EN].get(key, key))
    return text_template.format(**kwargs)


def is_already_running():
    """
    Create a named mutex to ensure single-instance execution.

    Windows behavior:
    - If the mutex does not exist, CreateMutex creates it and GetLastError() == 0.
    - If the mutex already exists, CreateMutex still returns a handle but
      GetLastError() == ERROR_ALREADY_EXISTS, meaning another instance is running.

    Returns:
        handle: Unique mutex handle if this is the first instance.
        None:   If another instance is already running.
    """
    mutex_name = SINGLE_INSTANCE_MUTEX
    handle = _create_mutex(
    None,          
    False,         # bInitialOwner: Create the mutex object only, without acquiring the lock.
                   # (We need the "Mutex" resource, not immediate "Lock" ownership.)
    mutex_name     
)
    if not handle:
        return None

    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
    # The mutex already exists, which means another instance of the program is running.
    # CreateMutex() still returns a valid handle to the existing mutex, but we should not
    # use it in a single-instance check. We only needed to detect the condition.
    
    # Close the handle to avoid leaking system resources.
        _close_handle(handle)
        return None

    return handle
