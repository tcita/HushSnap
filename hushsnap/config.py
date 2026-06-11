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
    DEFAULT_OCR_FONT_SIZE,

    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    OCR_ENGINE_PPOCR,
    THUMBNAIL_DISPLAY_MS,
)
from .translations import (
    UI_LANG_AUTO,
    UI_LANG_EN,
    UI_LANG_ZH,
    UI_LANG_ZH_TW,
    UI_LANG_JA,
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

# --- Environment Isolation ---
_is_frozen = getattr(sys, "frozen", False)

def get_app_id() -> str:
    """Get the application identifier for AppUserModelID and registration."""
    return "HushSnap" if _is_frozen else "HushSnap_Dev"

def get_mutex_name() -> str:
    """Get the unique mutex name for single-instance detection."""
    suffix = "" if _is_frozen else ".Dev"
    return f"Local\\hushsnap.SingleInstance{suffix}"

def get_startup_reg_name() -> str:
    """Get the registry key name for startup execution."""
    return "HushSnap" if _is_frozen else "HushSnap_Dev"

# ── Config defaults (single source of truth for new-key migration) ────
_CONFIG_DEFAULTS = {
    "hotkey": DEFAULT_HOTKEY,
    "language": UI_LANG_AUTO,
    "debug": not _is_frozen,
    "copy_image_to_clipboard": True,
    "auto_copy_ocr_result": True,
    "thumbnail_display_time": THUMBNAIL_DISPLAY_MS,
}


def is_running_as_package() -> bool:
    """Check if the application is running as a packaged MSIX app."""
    try:
        length = ctypes.c_uint32(0)
        # Call once to get required length, will fail with ERROR_INSUFFICIENT_BUFFER if packaged,
        # or APPMODEL_ERROR_NO_PACKAGE if unpackaged.
        result = _kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
        # 15700 is APPMODEL_ERROR_NO_PACKAGE
        return result != 15700
    except (AttributeError, Exception):
        return False


def get_package_family_name() -> str:
    """Get the MSIX package family name."""
    try:
        length = ctypes.c_uint32(0)
        _kernel32.GetCurrentPackageFamilyName(ctypes.byref(length), None)
        if length.value > 0:
            buf = ctypes.create_unicode_buffer(length.value)
            if _kernel32.GetCurrentPackageFamilyName(ctypes.byref(length), buf) == 0:
                return buf.value
    except Exception:
        pass
    return ""


def resolve_physical_path(path: Path) -> Path:
    """
    Resolves any path (including virtualized paths inside MSIX sandbox)
    to its actual physical path on disk so that external processes (like explorer.exe)
    can access it.
    """
    try:
        resolved = path.resolve()
        # Ensure the directory physically exists so explorer.exe can open it
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved
    except Exception as e:
        logger.warning(f"Error resolving physical path: {e}")
    return path


# --- Path constants ---
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


def _ensure_default_config_exists(config_path):
    """
    Ensure the config file exists and contains every key declared in
    ``_CONFIG_DEFAULTS``.

    * First run: creates the file with all defaults.
    * Subsequent runs / upgrades: merges in any keys that were added in a
      newer version, without touching values the user has already changed.
    """
    try:
        if config_path.exists():
            # Existing config — fill in any keys that are missing (e.g. after
            # an upgrade that introduced new settings).
            config_data = _load_config_data(config_path)
            missing = {
                k: v for k, v in _CONFIG_DEFAULTS.items() if k not in config_data
            }
            if missing:
                config_data.update(missing)
                _write_config_data(config_path, config_data)
                logger.debug(
                    "Config migrated — added keys: %s", list(missing.keys())
                )
            return

        # Fresh install — write the full defaults set.
        _write_config_data(config_path, dict(_CONFIG_DEFAULTS))
    except Exception as e:
        logger.debug(
            "Failed to ensure default config exists at %s: %s", config_path, e
        )


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
    preferred_order = ["hotkey", "language"]
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
    engine = _normalize_ocr_engine(state_data.get("ocr_engine")) or OCR_ENGINE_PPOCR
    font_size = state_data.get("ocr_font_size", DEFAULT_OCR_FONT_SIZE)
    if not isinstance(font_size, int):
        font_size = DEFAULT_OCR_FONT_SIZE
    pinned = state_data.get("ocr_pinned", False)
    if not isinstance(pinned, bool):
        pinned = False
    lines = [
        f'ocr_engine = "{engine}"',
        f'ocr_font_size = {font_size}',
        f'ocr_pinned = {str(pinned).lower()}',
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
        _write_state_data({"ocr_engine": OCR_ENGINE_PPOCR, "ocr_font_size": DEFAULT_OCR_FONT_SIZE, "ocr_pinned": False}, state_path)
    except Exception as e:
        logger.debug(f"Failed to ensure default state exists at {state_path}: {e}")


def _migrate_ocr_from_config(state_data, config_path):
    """One-shot: pull ocr_engine from old config TOML into state dict."""
    config_data = _load_config_data(config_path)
    if not config_data:
        return state_data
    migrated = False
    if "ocr_engine" not in state_data:
        engine = _normalize_ocr_engine(config_data.get("ocr_engine"))
        if engine:
            state_data["ocr_engine"] = engine
            migrated = True
    if migrated:
        try:
            _write_state_data(state_data)
            logger.debug("Migrated OCR settings from config to state file")
        except Exception:
            pass
    return state_data


def get_debug_enabled(config_path=None):
    """Read the debug flag from config.

    When true, it enables DEBUG-level logging and saves preprocessed OCR images
    to the user data directory for troubleshooting. Set ``debug = true`` in
    ``hushsnap_config.toml`` to activate.
    """
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    return bool(config_data.get("debug", not _is_frozen))


def get_copy_image_to_clipboard(config_path=None):
    """Read 'copy_image_to_clipboard' from config (default True)."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    return bool(config_data.get("copy_image_to_clipboard", True))


def update_copy_image_to_clipboard(enabled, config_path=None):
    """Update and persist 'copy_image_to_clipboard' in config."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    config_data["copy_image_to_clipboard"] = bool(enabled)
    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update copy_image_to_clipboard: {e}")


def get_auto_copy_ocr_result(config_path=None):
    """Read 'auto_copy_ocr_result' from config (default True)."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    return bool(config_data.get("auto_copy_ocr_result", True))


def update_auto_copy_ocr_result(enabled, config_path=None):
    """Update and persist 'auto_copy_ocr_result' in config."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    config_data["auto_copy_ocr_result"] = bool(enabled)
    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update auto_copy_ocr_result: {e}")


def get_last_save_directory(config_path=None):
    """Read 'last_save_directory' from config (default user's Desktop)."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    raw = config_data.get("last_save_directory")
    if isinstance(raw, str) and raw.strip():
        path = Path(raw.strip())
        if path.is_dir():
            return str(path)
    # Fallback: Desktop
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        return str(desktop)
    return str(Path.home())


def update_last_save_directory(directory, config_path=None):
    """Persist the last-used save directory to config."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    config_data["last_save_directory"] = str(Path(directory))
    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update last_save_directory: {e}")


def get_thumbnail_display_time(config_path=None):
    """Read 'thumbnail_display_time' from config (default 10000)."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    return int(config_data.get("thumbnail_display_time", 10000))


def update_thumbnail_display_time(ms, config_path=None):
    """Update and persist 'thumbnail_display_time' in config."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    config_data["thumbnail_display_time"] = int(ms)
    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update thumbnail_display_time: {e}")


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
    return OCR_ENGINE_PPOCR


def update_ocr_engine(engine, state_path=None):
    """Persist OCR engine to state file."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    try:
        state_data = _load_state_data(state_path)
        state_data["ocr_engine"] = _normalize_ocr_engine(engine) or OCR_ENGINE_PPOCR
        _write_state_data(state_data, state_path)
    except Exception as e:
        logger.error(f"Failed to update OCR engine in state: {e}")


def get_ocr_font_size(state_path=None):
    """Read OCR text font size from state file (default 16)."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    state_data = _load_state_data(state_path)
    font_size = state_data.get("ocr_font_size", DEFAULT_OCR_FONT_SIZE)
    if isinstance(font_size, int) and 8 <= font_size <= 48:
        return font_size
    return DEFAULT_OCR_FONT_SIZE


def update_ocr_font_size(font_size, state_path=None):
    """Persist OCR text font size to state file."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    try:
        state_data = _load_state_data(state_path)
        state_data["ocr_font_size"] = int(font_size)
        _write_state_data(state_data, state_path)
    except Exception as e:
        logger.error(f"Failed to update OCR font size in state: {e}")


def get_ocr_pinned(state_path=None):
    """Read OCR popup pin state from state file (default False)."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    state_data = _load_state_data(state_path)
    pinned = state_data.get("ocr_pinned", False)
    if isinstance(pinned, bool):
        return pinned
    # TOML parses "false"/"true" as bool already; handle string edge cases
    if isinstance(pinned, str):
        return pinned.strip().lower() == "true"
    return False


def update_ocr_pinned(pinned, state_path=None):
    """Persist OCR popup pin state to state file."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    try:
        state_data = _load_state_data(state_path)
        state_data["ocr_pinned"] = bool(pinned)
        _write_state_data(state_data, state_path)
    except Exception as e:
        logger.error(f"Failed to update OCR pin state in state: {e}")


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
    Normalize incoming language values to supported language codes.

    Accepts common BCP 47 forms (e.g. en-US, zh-CN, zh-Hant) and maps them to:
    - 'en' for English
    - 'zh' for Simplified Chinese
    - 'zh-TW' for Traditional Chinese
    - 'auto' only when allow_auto=True
    """
    if not isinstance(raw_value, str):
        return None

    normalized = raw_value.strip().lower().replace("_", "-")
    if not normalized:
        return None

    if allow_auto and normalized == UI_LANG_AUTO:
        return UI_LANG_AUTO

    # Traditional Chinese variants
    if normalized in ("zh-tw", "zh-hk", "zh-mo", "zh-hant"):
        return UI_LANG_ZH_TW

    # Simplified Chinese and other zh-* fall back to zh
    if normalized.startswith("zh"):
        return UI_LANG_ZH

    if normalized.startswith("en"):
        return UI_LANG_EN

    if normalized in ("ja", "ja-jp"):
        return UI_LANG_JA

    return None


def _normalize_ocr_engine(raw_value):
    if not isinstance(raw_value, str):
        return None

    normalized = raw_value.strip().lower().replace("-", "").replace("_", "")
    if normalized in {"ppocr"}:
        return OCR_ENGINE_PPOCR
    return None


def _get_system_ui_language():
    """Get the user's default system UI language code ('zh', 'zh-TW', or 'en')."""
    try:
        # GetUserDefaultUILanguage returns the LCID (Language Identifier)
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        primary_lang = lcid & 0x3ff  # Primary language ID is the lower 10 bits
        sub_lang = (lcid >> 10) & 0x3f
        if primary_lang == 0x04:  # LANG_CHINESE
            if sub_lang == 0x01:  # SUBLANG_CHINESE_TRADITIONAL (zh-TW)
                return UI_LANG_ZH_TW
            return UI_LANG_ZH
        if primary_lang == 0x11:  # LANG_JAPANESE
            return UI_LANG_JA
    except Exception as e:
        logger.debug(f"Failed to get system UI language via GetUserDefaultUILanguage: {e}")

    # Fallback to locale or environment variables
    try:
        import locale
        lang, _ = locale.getdefaultlocale()
        if lang:
            lang = lang.lower()
            if lang in ("zh-tw", "zh-hk", "zh-mo", "zh-hant") or "taiwan" in lang:
                return UI_LANG_ZH_TW
            if "zh" in lang or "chinese" in lang:
                return UI_LANG_ZH
            if "ja" in lang or "japanese" in lang or "japan" in lang:
                return UI_LANG_JA
    except Exception as e:
        logger.debug(f"Failed to get system language via locale: {e}")
        
    return UI_LANG_EN


def resolve_ui_lang(config_path):
    """
    Resolve the final UI language.
    Priority: config file > system default > English fallback.
    """
    config_language = _read_ui_lang_from_config(config_path)
    if config_language in SUPPORTED_LANGUAGES:
        return config_language

    system_language = _get_system_ui_language()
    if system_language in SUPPORTED_LANGUAGES:
        return system_language

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
    mutex_name = get_mutex_name()
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


def release_instance_lock(handle):
    """
    Release the single-instance mutex handle.
    Used during application restart to ensure the new process can start.
    """
    if handle:
        _close_handle(handle)
