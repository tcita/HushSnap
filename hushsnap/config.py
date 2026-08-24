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
_APPMODEL_ERROR_NO_PACKAGE = 15700

logger = logging.getLogger(__name__)

# --- Environment Isolation ---
_is_frozen = getattr(sys, "frozen", False)


def _get_app_folder_name() -> str:
    """Return the app folder / registration name, suffixed for dev runs."""
    return "HushSnap" if _is_frozen else "HushSnap_Dev"


def get_app_id() -> str:
    """Get the application identifier for AppUserModelID and registration."""
    return _get_app_folder_name()


def get_mutex_name() -> str:
    """Get the unique mutex name for single-instance detection."""
    return "Local\\hushsnap.SingleInstance"


def get_startup_reg_name() -> str:
    """Get the registry key name for startup execution."""
    return _get_app_folder_name()

# ── Config defaults (single source of truth for new-key migration) ────
_CONFIG_DEFAULTS = {
    "hotkey": DEFAULT_HOTKEY,
    "language": UI_LANG_AUTO,
    "debug": not _is_frozen,
    "thumbnail_display_time": THUMBNAIL_DISPLAY_MS,
    "thumbnail_frame": "",
    "show_capture_dimension_label": True,
    "auto_ocr_after_capture": False,
    "hide_thumbnail": False,
}

# Schema version of the on-disk config file. Bump on any breaking change to
# an existing key's semantics (unit, enum mapping) or a forced value reset,
# and add a matching ``if v < N`` branch in ``_migrate_config`` below.
# ``config_version`` is intentionally NOT part of ``_CONFIG_DEFAULTS`` — it is
# stamped by ``_migrate_config`` so pre-version files (no field) are treated as
# v1 and run through the migration ladder, rather than being back-filled as if
# they had always been current.
_CONFIG_VERSION = 4


def is_running_as_package() -> bool:
    """Check if the application is running as a packaged MSIX app."""
    try:
        length = ctypes.c_uint32(0)
        # Call once to get required length, will fail with ERROR_INSUFFICIENT_BUFFER if packaged,
        # or APPMODEL_ERROR_NO_PACKAGE if unpackaged.
        result = _kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
        return result != _APPMODEL_ERROR_NO_PACKAGE
    except (AttributeError, Exception):
        return False


