"""
HushSnap configuration management module.
Handles config read/write, path resolution, i18n, and single-instance detection.
"""

import os
import sys
import ctypes
import logging
import tomllib
from ctypes import wintypes
from pathlib import Path

from .constants import (
    APP_CONFIG_FILENAME,
    APP_STATE_FILENAME,
    DEFAULT_HOTKEY,
    DEFAULT_OCR_HOTKEY,
    INSTALLER_LANG_FILENAME,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    OCR_ENGINE_RAPID,
    OCR_ENGINE_WINDOWS,
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
# Internal state file (engine, language) — persisted but not user-editable.
STATE_PATH = get_user_data_dir() / APP_STATE_FILENAME
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


def get_state_path():
    """Get the absolute state file path (internal persistence)."""
    return STATE_PATH


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
            "ocr_hotkey": DEFAULT_OCR_HOTKEY,
            "language": UI_LANG_AUTO,
        }
        _write_config_data(config_path, config_data)
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
    """Load TOML config data from disk."""
    try:
        config_data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(config_data, dict):
            return config_data
    except Exception as e:
        logger.debug(f"Failed to load config data from {config_path}: {e}")
    return {}


def _format_toml_value(value):
    """Format a Python value into a TOML-compatible string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    # String formatting with basic escaping
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _serialize_config_data(config_data):
    """
    Serialize config dictionary into a valid TOML document.
    Iterates over all keys to preserve user-added or future fields.
    """
    lines = [
        "# HushSnap Configuration File",
        "# If you modify this manually, ensure the syntax is valid TOML.",
        ""
    ]

    # Define preferred order for core settings for better readability
    preferred_order = ["hotkey", "ocr_hotkey", "language"]
    processed_keys = set()

    # 1. Write preferred top-level keys first
    for key in preferred_order:
        if key in config_data and not isinstance(config_data[key], dict):
            lines.append(f"{key} = {_format_toml_value(config_data[key])}")
            processed_keys.add(key)

    # 2. Write remaining top-level keys (alphabetical)
    for key in sorted(config_data.keys()):
        if key not in processed_keys and not isinstance(config_data[key], dict):
            lines.append(f"{key} = {_format_toml_value(config_data[key])}")
            processed_keys.add(key)

    lines.append("")

    # 3. Write nested tables (dictionaries)
    for key in sorted(config_data.keys()):
        val = config_data[key]
        if isinstance(val, dict):
            lines.append(f"[{key}]")
            for sub_key, sub_val in sorted(val.items()):
                lines.append(f"{sub_key} = {_format_toml_value(sub_val)}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def get_ocr_preprocess_settings_from_config(config_path):
    """Read OCR preprocess settings from config and return as a dict."""
    try:
        config_data = _load_config_data(config_path)
        preprocess = config_data.get("ocr_preprocess")
        if isinstance(preprocess, dict):
            return preprocess
    except Exception as e:
        logger.debug(f"Failed to read OCR preprocess settings from config: {e}")
    return {}


def _write_config_data(config_path, config_data):
    """Write config data to disk as TOML."""
    config_path.write_text(_serialize_config_data(config_data), encoding="utf-8")


def read_hotkey_text_from_config(config_path):
    """Read hotkey text from config file."""
    config_data = _load_config_data(config_path)
    if not isinstance(config_data, dict):
        raise ValueError("Config must be a TOML table.")
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

    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update hotkey in config: {e}")


# ── Internal state file (OCR engine + language persistence) ──────────

def _load_state_data(state_path=None):
    """Load TOML state data from disk."""
    if state_path is None:
        state_path = STATE_PATH
    try:
        data = tomllib.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.debug(f"Failed to load state data from {state_path}: {e}")
    return {}


def _write_state_data(state_data, state_path=None):
    """Write state data to disk as a minimal TOML file (no comments, not user-editable)."""
    if state_path is None:
        state_path = STATE_PATH
    engine = _normalize_ocr_engine(state_data.get("ocr_engine")) or OCR_ENGINE_RAPID
    lang = _normalize_ocr_language_tag(state_data.get("ocr_language")) or ""
    lines = [
        f'ocr_engine = "{engine}"',
        f'ocr_language = "{lang}"',
        "",
    ]
    state_path.write_text("\n".join(lines), encoding="utf-8")


def _ensure_default_state_exists(state_path=None):
    """Create state file with defaults if it does not exist."""
    if state_path is None:
        state_path = STATE_PATH
    if state_path.exists():
        return
    try:
        _write_state_data({"ocr_engine": OCR_ENGINE_RAPID, "ocr_language": ""}, state_path)
    except Exception as e:
        logger.debug(f"Failed to ensure default state exists at {state_path}: {e}")


def _migrate_ocr_from_config(state_data, config_path):
    """One-shot: pull ocr_engine / ocr_language from old config TOML into state dict."""
    config_data = _load_config_data(config_path)
    if not config_data:
        return state_data
    migrated = False
    if "ocr_engine" not in state_data:
        engine = _normalize_ocr_engine(config_data.get("ocr_engine"))
        if engine:
            state_data["ocr_engine"] = engine
            migrated = True
    if "ocr_language" not in state_data:
        lang = _normalize_ocr_language_tag(config_data.get("ocr_language"))
        if lang:
            state_data["ocr_language"] = lang
            migrated = True
    if migrated:
        try:
            _write_state_data(state_data)
            logger.debug("Migrated OCR settings from config to state file")
        except Exception:
            pass
    return state_data


def get_ocr_lang(state_path=None, config_path=None):
    """Read OCR language from state file, with migration fallback from config."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    state_data = _load_state_data(state_path)
    ocr_language = state_data.get("ocr_language")
    normalized = _normalize_ocr_language_tag(ocr_language)
    if normalized:
        return normalized
    # Migration: try old config location
    state_data = _migrate_ocr_from_config(state_data, config_path or get_config_path())
    ocr_language = state_data.get("ocr_language")
    normalized = _normalize_ocr_language_tag(ocr_language)
    if normalized:
        return normalized
    return _resolve_default_ocr_language(config_path or get_config_path())


