"""
HushSnap configuration management module.
Handles config read/write, path resolution, i18n, and single-instance detection.
"""

import json
import os
import sys
import ctypes
import logging
from ctypes import wintypes
from pathlib import Path

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
    UI_LANG_ZH,
    SUPPORTED_LANGUAGES,
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

logger = logging.getLogger(__name__)


# --- Path constants ---
_is_frozen = getattr(sys, "frozen", False)
# Application install directory (contains read-only assets like icon files).
APP_DIR = Path(sys.executable).resolve().parent if _is_frozen else Path(__file__).resolve().parent.parent

def get_user_data_dir():
    """
    Get the user-writable data directory (%LOCALAPPDATA%\\HushSnap).
    Uses a different folder name for development runs to avoid interference.
    
    Returns:
        Path: Path object for the user data directory.
    """
    folder_name = "HushSnap" if _is_frozen else "HushSnap_Dev"
    local_app_data = os.getenv("LOCALAPPDATA")
    
    if local_app_data:
        path = Path(local_app_data) / folder_name
    else:
        # Fallback: use a hidden folder under home if LOCALAPPDATA is unavailable.
        path = Path.home() / f".{folder_name.lower()}"

    # Ensure directory exists to avoid subsequent read/write errors.
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        # Fallback to system temp directory if creation fails (e.g. permission issues).
        import tempfile
        logger.debug(f"Failed to create user data dir at {path}: {e}. Falling back to temp.")
        path = Path(tempfile.gettempdir()) / "HushSnap"
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as e2:
            logger.critical(f"Failed to create temp data dir: {e2}")
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


def _default_ocr_language_for_ui_language(ui_language):
    """Map the resolved UI language to the best default OCR language tag."""
    return "zh-CN" if ui_language == UI_LANG_ZH else "en-US"


def _resolve_default_ocr_language(config_path):
    """Resolve the default OCR language for first run or missing config fields."""
    return _default_ocr_language_for_ui_language(resolve_ui_lang(config_path))


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
            "ocr_enabled": False,
            "ocr_language": _resolve_default_ocr_language(config_path),
            "_hotkey_note": _hotkey_warning_note(),
        }
        config_path.write_text(
            json.dumps(config_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug(f"Failed to ensure default config exists at {config_path}: {e}")


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
    except Exception as e:
        logger.debug(f"Failed to load config data from {config_path}: {e}")
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
    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.debug(f"Failed to update hotkey note: {e}")


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

    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update hotkey in config: {e}")


def get_ocr_lang_from_config(config_path):
    """Read OCR language preference from config."""
    try:
        config_data = _load_config_data(config_path)
        ocr_language = config_data.get("ocr_language")
        normalized_ocr_language = _normalize_ocr_language_tag(ocr_language)
        if normalized_ocr_language:
            return normalized_ocr_language
    except Exception as e:
        logger.debug(f"Failed to read OCR language from config: {e}")
    return _resolve_default_ocr_language(config_path)


def update_ocr_lang_in_config(config_path, ocr_lang):
    """Update OCR language preference in config."""
    try:
        config_data = _load_config_data(config_path)
        config_data["ocr_language"] = _normalize_ocr_language_tag(ocr_lang) or ocr_lang
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update OCR language in config: {e}")


def get_ocr_enabled_from_config(config_path):
    """Read OCR toggle state from config."""
    try:
        config_data = _load_config_data(config_path)
        return bool(config_data.get("ocr_enabled", False))
    except Exception as e:
        logger.debug(f"Failed to read OCR enabled state from config: {e}")
        return False


def update_ocr_enabled_in_config(config_path, enabled):
    """Update OCR toggle state in config."""
    try:
        config_data = _load_config_data(config_path)
        config_data["ocr_enabled"] = bool(enabled)
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update OCR enabled state in config: {e}")


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
    except Exception as e:
        # Fallback to default system hotkey if parsing fails.
        logger.warning(f"Failed to load hotkey from config, falling back to default: {e}")
        modifier_mask, virtual_key, canonical_hotkey = parse_hotkey(DEFAULT_HOTKEY)
        return modifier_mask, virtual_key, canonical_hotkey, config_path


def _read_ui_lang_from_config(config_path):
    """Read UI language setting from config."""
    try:
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config_data, dict):
            return UI_LANG_AUTO
        configured_language = config_data.get("language", UI_LANG_AUTO)
        normalized_language = _normalize_ui_language_code(configured_language, allow_auto=True)
        if normalized_language:
            return normalized_language
    except Exception as e:
        logger.debug(f"Failed to read UI language from config: {e}")
    return UI_LANG_AUTO


def _normalize_ui_language_code(raw_value, allow_auto=False):
    """
    Normalize incoming language values to supported ISO 639-1 codes.

    Accepts common BCP 47 forms (e.g. en-US, zh-CN, zh_Hans) and maps them to:
    - 'en' for English
    - 'zh' for Chinese
    - 'auto' only when allow_auto=True
    """
    if not isinstance(raw_value, str):
        return None

    normalized = raw_value.strip().lower().replace("_", "-")
    if not normalized:
        return None

    if allow_auto and normalized == UI_LANG_AUTO:
        return UI_LANG_AUTO

    primary_subtag = normalized.split("-", 1)[0]
    if primary_subtag in SUPPORTED_LANGUAGES:
        return primary_subtag
    return None


def _normalize_ocr_language_tag(raw_value):
    """Normalize OCR language values into the app's supported OCR options."""
    if not isinstance(raw_value, str):
        return None

    normalized = raw_value.strip().replace("_", "-")
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered == "en" or lowered.startswith("en-"):
        return "en-US"
    if lowered == "zh":
        return "zh-CN"
    if lowered in ("zh-cn", "zh-sg", "zh-hans"):
        return "zh-CN"
    if lowered in ("zh-tw", "zh-hk", "zh-mo", "zh-hant"):
        return "zh-TW"
    return normalized


def _read_ui_lang_from_installer_hint(config_path):
    """
    Read language hint written by installer.
    Used on first run to follow language selected in installer UI.
    """
    hint_path = config_path.parent / INSTALLER_LANG_FILENAME
    try:
        hint_value = hint_path.read_text(encoding="utf-8").strip().lower()
    except Exception as e:
        logger.debug(f"Failed to read installer language hint: {e}")
        return None

    normalized_hint = _normalize_ui_language_code(hint_value)
    if normalized_hint in SUPPORTED_LANGUAGES:
        return normalized_hint
    if "chinese" in hint_value:
        return UI_LANG_ZH
    if "english" in hint_value:
        return UI_LANG_EN
    return None


def resolve_ui_lang(config_path):
    """
    Resolve the final UI language.
    Priority: config file > installer hint > English fallback.
    """
    config_language = _read_ui_lang_from_config(config_path)
    if config_language in SUPPORTED_LANGUAGES:
        return config_language

    installer_hint_language = _read_ui_lang_from_installer_hint(config_path)
    if installer_hint_language in SUPPORTED_LANGUAGES:
        return installer_hint_language

    # Final fallback: use English.
    return UI_LANG_EN


def ui_text(lang, key, **kwargs):
    """
    Get UI text by language code and key.
    
    Args:
        lang (str): Language code (e.g. en/zh).
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
