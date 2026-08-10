"""
Unit tests for the configuration module.
Covers hotkey parsing, path resolution, and UI text translations.
"""

import logging
import tomllib
import pytest
from pathlib import Path
from unittest.mock import patch
from hushsnap.config import (
    _ensure_default_config_exists,
    _normalize_ui_language_code,
    get_mutex_name,
    get_user_data_dir,
    parse_hotkey,
    resolve_ui_lang,
    ui_text,
    get_configured_ui_lang,
    update_ui_lang_in_config,
    get_debug_enabled,
    get_show_capture_dimension_label,
    get_thumbnail_display_time,
    get_ocr_engine,
    get_ocr_font_size,
    get_onboarding_toast_shown,
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


# ── _normalize_ui_language_code ─────────────────────────────────────

def test_normalize_ui_lang_auto():
    assert _normalize_ui_language_code("auto", allow_auto=True) == "auto"
    # "auto" is not a real language code — without allow_auto it's rejected
    assert _normalize_ui_language_code("auto", allow_auto=False) is None


def test_normalize_ui_lang_en_variants():
    assert _normalize_ui_language_code("en") == "en"
    assert _normalize_ui_language_code("en-US") == "en"
    assert _normalize_ui_language_code("en_GB") == "en"
    assert _normalize_ui_language_code("EN") == "en"


def test_normalize_ui_lang_zh_variants():
    assert _normalize_ui_language_code("zh") == "zh"
    assert _normalize_ui_language_code("zh-CN") == "zh"
    assert _normalize_ui_language_code("zh-SG") == "zh"


def test_normalize_ui_lang_zh_tw_variants():
    assert _normalize_ui_language_code("zh-TW") == "zh-TW"
    assert _normalize_ui_language_code("zh-HK") == "zh-TW"
    assert _normalize_ui_language_code("zh-MO") == "zh-TW"
    assert _normalize_ui_language_code("zh-Hant") == "zh-TW"


def test_normalize_ui_lang_unknown():
    assert _normalize_ui_language_code("fr") is None
    assert _normalize_ui_language_code("ja") == "ja"
    assert _normalize_ui_language_code("ja-JP") == "ja"


def test_normalize_ui_lang_invalid_input():
    assert _normalize_ui_language_code(None) is None
    assert _normalize_ui_language_code("") is None
    assert _normalize_ui_language_code(123) is None


# ── _ensure_default_config_exists (migration) ───────────────────────

def test_config_migration_adds_missing_keys(tmp_path):
    """When _CONFIG_DEFAULTS gains a new key, existing config files get it
    without overwriting user-changed values."""
    config_path = tmp_path / "hushsnap_config.toml"
    config_path.write_text(
        'hotkey = "Ctrl+Shift+K"\nlanguage = "zh"\n', encoding="utf-8"
    )

    _ensure_default_config_exists(config_path)

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    # User values preserved
    assert data["hotkey"] == "Ctrl+Shift+K"
    assert data["language"] == "zh"
    # Default keys injected
    assert data["debug"] is True  # dev-mode default
    assert data["thumbnail_display_time"] == 12000


def test_config_migration_does_not_overwrite_existing_keys(tmp_path):
    """Keys already present in config must not be overwritten by defaults."""
    config_path = tmp_path / "hushsnap_config.toml"
    config_path.write_text(
        'hotkey = "Alt+X"\n', encoding="utf-8"
    )

    _ensure_default_config_exists(config_path)

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert data["hotkey"] == "Alt+X"  # user's choice


# ── Editor window size persistence ─────────────────────────────────────


def test_editor_window_size_roundtrip(tmp_path):
    """set/get editor window size persists w/h to the state file."""
    from hushsnap.config import get_editor_window_size, set_editor_window_size
    state_path = tmp_path / "hushsnap_state.toml"
    assert get_editor_window_size(state_path) is None  # nothing yet

    set_editor_window_size(1100, 720, state_path)
    assert get_editor_window_size(state_path) == {"w": 1100, "h": 720}


def test_editor_window_size_preserves_other_state(tmp_path):
    """Writing editor size must not drop OCR/onboarding state fields."""
    from hushsnap.config import (
        set_editor_window_size,
        get_ocr_font_size,
        update_ocr_font_size,
        get_editor_window_size,
    )
    state_path = tmp_path / "hushsnap_state.toml"
    update_ocr_font_size(24, state_path)
    set_editor_window_size(800, 600, state_path)

    assert get_ocr_font_size(state_path) == 24
    assert get_editor_window_size(state_path) == {"w": 800, "h": 600}


def test_editor_window_size_rejects_too_small(tmp_path):
    """A size smaller than the minimum is treated as not stored."""
    from hushsnap.config import set_editor_window_size, get_editor_window_size
    state_path = tmp_path / "hushsnap_state.toml"
    set_editor_window_size(100, 100, state_path)
    assert get_editor_window_size(state_path) is None


# ── Bad-value fallback: corrupt config values fall back to defaults + log ──
# Each pair: a valid value is returned as-is (no warning), a bad value falls
# back to the default and emits a WARNING so it's traceable in logs without
# crashing or nagging the user.


def _write_config(path, body):
    path.write_text(body, encoding="utf-8")


def test_debug_enabled_valid_value_no_warning(tmp_path, caplog):
    config_path = tmp_path / "hushsnap_config.toml"
    _write_config(config_path, "debug = false\n")
    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        assert get_debug_enabled(config_path) is False
    assert not any("debug" in r.message for r in caplog.records)


def test_debug_enabled_bad_value_falls_back_and_logs(tmp_path, caplog):
    config_path = tmp_path / "hushsnap_config.toml"
    _write_config(config_path, 'debug = "apple"\n')  # string, not bool
    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        result = get_debug_enabled(config_path)
    # Falls back to the dev default (True in tests, since not frozen).
    assert result is True
    assert any("debug" in r.message and "apple" in r.message
               and r.levelno == logging.WARNING for r in caplog.records)






def test_show_capture_dimension_label_bad_value_falls_back(tmp_path, caplog):
    config_path = tmp_path / "hushsnap_config.toml"
    _write_config(config_path, "show_capture_dimension_label = []\n")  # array
    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        assert get_show_capture_dimension_label(config_path) is True  # default
    assert any("show_capture_dimension_label" in r.message for r in caplog.records)


def test_thumbnail_display_time_bad_value_falls_back_no_crash(tmp_path, caplog):
    """The old int(raw) path raised ValueError on a non-numeric value and
    crashed the caller. It must now fall back to the default and log."""
    config_path = tmp_path / "hushsnap_config.toml"
    _write_config(config_path, 'thumbnail_display_time = "apple"\n')
    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        result = get_thumbnail_display_time(config_path)
    assert result == 12000  # default, not a crash
    assert any("thumbnail_display_time" in r.message and "apple" in r.message
               for r in caplog.records)


def test_thumbnail_display_time_valid_value(tmp_path, caplog):
    config_path = tmp_path / "hushsnap_config.toml"
    _write_config(config_path, "thumbnail_display_time = 5000\n")
    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        assert get_thumbnail_display_time(config_path) == 5000
    assert not any("thumbnail_display_time" in r.message for r in caplog.records)


def test_ocr_engine_bad_value_falls_back(tmp_path, caplog):
    state_path = tmp_path / "hushsnap_state.toml"
    _write_config(state_path, 'ocr_engine = "apple"\n')
    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        assert get_ocr_engine(state_path) == "ppocr"  # default
    assert any("ocr_engine" in r.message and "apple" in r.message
               for r in caplog.records)


def test_ocr_engine_missing_key_no_warning(tmp_path, caplog):
    """A missing key is the normal migration/first-run case - it must NOT
    log a warning (only genuinely bad values do)."""
    state_path = tmp_path / "hushsnap_state.toml"
    _write_config(state_path, "ocr_font_size = 16\n")  # no ocr_engine key
    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        assert get_ocr_engine(state_path) == "ppocr"  # default, silently
    assert not any("ocr_engine" in r.message for r in caplog.records)


def test_ocr_font_size_out_of_range_falls_back(tmp_path, caplog):
    state_path = tmp_path / "hushsnap_state.toml"
    _write_config(state_path, "ocr_font_size = 999\n")  # out of 8..48
    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        from hushsnap.config import DEFAULT_OCR_FONT_SIZE
        assert get_ocr_font_size(state_path) == DEFAULT_OCR_FONT_SIZE
    assert any("ocr_font_size" in r.message for r in caplog.records)


def test_ui_lang_bad_value_falls_back_to_auto(tmp_path, caplog):
    config_path = tmp_path / "hushsnap_config.toml"
    _write_config(config_path, 'language = "klingon"\n')
    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        assert get_configured_ui_lang(config_path) == "auto"  # default
    assert any("language" in r.message and "klingon" in r.message
               for r in caplog.records)


def test_ui_lang_missing_key_no_warning(tmp_path, caplog):
    """A missing language key is normal - must not log."""
    config_path = tmp_path / "hushsnap_config.toml"
    _write_config(config_path, "debug = true\n")  # no language key
    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        assert get_configured_ui_lang(config_path) == "auto"
    assert not any("language" in r.message for r in caplog.records)


def test_onboarding_toast_shown_bad_value_falls_back(tmp_path, caplog):
    """A non-boolean state value falls back to False and logs - matching the
    boolean-field handling in config, since the state file lives right next
    to the config file and is equally reachable to a user editing by hand."""
    state_path = tmp_path / "hushsnap_state.toml"
    _write_config(state_path, 'onboarding_toast_shown = "yes"\n')
    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        assert get_onboarding_toast_shown(state_path) is False  # default
    assert any("onboarding_toast_shown" in r.message for r in caplog.records)


# ── Whole-file TOML syntax error (e.g. ``key = cat`` with no quotes) ──────
# A broken file makes _load_config_data return {}, so every field falls back
# to its default. The failure itself is logged once at WARNING - we don't
# classify the cause, just signal "config is unusable, defaults in use".


def test_config_unusable_logs_warning_and_falls_back(tmp_path, caplog):
    """A present-but-broken config logs a single WARNING and yields defaults."""
    config_path = tmp_path / "hushsnap_config.toml"
    # `thumbnail_display_time = cat` is invalid TOML (bare word as value).
    _write_config(config_path, 'thumbnail_display_time = cat\n')

    with caplog.at_level(logging.WARNING, logger="hushsnap.config"):
        assert get_thumbnail_display_time(config_path) == 12000  # default

    assert any(
        r.levelno == logging.WARNING and "config data" in r.message
        for r in caplog.records
    )


# ── Hotkey reload: parse failure stays silent (no toast) ──────────────────


def test_hotkey_reload_parse_failure_no_status_toast(tmp_path):
    """When the watcher reloads and the config is unparseable (a genuinely
    broken hotkey OR an editor's save-midway state), it must NOT emit a
    status toast - just keep the current hotkey and log. This is the bug
    where editing unrelated keys (e.g. debug) popped 'Invalid config'."""
    from hushsnap.system.hotkey_manager import HotkeyManager

    config_path = tmp_path / "hushsnap_config.toml"
    _write_config(config_path, 'hotkey = "Ctrl+Alt+!!!"\n')  # unparseable

    calls = []
    hm = HotkeyManager.__new__(HotkeyManager)  # bypass Qt/ctypes init
    hm.config_path = config_path
    hm.current_hotkey_modifier = MOD_ALT
    hm.current_hotkey_virtual_key = ord("Q")
    hm.current_hotkey_name = "Alt+Q"
    hm.tray_icon = None
    hm._request_status_msg = lambda *a, **k: calls.append((a, k))

    # _apply_hotkey_reload_core also calls _ensure_watch_targets; stub it so
    # the test isolates the parse path without a real QFileSystemWatcher.
    hm._ensure_watch_targets = lambda: None

    hm._apply_hotkey_reload_core()

    # No status/toast emitted; current hotkey kept as-is.
    assert calls == []
    assert hm.current_hotkey_name == "Alt+Q"


# ── get_mutex_name simplification ─────────────────────────────────────
# v1.5.1+ dropped the .Dev suffix so dev and packaged builds share the
# same mutex name — simplifies single-instance detection.


def test_get_mutex_name_no_dev_suffix():
    """get_mutex_name returns the same name regardless of frozen status."""
    name = get_mutex_name()
    assert name == "Local\\hushsnap.SingleInstance"
    assert ".Dev" not in name