def update_ocr_lang(ocr_lang, state_path=None):
    """Persist OCR language to state file."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    try:
        state_data = _load_state_data(state_path)
        state_data["ocr_language"] = _normalize_ocr_language_tag(ocr_lang) or ocr_lang
        _write_state_data(state_data, state_path)
    except Exception as e:
        logger.error(f"Failed to update OCR language in state: {e}")


def get_ocr_engine(state_path=None, config_path=None):
    """Read OCR engine from state file, with migration fallback from config."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    state_data = _load_state_data(state_path)
    engine = _normalize_ocr_engine(state_data.get("ocr_engine"))
    if engine:
        return engine
    # Migration: try old config location
    state_data = _migrate_ocr_from_config(state_data, config_path or get_config_path())
    engine = _normalize_ocr_engine(state_data.get("ocr_engine"))
    if engine:
        return engine
    return OCR_ENGINE_RAPID


def update_ocr_engine(engine, state_path=None):
    """Persist OCR engine to state file."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    try:
        state_data = _load_state_data(state_path)
        state_data["ocr_engine"] = _normalize_ocr_engine(engine) or OCR_ENGINE_RAPID
        _write_state_data(state_data, state_path)
    except Exception as e:
        logger.error(f"Failed to update OCR engine in state: {e}")


def load_hotkey_setting():
    """
    Entry point for loading hotkey settings, with initialization and fault tolerance.
    
    Returns:
        tuple: (modifier_mask, virtual_key, canonical_hotkey, config_path)
    """
    config_path = get_config_path()
    _ensure_default_config_exists(config_path)

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


def read_ocr_hotkey_text_from_config(config_path):
    """Read OCR screenshot hotkey text from config file."""
    config_data = _load_config_data(config_path)
    if not isinstance(config_data, dict):
        raise ValueError("Config must be a TOML table.")
    ocr_hotkey_value = config_data.get("ocr_hotkey")
    if not isinstance(ocr_hotkey_value, str) or not ocr_hotkey_value.strip():
        return DEFAULT_OCR_HOTKEY
    return ocr_hotkey_value.strip()


def update_ocr_hotkey_in_config(config_path, hotkey_text):
    """Update and persist the OCR screenshot hotkey in the config file."""
    config_data = _load_config_data(config_path)
    config_data["ocr_hotkey"] = hotkey_text
    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update OCR hotkey in config: {e}")


def load_ocr_hotkey_setting(config_path=None):
    """Load OCR screenshot hotkey settings, with initialization and fault tolerance."""
    if config_path is None:
        config_path = get_config_path()
    _ensure_default_config_exists(config_path)
    try:
        modifier_mask, virtual_key, canonical_hotkey = parse_hotkey(
            read_ocr_hotkey_text_from_config(config_path)
        )
        return modifier_mask, virtual_key, canonical_hotkey, config_path
    except Exception as e:
        logger.warning(f"Failed to load OCR hotkey from config, falling back to default: {e}")
        modifier_mask, virtual_key, canonical_hotkey = parse_hotkey(DEFAULT_OCR_HOTKEY)
        return modifier_mask, virtual_key, canonical_hotkey, config_path


def _read_ui_lang_from_config(config_path):
    """Read UI language setting from config."""
    try:
        config_data = _load_config_data(config_path)
        if not isinstance(config_data, dict):
            return UI_LANG_AUTO
        configured_language = config_data.get("language", UI_LANG_AUTO)
        normalized_language = _normalize_ui_language_code(configured_language, allow_auto=True)
        if normalized_language:
            return normalized_language
    except Exception as e:
        logger.debug(f"Failed to read UI language from config: {e}")
    return UI_LANG_AUTO


def get_configured_ui_lang(config_path):
    """Read the configured UI language ('auto', 'en', or 'zh')."""
    return _read_ui_lang_from_config(config_path)


def update_ui_lang_in_config(config_path, language_code):
    """Update and persist the UI language in the config file."""
    config_data = _load_config_data(config_path)
    config_data["language"] = language_code
    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update UI language in config: {e}")


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


def _normalize_ocr_engine(raw_value):
    if not isinstance(raw_value, str):
        return None

    normalized = raw_value.strip().lower().replace("-", "").replace("_", "")
    if normalized in {"rapidocr", "rapid"}:
        return OCR_ENGINE_RAPID
    if normalized in {"windows", "win", "windowsocr"}:
        return OCR_ENGINE_WINDOWS
    return None


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


def _get_system_ui_language():
    """Get the user's default system UI language code ('zh' or 'en')."""
    try:
        # GetUserDefaultUILanguage returns the LCID (Language Identifier)
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        primary_lang = lcid & 0x3ff  # Primary language ID is the lower 10 bits
        if primary_lang == 0x04:  # LANG_CHINESE
            return UI_LANG_ZH
    except Exception as e:
        logger.debug(f"Failed to get system UI language via GetUserDefaultUILanguage: {e}")
    
    # Fallback to locale or environment variables
    try:
        import locale
        lang, _ = locale.getdefaultlocale()
        if lang:
            lang = lang.lower()
            if "zh" in lang or "chinese" in lang:
                return UI_LANG_ZH
    except Exception as e:
        logger.debug(f"Failed to get system language via locale: {e}")
        
    return UI_LANG_EN


def resolve_ui_lang(config_path):
    """
    Resolve the final UI language.
    Priority: config file > installer hint > system default > English fallback.
    """
    config_language = _read_ui_lang_from_config(config_path)
    if config_language in SUPPORTED_LANGUAGES:
        return config_language

    installer_hint_language = _read_ui_lang_from_installer_hint(config_path)
    if installer_hint_language in SUPPORTED_LANGUAGES:
        return installer_hint_language

    # Check system UI language (e.g. for MSIX / Store app installations without installer hint)
    system_language = _get_system_ui_language()
    if system_language in SUPPORTED_LANGUAGES:
        return system_language

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