def get_current_package_family_name() -> str | None:
    """Get the current package family name if running as packaged app."""
    try:
        # Check if the API is supported on this Windows version
        if not hasattr(_kernel32, "GetCurrentPackageFamilyName"):
            return None
        length = ctypes.c_uint32(0)
        # Call once to get required length
        _kernel32.GetCurrentPackageFamilyName(ctypes.byref(length), None)
        if length.value == 0:
            return None
        buffer = ctypes.create_unicode_buffer(length.value)
        result = _kernel32.GetCurrentPackageFamilyName(ctypes.byref(length), buffer)
        if result == 0:  # ERROR_SUCCESS
            return buffer.value
    except Exception as e:
        logger.warning(f"Failed to query package family name: {e}")
    return None


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
    Get the user-writable data directory.
    If running as a packaged MSIX app, returns the package's LocalState directory.
    Otherwise, returns %LOCALAPPDATA%\\HushSnap (or fallback).
    
    Returns:
        Path: Path object for the user data directory.
    """
    family_name = get_current_package_family_name()
    local_app_data = os.getenv("LOCALAPPDATA")
    
    if family_name and local_app_data:
        # For MSIX, store everything inside the package's LocalState.
        # This directory is automatically deleted by Windows upon uninstall.
        path = Path(local_app_data) / "Packages" / family_name / "LocalState"
    else:
        folder_name = _get_app_folder_name()
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


def get_resource_dir():
    """Get the resource directory."""
    return RESOURCE_DIR


def get_config_path():
    """Get the absolute config file path."""
    return CONFIG_PATH


def get_state_path():
    """Get the absolute state file path (internal persistence)."""
    return STATE_PATH


def _migrate_config(config_data):
    """Apply version-gated breaking migrations to an existing config dict.

    Pre-version files (no ``config_version`` field) are treated as v1. Each
    ``if v < N`` block performs the v(N-1)→vN migration in place; the version
    is then stamped to current so each step runs at most once per file. Returns
    True if anything changed.
    """
    changed = False
    v = config_data.get("config_version", 1)
    # ── v1 → v2: first breaking change: thumbnail_frame bool -> ornament id ──
    # v1 -> v2: 'thumbnail_frame' changed from a bool on/off toggle to a string
    # selecting which ornament ("" = off, "vine"/... = a registered ornament).
    # Preserve the user's on/off choice.  Must run before the empty-string repair
    # pass in _ensure_default_config_exists, which would otherwise clobber the bool.
    if v < 2:
        tf = config_data.get("thumbnail_frame")
        if isinstance(tf, bool):
            config_data["thumbnail_frame"] = "vine" if tf else ""
            changed = True
            logger.debug(
                "Config migrated v1->v2: thumbnail_frame %r -> %r",
                tf, config_data["thumbnail_frame"],
            )
    # ── v2 → v3: drop 30 s thumbnail duration ──
    # The 30 s option was removed from the UI.  Existing users who had it
    # selected are migrated to the default 12 s so the dropdown resolves
    # to a valid entry.
    if v < 3:
        if config_data.get("thumbnail_display_time") == 30000:
            config_data["thumbnail_display_time"] = 12000
            changed = True
            logger.debug(
                "Config migrated v2->v3: thumbnail_display_time 30000 -> 12000"
            )
    # ── v3 → v4: new default 5 s thumbnail duration ──
    # The default was shortened from 12 s to 5 s.  Migrate users who had
    # the old default so they pick up the new one automatically.  Users
    # who prefer 12 s can reselect it in Settings.
    if v < 4:
        if config_data.get("thumbnail_display_time") == 12000:
            config_data["thumbnail_display_time"] = 5000
            changed = True
            logger.debug(
                "Config migrated v3->v4: thumbnail_display_time 12000 -> 5000"
            )
    if v != _CONFIG_VERSION or "config_version" not in config_data:
        config_data["config_version"] = _CONFIG_VERSION
        changed = True
    return changed


def _ensure_default_config_exists(config_path):
    """
    Ensure the config file exists and contains every key declared in
    ``_CONFIG_DEFAULTS``.

    * First run: creates the file with all defaults.
    * Subsequent runs / upgrades: merges in any keys that were added in a
      newer version, without touching values the user has already changed.
    * Also repairs string-typed keys whose values are empty (e.g. a user or
      bug wrote ``hotkey = ""``), replacing them with their defaults.
    """
    try:
        if config_path.exists():
            # Existing config — fill in any keys that are missing (e.g. after
            # an upgrade that introduced new settings).
            config_data = _load_config_data(config_path)
            changed = False

            missing = {
                k: v for k, v in _CONFIG_DEFAULTS.items() if k not in config_data
            }
            if missing:
                config_data.update(missing)
                changed = True
                logger.debug(
                    "Config migrated — added keys: %s", list(missing.keys())
                )

            # Apply breaking version-gated migrations BEFORE the repair pass so
            # the repair logic sees post-migration values (e.g. a bool turned
            # into a string) instead of clobbering them as "non-string / empty".
            if _migrate_config(config_data):
                changed = True

            # Repair string-typed keys that are present but empty.
            for key, default_val in _CONFIG_DEFAULTS.items():
                if key in config_data and isinstance(default_val, str):
                    current = config_data.get(key)
                    if not isinstance(current, str) or not current.strip():
                        config_data[key] = default_val
                        changed = True
                        logger.warning(
                            "Config key '%s' was empty — repaired to default.", key
                        )

            if changed:
                _write_config_data(config_path, config_data)
            return

        # Fresh install — write the full defaults set with the current schema
        # version stamped in.
        _write_config_data(config_path, {**_CONFIG_DEFAULTS, "config_version": _CONFIG_VERSION})
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
    """Load TOML config data from disk.

    Returns {} on any failure so callers' ``.get(key, default)`` falls back to
    defaults. Any failure (missing file, syntax error) is logged at WARNING
    as a single "config is unusable" signal - we don't classify the cause,
    because a manually-broken file can break in uncountable ways and guessing
    the category isn't worth the branching. Per-field bad-value handling
    (``get_*`` isinstance / int guards) covers the case where the file parses
    fine but a single value has the wrong type.
    """
    try:
        config_data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(config_data, dict):
            return config_data
    except Exception as e:
        logger.warning(f"Failed to load config data from {config_path}: {e}")
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
    preferred_order = ["config_version", "hotkey", "language"]
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
    """Load TOML state data from disk. See _load_config_data."""
    if state_path is None:
        state_path = STATE_PATH
    try:
        data = tomllib.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning(f"Failed to load state data from {state_path}: {e}")
    return {}


def _write_state_data(state_data, state_path=None):
    """Write state data to disk as a minimal TOML file.

    Preserves any extra keys (e.g. editor window geometry) present in
    state_data so new state fields can be added without losing old ones —
    the known scalar fields are normalized, the rest are passed through.
    """
    if state_path is None:
        state_path = STATE_PATH
    font_size = state_data.get("ocr_font_size", DEFAULT_OCR_FONT_SIZE)
    if not isinstance(font_size, int):
        font_size = DEFAULT_OCR_FONT_SIZE
    onboarding_shown = bool(state_data.get("onboarding_toast_shown", False))

    # Known scalar keys, written in a stable order.
    out = {
        "ocr_font_size": str(font_size),
        "onboarding_toast_shown": "true" if onboarding_shown else "false",
    }
    # Pass through any other keys the caller included (e.g. editor window
    # geometry: a TOML inline table). Skip the ones already handled above.
    for key, val in state_data.items():
        if key in out:
            continue
        if isinstance(val, dict):
            # Render as a TOML inline table: key = { a = 1, b = 2 }
            inner = ", ".join(f"{k} = {_toml_scalar(v)}" for k, v in val.items())
            out[key] = "{" + inner + "}"
        else:
            out[key] = _toml_scalar(val)

    lines = [f"{k} = {v}" for k, v in out.items()]
    lines.append("")
    state_path.write_text("\n".join(lines), encoding="utf-8")


def _toml_scalar(val):
    """Render a Python scalar as a TOML scalar string."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    # Fallback: best-effort string.
    return '"' + str(val).replace('"', '\\"') + '"'


