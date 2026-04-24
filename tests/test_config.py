"""
Unit tests for the configuration module.
Covers hotkey parsing, path resolution, and UI text translations.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch
from hushsnap.config import (
    _ensure_default_config_exists,
    get_ocr_enabled_from_config,
    get_ocr_lang_from_config,
    get_user_data_dir,
    parse_hotkey,
    resolve_ui_lang,
    ui_text,
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
        with patch("hushsnap.config._read_ui_lang_from_installer_hint") as mock_hint:
            mock_hint.return_value = None
            lang = resolve_ui_lang(Path("dummy_path"))
            assert lang == "en"


def test_resolve_ui_lang_from_config_bcp47():
    """Test config language normalization (BCP 47 -> ISO 639-1)."""
    with patch("hushsnap.config._read_ui_lang_from_config") as mock_config:
        mock_config.return_value = "zh"
        lang = resolve_ui_lang(Path("dummy_path"))
        assert lang == "zh"


def test_resolve_ui_lang_from_installer_hint_when_config_auto():
    """Test installer hint is used when config is auto."""
    with patch("hushsnap.config._read_ui_lang_from_config") as mock_config:
        mock_config.return_value = "auto"
        with patch("hushsnap.config._read_ui_lang_from_installer_hint") as mock_hint:
            mock_hint.return_value = "zh"
            lang = resolve_ui_lang(Path("dummy_path"))
            assert lang == "zh"


def test_default_config_uses_chinese_ocr_when_installer_hint_is_chinese(tmp_path):
    config_path = tmp_path / "hushsnap_config.json"
    (tmp_path / "hushsnap_installer_lang.txt").write_text("zh", encoding="utf-8")

    _ensure_default_config_exists(config_path)

    config_data = json.loads(config_path.read_text(encoding="utf-8"))
    assert config_data["ocr_language"] == "zh-CN"
    assert "_hotkey_note" not in config_data


def test_get_ocr_lang_from_config_falls_back_to_installer_hint(tmp_path):
    config_path = tmp_path / "hushsnap_config.json"
    config_path.write_text(json.dumps({"language": "auto"}), encoding="utf-8")
    (tmp_path / "hushsnap_installer_lang.txt").write_text("zh", encoding="utf-8")

    assert get_ocr_lang_from_config(config_path) == "zh-CN"


def test_get_ocr_lang_from_config_normalizes_legacy_chinese_tag(tmp_path):
    config_path = tmp_path / "hushsnap_config.json"
    config_path.write_text(json.dumps({"ocr_language": "zh-CN"}), encoding="utf-8")

    assert get_ocr_lang_from_config(config_path) == "zh-CN"


def test_get_ocr_lang_from_config_normalizes_traditional_chinese_tag(tmp_path):
    config_path = tmp_path / "hushsnap_config.json"
    config_path.write_text(json.dumps({"ocr_language": "zh-HK"}), encoding="utf-8")

    assert get_ocr_lang_from_config(config_path) == "zh-TW"


def test_get_ocr_lang_from_config_logs_and_falls_back_on_error(tmp_path):
    config_path = tmp_path / "hushsnap_config.json"

    with patch("hushsnap.config._load_config_data", side_effect=RuntimeError("boom")):
        with patch("hushsnap.config._resolve_default_ocr_language", return_value="zh-CN") as mock_default:
            with patch("hushsnap.config.logger.debug") as mock_debug:
                assert get_ocr_lang_from_config(config_path) == "zh-CN"

    mock_default.assert_called_once_with(config_path)
    assert any(
        "Failed to read OCR language from config" in call.args[0]
        for call in mock_debug.call_args_list
    )


def test_get_ocr_enabled_from_config_logs_and_falls_back_on_error(tmp_path):
    config_path = tmp_path / "hushsnap_config.json"

    with patch("hushsnap.config._load_config_data", side_effect=RuntimeError("boom")):
        with patch("hushsnap.config.logger.debug") as mock_debug:
            assert get_ocr_enabled_from_config(config_path) is False

    assert any(
        "Failed to read OCR enabled state from config" in call.args[0]
        for call in mock_debug.call_args_list
    )
