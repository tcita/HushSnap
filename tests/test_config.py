"""
Unit tests for the configuration module.
Covers hotkey parsing, path resolution, and UI text translations.
"""

import tomllib
import pytest
from pathlib import Path
from unittest.mock import patch
from hushsnap.config import (
    _ensure_default_config_exists,
    get_user_data_dir,
    load_ocr_hotkey_setting,
    parse_hotkey,
    read_ocr_hotkey_text_from_config,
    resolve_ui_lang,
    ui_text,
    update_ocr_hotkey_in_config,
    get_configured_ui_lang,
    update_ui_lang_in_config,
)
from hushsnap.constants import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN

def test_parse_hotkey_valid():
    """Test parsing of various valid hotkey string formats."""
    # Test simple hotkey
    mask, vk, canonical = parse_hotkey("Alt+Q")
    assert mask == MOD_ALT
    assert vk == ord('Q')
    assert canonical == "Alt+Q"

    # Test complex hotkey
    mask, vk, canonical = parse_hotkey("Ctrl+Shift+A")
    assert mask == (MOD_CONTROL | MOD_SHIFT)
    assert vk == ord('A')
    assert canonical == "Ctrl+Shift+A"

    # Test single key
    mask, vk, canonical = parse_hotkey("F1")
    assert mask == 0
    assert vk == 0x70 # VK_F1
    assert canonical == "F1"

    # Test lowercase and spaces
    mask, vk, canonical = parse_hotkey(" ctrl + alt + s ")
    assert mask == (MOD_CONTROL | MOD_ALT)
    assert vk == ord('S')
    assert canonical == "Ctrl+Alt+S"

def test_parse_hotkey_invalid():
    """Test that invalid hotkey strings raise appropriate errors."""
    with pytest.raises(ValueError, match="Unknown modifier"):
        parse_hotkey("Cmd+A")
    
    with pytest.raises(ValueError, match="Unsupported key"):
        parse_hotkey("Ctrl+UnknownKey")
    
    with pytest.raises(ValueError, match="Hotkey must include at least one key"):
        parse_hotkey("")

def test_get_user_data_dir():
    """Test the resolution of the user data directory."""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = "C:\\Users\\Test\\AppData\\Local"
        with patch.object(Path, "mkdir") as mock_mkdir:
            path = get_user_data_dir()
            # Dev mode uses a dedicated data folder to avoid polluting release data.
            assert str(path) == "C:\\Users\\Test\\AppData\\Local\\HushSnap_Dev"
            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

def test_ui_text():
    """Test the UI translation lookup and formatting."""
    # Test basic translation
    assert ui_text("en", "error") == "Error"
    assert ui_text("zh", "error") == "错误"
    
    # Test formatting
    assert ui_text("en", "hotkey_enabled", hotkey="Alt+Q") == "Enabled Alt+Q"
    assert ui_text("zh", "hotkey_enabled", hotkey="Alt+Q") == "已启用 Alt+Q"

    # Test fallback to English
    assert ui_text("unknown", "error") == "Error"
    
    # Test fallback to key if missing
    assert ui_text("en", "non_existent_key") == "non_existent_key"

def test_resolve_ui_lang_fallback_to_english():
    """Test default fallback to English when no source provides a valid language."""
    with patch("hushsnap.config._read_ui_lang_from_config") as mock_config:
        mock_config.return_value = "auto"
        with patch("hushsnap.config._get_system_ui_language") as mock_system:
            mock_system.return_value = "en"
            lang = resolve_ui_lang(Path("dummy_path"))
            assert lang == "en"


def test_resolve_ui_lang_from_config_bcp47():
    """Test config language normalization (BCP 47 -> ISO 639-1)."""
    with patch("hushsnap.config._read_ui_lang_from_config") as mock_config:
        mock_config.return_value = "zh"
        lang = resolve_ui_lang(Path("dummy_path"))
        assert lang == "zh"


def test_resolve_ui_lang_from_system_default():
    """Test system default language is used when config is auto."""
    with patch("hushsnap.config._read_ui_lang_from_config") as mock_config:
        mock_config.return_value = "auto"
        with patch("hushsnap.config._get_system_ui_language") as mock_system:
            mock_system.return_value = "zh"
            lang = resolve_ui_lang(Path("dummy_path"))
            assert lang == "zh"