def _ensure_default_state_exists(state_path=None):
    """Create state file with defaults if it does not exist."""
    if state_path is None:
        state_path = STATE_PATH
    if state_path.exists():
        return
    try:
        _write_state_data({"ocr_font_size": DEFAULT_OCR_FONT_SIZE}, state_path)
    except Exception as e:
        logger.debug(f"Failed to ensure default state exists at {state_path}: {e}")


def get_debug_enabled(config_path=None):
    """Read the debug flag from config.

    When true, it enables DEBUG-level logging and saves preprocessed OCR images
    to the user data directory for troubleshooting. Set ``debug = true`` in
    ``hushsnap_config.toml`` to activate.
    """
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    raw = config_data.get("debug", not _is_frozen)
    if not isinstance(raw, bool):
        logger.warning(
            "Config key 'debug' has non-boolean value %r; falling back to default %r.",
            raw, not _is_frozen,
        )
        return not _is_frozen
    return raw


def get_show_capture_dimension_label(config_path=None):
    """Read 'show_capture_dimension_label' from config (default True).

    When True, the capture overlay shows the selection size while dragging and
    the cursor position while hovering. When False, neither label is drawn —
    the selection border and handles are unaffected.
    """
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    raw = config_data.get("show_capture_dimension_label", True)
    if not isinstance(raw, bool):
        logger.warning(
            "Config key 'show_capture_dimension_label' has non-boolean value %r; falling back to default True.",
            raw,
        )
        return True
    return raw


