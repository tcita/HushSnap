import json
import os
import sys
import ctypes
from ctypes import wintypes
from pathlib import Path

from PyQt6 import QtCore

from .constants import (
    DEFAULT_HOTKEY,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    UI_LANG_AUTO,
    UI_LANG_EN,
    UI_LANG_ENV,
    UI_LANG_ZH,
    UI_TEXT,
)

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_create_mutex = _kernel32.CreateMutexW
_create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_create_mutex.restype = wintypes.HANDLE
_close_handle = _kernel32.CloseHandle
_close_handle.argtypes = (wintypes.HANDLE,)
_close_handle.restype = wintypes.BOOL
_ERROR_ALREADY_EXISTS = 183


def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent



def get_resource_dir():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent

def get_config_path():
    return get_app_dir() / "hashsnap_config.json"


def _hotkey_warning_note():
    return "Note: You can edit hotkey manually (including single-key). Some keys may conflict with system/apps; use at your own discretion."


def _ensure_default_config_exists(config_path):
    if config_path.exists():
        return
    try:
        config_path.write_text(
            json.dumps(
                {
                    "hotkey": DEFAULT_HOTKEY,
                    "language": UI_LANG_AUTO,
                    "_hotkey_note": _hotkey_warning_note(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _parse_virtual_key(token):
    normalized_token = token.strip().upper()
    if len(normalized_token) == 1 and "A" <= normalized_token <= "Z":
        return ord(normalized_token)
    if len(normalized_token) == 1 and "0" <= normalized_token <= "9":
        return ord(normalized_token)
    if normalized_token.startswith("F") and normalized_token[1:].isdigit():
        function_key_index = int(normalized_token[1:])
        if 1 <= function_key_index <= 24:
            return 0x6F + function_key_index

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
    hotkey_parts = [part.strip() for part in hotkey_text.split("+") if part.strip()]
    if len(hotkey_parts) < 1:
        raise ValueError("Hotkey must include at least one key.")

    if len(hotkey_parts) == 1:
        modifier_tokens = []
        key_token = hotkey_parts[0]
    else:
        modifier_tokens = hotkey_parts[:-1]
        key_token = hotkey_parts[-1]

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

    virtual_key = _parse_virtual_key(key_token)
    if virtual_key is None:
        raise ValueError(f"Unsupported key: {key_token}")

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
    try:
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(config_data, dict):
            return config_data
    except Exception:
        pass
    return {}


def _write_config_data(config_path, config_data):
    config_path.write_text(
        json.dumps(config_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _ensure_hotkey_note_field(config_path):
    config_data = _load_config_data(config_path)
    note = _hotkey_warning_note()
    if config_data.get("_hotkey_note") == note:
        return
    config_data["_hotkey_note"] = note
    _write_config_data(config_path, config_data)


def read_hotkey_text_from_config(config_path):
    config_data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config_data, dict):
        raise ValueError("Config must be a JSON object.")
    hotkey_value = config_data.get("hotkey")
    if not isinstance(hotkey_value, str) or not hotkey_value.strip():
        raise ValueError("hotkey must be a non-empty string.")
    return hotkey_value.strip()


def update_hotkey_in_config(config_path, hotkey_text):
    config_data = _load_config_data(config_path)
    config_data["hotkey"] = hotkey_text

    language_value = config_data.get("language")
    if not isinstance(language_value, str) or not language_value.strip():
        config_data["language"] = UI_LANG_AUTO
    config_data["_hotkey_note"] = _hotkey_warning_note()

    _write_config_data(config_path, config_data)


def load_hotkey_setting():
    config_path = get_config_path()
    _ensure_default_config_exists(config_path)
    _ensure_hotkey_note_field(config_path)

    try:
        modifier_mask, virtual_key, canonical_hotkey = parse_hotkey(
            read_hotkey_text_from_config(config_path)
        )
        return modifier_mask, virtual_key, canonical_hotkey, config_path
    except Exception:
        modifier_mask, virtual_key, canonical_hotkey = parse_hotkey(DEFAULT_HOTKEY)
        return modifier_mask, virtual_key, canonical_hotkey, config_path


def _read_ui_lang_from_config(config_path):
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
    hint_path = config_path.parent / "hashsnap_installer_lang.txt"
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
    env_language = os.environ.get(UI_LANG_ENV, "").strip().lower()
    if env_language in {UI_LANG_EN, UI_LANG_ZH}:
        return env_language

    config_language = _read_ui_lang_from_config(config_path)
    if config_language in {UI_LANG_EN, UI_LANG_ZH}:
        return config_language

    installer_hint_language = _read_ui_lang_from_installer_hint(config_path)
    if installer_hint_language in {UI_LANG_EN, UI_LANG_ZH}:
        return installer_hint_language

    locale_name = QtCore.QLocale.system().name().lower()
    return UI_LANG_ZH if locale_name.startswith("zh") else UI_LANG_EN


def ui_text(lang, key, **kwargs):
    lang_table = UI_TEXT.get(lang, UI_TEXT[UI_LANG_EN])
    text_template = lang_table.get(key, UI_TEXT[UI_LANG_EN].get(key, key))
    return text_template.format(**kwargs)


# 用互斥锁防止多开
def is_already_running():
    mutex_name = "Local\\HashSnap.SingleInstance"
    handle = _create_mutex(None, False, mutex_name)
    if not handle:
        return None

    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        _close_handle(handle)
        return None

    return handle