def test_default_config_omits_ocr_fields(tmp_path):
    config_path = tmp_path / "hushsnap_config.toml"

    _ensure_default_config_exists(config_path)

    config_data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    # ocr_language and ocr_engine moved to state file — config should not contain them
    assert "ocr_language" not in config_data
    assert "ocr_engine" not in config_data
    assert config_data["hotkey"] == "Alt+Q"
    assert config_data["ocr_hotkey"] == "Alt+Z"


# --- OCR hotkey config tests ---

def test_read_ocr_hotkey_text_from_config_valid(tmp_path):
    config_path = tmp_path / "hushsnap_config.toml"
    config_path.write_text('ocr_hotkey = "Alt+Shift+X"\n', encoding="utf-8")

    result = read_ocr_hotkey_text_from_config(config_path)
    assert result == "Alt+Shift+X"


def test_read_ocr_hotkey_text_from_config_defaults_when_missing(tmp_path):
    config_path = tmp_path / "hushsnap_config.toml"
    config_path.write_text('hotkey = "Alt+Q"\n', encoding="utf-8")

    from hushsnap.constants import DEFAULT_OCR_HOTKEY
    result = read_ocr_hotkey_text_from_config(config_path)
    assert result == DEFAULT_OCR_HOTKEY


def test_read_ocr_hotkey_text_from_config_defaults_when_blank(tmp_path):
    config_path = tmp_path / "hushsnap_config.toml"
    config_path.write_text('ocr_hotkey = ""\n', encoding="utf-8")

    from hushsnap.constants import DEFAULT_OCR_HOTKEY
    result = read_ocr_hotkey_text_from_config(config_path)
    assert result == DEFAULT_OCR_HOTKEY


def test_load_ocr_hotkey_setting_success(tmp_path):
    config_path = tmp_path / "hushsnap_config.toml"
    config_path.write_text('ocr_hotkey = "Ctrl+Alt+O"\n', encoding="utf-8")

    modifier, vk, name, returned_path = load_ocr_hotkey_setting(config_path)
    from hushsnap.constants import MOD_ALT, MOD_CONTROL
    assert modifier == (MOD_CONTROL | MOD_ALT)
    assert vk == ord("O")
    assert name == "Ctrl+Alt+O"
    assert returned_path == config_path


def test_load_ocr_hotkey_setting_fallback_on_invalid(tmp_path):
    config_path = tmp_path / "hushsnap_config.toml"
    config_path.write_text('ocr_hotkey = "@@@invalid@@@"\n', encoding="utf-8")

    with patch("hushsnap.config.logger.warning") as mock_warn:
        modifier, vk, name, returned_path = load_ocr_hotkey_setting(config_path)

    assert mock_warn.called
    from hushsnap.constants import DEFAULT_OCR_HOTKEY, MOD_ALT
    assert modifier == MOD_ALT
    assert vk == ord("Z")
    assert name == DEFAULT_OCR_HOTKEY


def test_update_ocr_hotkey_in_config_writes_to_disk(tmp_path):
    config_path = tmp_path / "hushsnap_config.toml"
    config_path.write_text('ocr_hotkey = "Alt+Z"\n', encoding="utf-8")

    update_ocr_hotkey_in_config(config_path, "Ctrl+Shift+K")

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert data["ocr_hotkey"] == "Ctrl+Shift+K"


def test_get_configured_ui_lang(tmp_path):
    config_path = tmp_path / "hushsnap_config.toml"
    config_path.write_text('language = "zh"\n', encoding="utf-8")
    assert get_configured_ui_lang(config_path) == "zh"

    config_path.write_text('language = "auto"\n', encoding="utf-8")
    assert get_configured_ui_lang(config_path) == "auto"

    config_path.write_text('', encoding="utf-8")
    assert get_configured_ui_lang(config_path) == "auto"


def test_update_ui_lang_in_config(tmp_path):
    config_path = tmp_path / "hushsnap_config.toml"
    config_path.write_text('language = "auto"\n', encoding="utf-8")

    update_ui_lang_in_config(config_path, "en")
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert data["language"] == "en"