def update_show_capture_dimension_label(enabled, config_path=None):
    """Update and persist 'show_capture_dimension_label' in config."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    config_data["show_capture_dimension_label"] = bool(enabled)
    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update show_capture_dimension_label: {e}")


def get_auto_ocr_after_capture(config_path=None):
    """Read 'auto_ocr_after_capture' from config (default False).

    When True, OCR runs silently in the background after every screenshot as
    a prefetch — the result fills an in-memory cache so a later thumbnail
    click shows the text faster.  It does NOT write to the clipboard and
    shows no toast; the screenshot image remains the sole clipboard content.
    """
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    raw = config_data.get("auto_ocr_after_capture", False)
    if not isinstance(raw, bool):
        logger.warning(
            "Config key 'auto_ocr_after_capture' has non-boolean value %r "
            "— falling back to default False.",
            raw,
        )
        return False
    return raw


def update_auto_ocr_after_capture(enabled, config_path=None):
    """Update and persist 'auto_ocr_after_capture' in config."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    config_data["auto_ocr_after_capture"] = bool(enabled)
    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update auto_ocr_after_capture: {e}")


def get_hide_thumbnail(config_path=None):
    """Read 'hide_thumbnail' from config (default False).

    When True, the floating thumbnail card is suppressed entirely after every
    capture — no preview, no action pill (edit / pin / close), no save-to-desktop
    via the thumbnail. Clipboard and OCR still work normally.
    """
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    raw = config_data.get("hide_thumbnail", False)
    if not isinstance(raw, bool):
        logger.warning(
            "Config key 'hide_thumbnail' has non-boolean value %r "
            "— falling back to default False.",
            raw,
        )
        return False
    return raw


def update_hide_thumbnail(enabled, config_path=None):
    """Update and persist 'hide_thumbnail' in config."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    config_data["hide_thumbnail"] = bool(enabled)
    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update hide_thumbnail: {e}")




def get_last_save_directory(config_path=None):
    """Read 'last_save_directory' from config.

    Falls back to the user's Desktop on first use (matching the
    "Save to Desktop" pin action); ~/ is the last resort.
    """
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    raw = config_data.get("last_save_directory")
    if isinstance(raw, str) and raw.strip():
        path = Path(raw.strip())
        if path.is_dir():
            return str(path)
        # String present but not an existing dir - could be a typo, a moved
        # folder, or a malformed value. Log so it's traceable, then fall back.
        logger.warning(
            "Config key 'last_save_directory' = %r is not an existing directory; falling back to Desktop.",
            raw,
        )
    elif raw is not None:
        logger.warning(
            "Config key 'last_save_directory' has non-string value %r; falling back to Desktop.",
            raw,
        )
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
    """Read 'thumbnail_display_time' from config (default 5000)."""
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    raw = config_data.get("thumbnail_display_time", 5000)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Config key 'thumbnail_display_time' has non-integer value %r; falling back to default 5000.",
            raw,
        )
        return 5000
    return value


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


def get_thumbnail_frame(config_path=None):
    """Read 'thumbnail_frame' (which corner ornament to show on the thumbnail).

    Returns the ornament id string ("" = none/off).  Legacy bool values are
    migrated: True -> "vine" (the default ornament), False -> "".  This mirrors
    the v1->v2 on-disk migration so reads are safe even before the file is
    rewritten.
    """
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    raw = config_data.get("thumbnail_frame", "")
    if isinstance(raw, bool):
        return "vine" if raw else ""
    if isinstance(raw, str):
        return raw
    logger.warning(
        "Config key 'thumbnail_frame' has unsupported value %r; falling back to off.",
        raw,
    )
    return ""


def update_thumbnail_frame(ornament_id, config_path=None):
    """Update and persist 'thumbnail_frame' in config.

    ornament_id is the ornament id string ("" = none/off).
    """
    if config_path is None:
        config_path = get_config_path()
    config_data = _load_config_data(config_path)
    config_data["thumbnail_frame"] = ornament_id or ""
    try:
        _write_config_data(config_path, config_data)
    except Exception as e:
        logger.error(f"Failed to update thumbnail_frame: {e}")


def get_ocr_font_size(state_path=None):
    """Read OCR text font size from state file (default 16)."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    state_data = _load_state_data(state_path)
    font_size = state_data.get("ocr_font_size", DEFAULT_OCR_FONT_SIZE)
    if isinstance(font_size, int) and 8 <= font_size <= 48:
        return font_size
    logger.warning(
        "State key 'ocr_font_size' has out-of-range value %r; "
        "falling back to default %d.",
        font_size, DEFAULT_OCR_FONT_SIZE,
    )
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


