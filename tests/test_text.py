"""
Unit tests for the text composition and language adapter module.
Covers token classification, punctuation cleanup, text normalization,
language adapter selection, and full result composition.
"""

import pytest

from hushsnap.ocr.text import (
    OcrTextAdapter,
    cleanup_ocr_text_line,
    compose_cjk_line_text,
    compose_default_line_text,
    compose_spaced_line_text,
    compose_text_from_result,
    finalize_default_text,
    finalize_english_text,
    is_space_joining_word,
    matches_chinese,
    matches_english,
    normalize_ocr_text,
    normalize_token_text,
    select_text_adapter,
    _postprocess_layout_text,
)
from hushsnap.ocr.models import OcrBox, OcrLine, OcrRecognition, OcrWord


# ═══════════════════════════════════════════════════════════════════════
# is_space_joining_word
# ═══════════════════════════════════════════════════════════════════════

def test_is_space_joining_word_empty():
    assert is_space_joining_word("") is False


def test_is_space_joining_word_cjk_single():
    """CJK characters should never be space-joining, even if ≥2 chars."""
    assert is_space_joining_word("中") is False
    assert is_space_joining_word("你好") is False  # CJK, multi-char — still False


def test_is_space_joining_word_cjk_mixed():
    """Token containing any CJK character is not space-joining."""
    assert is_space_joining_word("A中") is False


def test_is_space_joining_word_japanese_kana():
    """Hiragana and Katakana are in the no-space script class."""
    assert is_space_joining_word("あ") is False
    assert is_space_joining_word("ア") is False


def test_is_space_joining_word_latin_single():
    """Single Latin letter (Lu category) IS space-joining (L but not Lo)."""
    assert is_space_joining_word("A") is True


def test_is_space_joining_word_latin_multi():
    """Multi-char Latin tokens ARE space-joining."""
    assert is_space_joining_word("hello") is True
    assert is_space_joining_word("ab") is True


def test_is_space_joining_word_numeric_digit():
    """Single Nd-category digit is space-joining."""
    assert is_space_joining_word("1") is True
    assert is_space_joining_word("123") is True


def test_is_space_joining_word_punctuation():
    """Punctuation like '.' has category Po, not L or Nd, so False if single."""
    assert is_space_joining_word(".") is False
    assert is_space_joining_word("..") is True  # len ≥ 2


def test_is_space_joining_word_null_and_none():
    assert is_space_joining_word(None) is False


# ═══════════════════════════════════════════════════════════════════════
# cleanup_ocr_text_line
# ═══════════════════════════════════════════════════════════════════════

def test_cleanup_punctuation_no_leading_space():
    """Remove space before punctuation."""
    assert cleanup_ocr_text_line("hello .") == "hello."
    assert cleanup_ocr_text_line("hello , world") == "hello, world"
    assert cleanup_ocr_text_line("hello ; world") == "hello; world"
    assert cleanup_ocr_text_line("hello : world") == "hello: world"
    assert cleanup_ocr_text_line("hello !") == "hello!"
    assert cleanup_ocr_text_line("hello ?") == "hello?"


def test_cleanup_punctuation_add_space_after():
    """After punctuation followed by alphanumeric, insert a space."""
    assert cleanup_ocr_text_line("hello.World") == "hello. World"
    assert cleanup_ocr_text_line("end,begin") == "end, begin"
    assert cleanup_ocr_text_line("ok;next") == "ok; next"


def test_cleanup_empty():
    assert cleanup_ocr_text_line("") == ""
    # None is not handled — raises TypeError from re.sub
    with pytest.raises(TypeError):
        cleanup_ocr_text_line(None)


def test_cleanup_no_change():
    assert cleanup_ocr_text_line("hello world") == "hello world"
    assert cleanup_ocr_text_line("中文。测试") == "中文。测试"


# ═══════════════════════════════════════════════════════════════════════
# normalize_token_text
# ═══════════════════════════════════════════════════════════════════════

def test_normalize_token_text_unicode_normalize():
    """NFKC normalization collapses compatibility characters."""
    assert normalize_token_text("ﬁ") == "fi"  # ligature → normalized
    assert normalize_token_text("ℌ") == "H"   # fancy H → ASCII


def test_normalize_token_text_rstrip():
    """Trailing whitespace is stripped."""
    assert normalize_token_text("hello   ") == "hello"


def test_normalize_token_text_empty_and_none():
    assert normalize_token_text("") == ""
    assert normalize_token_text(None) == ""  # "".rstrip() → ""


# ═══════════════════════════════════════════════════════════════════════
# compose_default_line_text / compose_spaced_line_text / compose_cjk_line_text
# ═══════════════════════════════════════════════════════════════════════

def _line(text="test line"):
    return OcrLine(text=text)


