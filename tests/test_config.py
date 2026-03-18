import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from hushsnap.config import parse_hotkey, get_user_data_dir, resolve_ui_lang, ui_text
from hushsnap.constants import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN

def test_parse_hotkey_valid():
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
    with pytest.raises(ValueError, match="Unknown modifier"):
        parse_hotkey("Cmd+A")
    
    with pytest.raises(ValueError, match="Unsupported key"):
        parse_hotkey("Ctrl+UnknownKey")
    
    with pytest.raises(ValueError, match="Hotkey must include at least one key"):
        parse_hotkey("")

def test_get_user_data_dir():
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = "C:\\Users\\Test\\AppData\\Local"
        with patch.object(Path, "mkdir") as mock_mkdir:
            path = get_user_data_dir()
            assert str(path) == "C:\\Users\\Test\\AppData\\Local\\HushSnap"
            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

def test_ui_text():
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

@patch("hushsnap.config.QtCore.QLocale")
def test_resolve_ui_lang_auto(mock_qlocale):
    # Mock system locale to Chinese
    mock_system = MagicMock()
    mock_system.name.return_value = "zh_CN"
    mock_qlocale.system.return_value = mock_system
    
    with patch("os.environ.get") as mock_env:
        mock_env.return_value = ""
        with patch("hushsnap.config._read_ui_lang_from_config") as mock_config:
            mock_config.return_value = "auto"
            with patch("hushsnap.config._read_ui_lang_from_installer_hint") as mock_hint:
                mock_hint.return_value = None
                
                lang = resolve_ui_lang(Path("dummy_path"))
                assert lang == "zh"

@patch("os.environ.get")
def test_resolve_ui_lang_env(mock_env):
    mock_env.return_value = "en"
    lang = resolve_ui_lang(Path("dummy_path"))
    assert lang == "en"