def get_onboarding_toast_shown(state_path=None):
    """Read whether the startup "ready" toast has already been shown once.

    Persisted across launches/upgrades; reset only if the state file is
    removed (e.g. uninstall or first run on a new machine). The toast is
    shown exactly once per install.
    """
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    state_data = _load_state_data(state_path)
    raw = state_data.get("onboarding_toast_shown", False)
    if not isinstance(raw, bool):
        logger.warning(
            "State key 'onboarding_toast_shown' has non-boolean value %r; falling back to default False.",
            raw,
        )
        return False
    return raw


def set_onboarding_toast_shown(state_path=None):
    """Mark the startup "ready" toast as shown (idempotent, write-once)."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    try:
        state_data = _load_state_data(state_path)
        if state_data.get("onboarding_toast_shown"):
            return  # already recorded — skip the disk write
        state_data["onboarding_toast_shown"] = True
        _write_state_data(state_data, state_path)
    except Exception as e:
        logger.error(f"Failed to update onboarding_toast_shown in state: {e}")


# ── Editor window size persistence ─────────────────────────────────────
# Only the window SIZE is remembered (the user's preferred editor size),
# never its position — the editor always opens centred on the cursor's
# screen. Position memory was dropped because it kept regressing (the
# config-vs-state path bug) and offered little for a short task. Size is
# screen-independent, so it has none of position's straddle-screen problems.


def get_editor_window_size(state_path=None):
    """Read the last image-editor window size from state.

    Returns a dict {w, h} or None when nothing valid is stored.
    """
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    state_data = _load_state_data(state_path)
    geo = state_data.get("editor_window_geometry")
    if not isinstance(geo, dict):
        return None
    try:
        w, h = int(geo["w"]), int(geo["h"])
        if w >= 320 and h >= 240:
            return {"w": w, "h": h}
    except (KeyError, TypeError, ValueError):
        pass
    return None


def set_editor_window_size(w, h, state_path=None):
    """Persist the image-editor window size to state (position is not stored)."""
    if state_path is None:
        state_path = STATE_PATH
    _ensure_default_state_exists(state_path)
    try:
        state_data = _load_state_data(state_path)
        state_data["editor_window_geometry"] = {"w": int(w), "h": int(h)}
        _write_state_data(state_data, state_path)
    except Exception as e:
        logger.error(f"Failed to update editor window size in state: {e}")


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
        if configured_language is not None:
            # Value present but unrecognized - log it. (A missing key falls
            # through to UI_LANG_AUTO silently, which is the normal default.)
            logger.warning(
                "Config key 'language' has unrecognized value %r; falling back to %r.",
                configured_language, UI_LANG_AUTO,
            )
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


def _get_system_ui_language():
    """Get the user's default system UI language code ('zh', 'zh-TW', 'ja', or 'en')."""
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
        # English and anything else we don't explicitly handle — the Win32
        # API is the authoritative source; don't fall through to the
        # unreliable locale.getdefaultlocale() codepath which can return
        # a mismatched locale on systems with East-Asian language packs.
        return UI_LANG_EN
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
    Resolve the final UI language to a concrete locale.
    Priority: config file (auto → system) > system default > English fallback.
    """
    config_language = _read_ui_lang_from_config(config_path)
    if config_language == UI_LANG_AUTO:
        config_language = _get_system_ui_language()
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