def test_compose_default_line_text():
    # Trailing spaces/tabs are stripped (OCR noise), but \n is preserved (paragraph marker)
    assert compose_default_line_text(_line("hello world  ")) == "hello world"
    assert compose_default_line_text(_line("Para 1\n")) == "Para 1\n"


def test_compose_spaced_line_text():
    assert compose_spaced_line_text(_line("hello world  ")) == "hello world"


def test_compose_cjk_line_text():
    """CJK line text strips spaces/tabs but preserves \\n paragraph markers."""
    assert compose_cjk_line_text(_line("中文测试  ")) == "中文测试"
    assert compose_cjk_line_text(_line("沪 A 测试")) == "沪 A 测试"


def test_compose_cjk_line_with_indentation():
    """Pre-composed lines with indentation should be preserved."""
    assert compose_cjk_line_text(_line("  中文内容")) == "  中文内容"


# ═══════════════════════════════════════════════════════════════════════
# normalize_ocr_text
# ═══════════════════════════════════════════════════════════════════════

def test_normalize_ocr_text_simple():
    assert normalize_ocr_text("hello\nworld") == "hello\nworld"


def test_normalize_ocr_text_line_endings():
    """CRLF and lone CR are normalized to LF."""
    assert normalize_ocr_text("line1\r\nline2\rline3") == "line1\nline2\nline3"


def test_normalize_ocr_text_trailing_spaces():
    """Trailing spaces per line are stripped."""
    assert normalize_ocr_text("hello   \nworld   ") == "hello\nworld"


def test_normalize_ocr_text_blank_lines_preserved():
    """Truly blank lines (paragraph separators) are preserved."""
    text = "paragraph1\n\nparagraph2"
    assert normalize_ocr_text(text) == "paragraph1\n\nparagraph2"


def test_normalize_ocr_text_whitespace_only_lines():
    """Lines with only whitespace become truly empty."""
    assert normalize_ocr_text("hello\n   \nworld") == "hello\n\nworld"


def test_normalize_ocr_text_surrounding_whitespace():
    """Leading/trailing blank lines are stripped."""
    assert normalize_ocr_text("\n\nhello\nworld\n\n") == "hello\nworld"


def test_normalize_ocr_text_empty():
    assert normalize_ocr_text("") == ""
    assert normalize_ocr_text(None) == ""


# ═══════════════════════════════════════════════════════════════════════
# Language matchers
# ═══════════════════════════════════════════════════════════════════════

def test_matches_chinese():
    assert matches_chinese("zh") is True
    assert matches_chinese("zh-CN") is True
    assert matches_chinese("zh-TW") is True
    assert matches_chinese("ZH") is True
    assert matches_chinese("en") is False
    assert matches_chinese("") is False


def test_matches_english():
    assert matches_english("en") is True
    assert matches_english("en-US") is True
    assert matches_english("EN") is True
    assert matches_english("zh") is False
    assert matches_english("") is False


# ═══════════════════════════════════════════════════════════════════════
# finalize_default_text / finalize_english_text
# ═══════════════════════════════════════════════════════════════════════

def test_finalize_default_text():
    # finalize_default_text wraps normalize_ocr_text — exercise its transforms
    # (CRLF/CR normalization, trailing-space stripping), not just identity passthrough.
    assert finalize_default_text("a\r\nb  \nc\r") == "a\nb\nc"


def test_finalize_english_text():
    assert finalize_english_text("a\r\nb  \nc\r") == "a\nb\nc"


# ═══════════════════════════════════════════════════════════════════════
# _postprocess_layout_text
# ═══════════════════════════════════════════════════════════════════════

def test_postprocess_normalizes_excessive_blank_lines():
    """Max 2 consecutive newlines (i.e. 1 blank line between paragraphs)."""
    text = "a\n\n\n\nb"
    result = _postprocess_layout_text(text)
    assert result == "a\n\nb"


def test_postprocess_keeps_single_blank_line():
    assert _postprocess_layout_text("a\n\nb") == "a\n\nb"


def test_postprocess_strips_trailing_newlines():
    assert _postprocess_layout_text("a\nb\n\n") == "a\nb"


def test_postprocess_whitespace_only_lines_become_empty():
    text = "a\n   \nb"
    result = _postprocess_layout_text(text)
    assert result == "a\n\nb"


def test_postprocess_preserves_indentation():
    """Non-empty lines keep their leading whitespace during line processing.

    Note: the final .strip() on the full text removes leading whitespace from
    the first line and trailing from the last, but internal indentation on
    middle lines is preserved.
    """
    text = "  first line\n  middle line\n  last line"
    result = _postprocess_layout_text(text)
    # First line loses indent (final .strip()), middle keeps it
    assert "first line" in result
    assert "  middle line" in result
    assert "last line" in result


