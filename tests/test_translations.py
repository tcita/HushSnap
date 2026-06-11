"""
Translation consistency tests.
Ensures all language packs have the same set of keys and that formatting
placeholders match, so no UI string silently degrades to the raw key.
"""

import re

from hushsnap.translations import UI_TEXT, UI_LANG_EN, UI_LANG_ZH, UI_LANG_ZH_TW, UI_LANG_JA


def _format_keys(template: str) -> set[str]:
    """Extract {key} formatting placeholders from a translation string."""
    return set(re.findall(r"\{(\w+)\}", template))


def test_all_languages_have_same_keys():
    """Every key in the English pack must exist in all other languages."""
    en_keys = set(UI_TEXT[UI_LANG_EN].keys())

    for lang in [UI_LANG_ZH, UI_LANG_ZH_TW, UI_LANG_JA]:
        lang_keys = set(UI_TEXT[lang].keys())
        missing = en_keys - lang_keys
        extra = lang_keys - en_keys
        assert not missing, f"{lang} is missing keys: {missing}"
        assert not extra, f"{lang} has extra keys not in EN: {extra}"


def test_format_placeholders_match():
    """Keys with {placeholder} formatting must use the same placeholders
    across all languages."""
    en = UI_TEXT[UI_LANG_EN]
    for key, en_template in en.items():
        en_placeholders = _format_keys(en_template)
        for lang in [UI_LANG_ZH, UI_LANG_ZH_TW, UI_LANG_JA]:
            lang_template = UI_TEXT[lang].get(key)
            assert lang_template is not None, f"{lang} missing key: {key}"
            lang_placeholders = _format_keys(lang_template)
            assert en_placeholders == lang_placeholders, (
                f"Placeholder mismatch for key '{key}' ({lang}): "
                f"EN={en_placeholders} vs {lang}={lang_placeholders}"
            )


def test_non_empty_translations():
    """No translation string should be empty (silent UI breaks are hard to spot)."""
    for lang, table in UI_TEXT.items():
        for key, value in table.items():
            assert value, f"{lang}.{key} is empty"