def test_postprocess_empty_and_none():
    assert _postprocess_layout_text("") == ""
    assert _postprocess_layout_text(None) is None


# ═══════════════════════════════════════════════════════════════════════
# select_text_adapter
# ═══════════════════════════════════════════════════════════════════════

def test_select_chinese_adapter():
    adapter = select_text_adapter("zh-CN")
    assert adapter.name == "chinese"
    assert adapter.matches_language("zh-CN") is True


def test_select_english_adapter():
    adapter = select_text_adapter("en-US")
    assert adapter.name == "english"


def test_select_default_adapter_for_unknown():
    adapter = select_text_adapter("fr-FR")
    assert adapter.name == "default"


def test_select_default_adapter_for_empty():
    adapter = select_text_adapter("")
    assert adapter.name == "default"


# ═══════════════════════════════════════════════════════════════════════
# compose_text_from_result
# ═══════════════════════════════════════════════════════════════════════

def test_compose_text_flat_text_fallback():
    """When result has no lines, use raw text via adapter's finalize_text."""
    result = OcrRecognition(text="hello world")
    text = compose_text_from_result(result, language_tag="en-US")
    assert text == "hello world"


def test_compose_text_from_lines():
    """When lines exist, compose via adapter and _postprocess_layout_text."""
    result = OcrRecognition(
        lines=[
            OcrLine(text="Line 1", bounding_box=OcrBox(0, 0, 100, 20)),
            OcrLine(text="Line 2", bounding_box=OcrBox(0, 30, 100, 20)),
        ]
    )
    text = compose_text_from_result(result, language_tag="en-US")
    assert text == "Line 1\nLine 2"


def test_compose_text_cjk_no_extra_spaces():
    """CJK adapter should not add spaces between CJK characters."""
    result = OcrRecognition(
        lines=[
            OcrLine(text="沪A测试", bounding_box=OcrBox(0, 0, 100, 20)),
        ]
    )
    text = compose_text_from_result(result, language_tag="zh-CN")
    # CJK adapter preserves internal spacing as-is
    assert text == "沪A测试"


def test_compose_text_empty_lines_filtered():
    """Empty or whitespace-only composed lines are filtered out."""
    result = OcrRecognition(
        lines=[
            OcrLine(text="", bounding_box=OcrBox()),
            OcrLine(text="   ", bounding_box=OcrBox()),
            OcrLine(text="valid", bounding_box=OcrBox(0, 0, 100, 20)),
        ]
    )
    text = compose_text_from_result(result, language_tag="en-US")
    assert text == "valid"


def test_compose_text_all_lines_empty_falls_back_to_flat():
    """When all lines compose to empty, fall back to flat text."""
    result = OcrRecognition(
        text="flat fallback",
        lines=[
            OcrLine(text="", bounding_box=OcrBox()),
            OcrLine(text="   ", bounding_box=OcrBox()),
        ],
    )
    text = compose_text_from_result(result, language_tag="en-US")
    assert text == "flat fallback"


def test_compose_text_default_adapter_cleans_punctuation():
    """Default adapter applies punctuation cleanup via cleanup_ocr_text_line."""
    result = OcrRecognition(
        lines=[
            OcrLine(text="hello . world", bounding_box=OcrBox(0, 0, 100, 20)),
        ]
    )
    text = compose_text_from_result(result, language_tag="fr-FR")
    assert text == "hello. world"


def test_compose_text_paragraph_break_via_double_newline():
    """Paragraph breaks are preserved when the text already contains \\n\\n
    (e.g. from engines that output paragraph structure directly)."""
    result = OcrRecognition(
        lines=[
            OcrLine(text="Para 1\n\nPara 2", bounding_box=OcrBox(0, 0, 200, 40)),
        ]
    )
    text = compose_text_from_result(result, language_tag="en-US")
    assert text == "Para 1\n\nPara 2"


def test_compose_text_paragraph_break_preserved():
    """A trailing \\n on a line (e.g. from _separate_paragraphs) survives
    through the compose pipeline and creates a paragraph break in the final text.
    The \\n is NOT stripped — trailing whitespace cleanup is deferred to
    normalize_ocr_text in finalize_text, which handles it line-by-line."""
    result = OcrRecognition(
        lines=[
            OcrLine(text="Para 1\n", bounding_box=OcrBox(0, 0, 100, 20)),
            OcrLine(text="Para 2", bounding_box=OcrBox(0, 100, 100, 20)),
        ]
    )
    text = compose_text_from_result(result, language_tag="en-US")
    # The \n survives compose, join produces \n\n, finalize preserves the paragraph break
    assert text == "Para 1\n\nPara 2"
