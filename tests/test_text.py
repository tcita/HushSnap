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
    extract_urls,
    finalize_default_text,
    finalize_english_text,
    find_url_at_position,
    is_space_joining_word,
    matches_chinese,
    matches_english,
    normalize_ocr_text,
    normalize_token_text,
    normalize_url,
    apply_outside_urls,
    select_text_adapter,
    _iter_url_spans,
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

def test_cleanup_preserves_space_before_punctuation():
    """Punctuation cleanup was removed: spaces before punctuation are
    preserved, not tightened.  These spaces come from the layout engine
    (inline-gap geometry or indentation) and represent real layout, not
    OCR noise - the old delete rule ate indent and gap spaces."""
    assert cleanup_ocr_text_line("hello .") == "hello ."
    assert cleanup_ocr_text_line("hello , world") == "hello , world"
    assert cleanup_ocr_text_line("hello ; world") == "hello ; world"
    assert cleanup_ocr_text_line("hello : world") == "hello : world"
    assert cleanup_ocr_text_line("hello !") == "hello !"
    assert cleanup_ocr_text_line("hello ?") == "hello ?"


def test_cleanup_preserves_no_space_after_punctuation():
    """Punctuation cleanup was removed: punctuation followed by alnum is
    left alone, not spaced.  The old insert rule guessed semantics from a
    character class and rewrote "3.14" -> "3. 14", "v1.0" -> "v1. 0"."""
    assert cleanup_ocr_text_line("hello.World") == "hello.World"
    assert cleanup_ocr_text_line("end,begin") == "end,begin"
    assert cleanup_ocr_text_line("ok;next") == "ok;next"
    assert cleanup_ocr_text_line("3.14") == "3.14"
    assert cleanup_ocr_text_line("v1.0") == "v1.0"


def test_cleanup_protects_url_from_dot_spacing():
    """Regression: the dot/colon spacers must not split a URL.

    Previously ``https://www.deepseek.com`` was rewritten to
    ``https://www. deepseek. com`` (a space inserted after every dot), so the
    link highlighter — which stops at whitespace — only matched ``https://www.``
    and the Ctrl+Click hover tooltip never fired over the rest of the URL.
    """
    assert cleanup_ocr_text_line("https://www.deepseek.com") == "https://www.deepseek.com"
    # The full URL survives as a single extractable link.
    assert extract_urls(cleanup_ocr_text_line("https://www.deepseek.com")) == ["https://www.deepseek.com"]


def test_cleanup_protects_url_but_leaves_surrounding_text():
    """With punctuation cleanup removed, the non-URL runs around a URL are
    returned as-is (no tightening of "today . ok").  The URL itself still
    survives intact and extractable."""
    cleaned = cleanup_ocr_text_line("Visit https://www.deepseek.com today . ok")
    assert cleaned == "Visit https://www.deepseek.com today . ok"
    assert extract_urls(cleaned) == ["https://www.deepseek.com"]


def test_cleanup_protects_url_with_port_query_and_fragment():
    """Colons (port), query and fragment punctuation inside a URL are preserved."""
    url = "https://example.com:8080/path?q=a,b&x=1#frag-ment"
    assert cleanup_ocr_text_line(url) == url
    assert extract_urls(cleanup_ocr_text_line(url)) == [url]


def test_cleanup_protects_url_with_cjk_query_value():
    """A URL whose query value is CJK survives cleanup intact and extractable.

    Regression for the tieba case: ``kw=测试页面&fr=pb`` must stay a single URL.
    """
    url = "https://tieba.baidu.com/f?kw=测试页面&fr=pb"
    assert cleanup_ocr_text_line(url) == url
    assert extract_urls(cleanup_ocr_text_line(url)) == [url]


def test_apply_outside_urls_leaves_url_runs_untouched():
    """apply_outside_urls runs the transform only on non-URL spans."""
    # Upper-case the non-URL text; URL stays exactly as-is.
    out = apply_outside_urls("see https://x.com/y?z=1 end", str.upper)
    assert out == "SEE https://x.com/y?z=1 END"


def test_apply_outside_urls_no_url_runs_transform_on_all():
    assert apply_outside_urls("hello.world", lambda s: s.replace(".", " ")) == "hello world"


def test_apply_outside_urls_empty_and_none():
    assert apply_outside_urls("", str.upper) == ""
    with pytest.raises(TypeError):
        apply_outside_urls(None, str.upper)


def test_cleanup_empty():
    assert cleanup_ocr_text_line("") == ""
    # cleanup_ocr_text_line is now identity — None flows through unchanged.
    assert cleanup_ocr_text_line(None) is None


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
    # Trailing spaces are NOT stripped here (deferred to normalize_ocr_text /
    # finalize); \n is preserved (paragraph marker).
    assert compose_default_line_text(_line("hello world  ")) == "hello world  "
    assert compose_default_line_text(_line("Para 1\n")) == "Para 1\n"


def test_compose_spaced_line_text():
    assert compose_spaced_line_text(_line("hello world  ")) == "hello world  "


def test_compose_cjk_line_text():
    """CJK line text preserves trailing spaces/tabs and \\n paragraph markers;
    stripping is deferred to normalize_ocr_text (finalize)."""
    assert compose_cjk_line_text(_line("中文测试  ")) == "中文测试  "
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

def test_postprocess_preserves_multiple_blank_lines():
    """The 3+-newline compression was removed: the PP-OCR pipeline never
    produces 3+ consecutive newlines (paragraph_break inserts at most one
    sentinel between any two lines, and line.text contains no \\n), so the
    rule was a no-op.  Multiple blank lines now pass through unchanged."""
    text = "a\n\n\n\nb"
    result = _postprocess_layout_text(text)
    assert result == "a\n\n\n\nb"


def test_postprocess_keeps_single_blank_line():
    assert _postprocess_layout_text("a\n\nb") == "a\n\nb"


def test_postprocess_strips_trailing_newlines():
    assert _postprocess_layout_text("a\nb\n\n") == "a\nb"


def test_postprocess_whitespace_only_lines_become_empty():
    text = "a\n   \nb"
    result = _postprocess_layout_text(text)
    assert result == "a\n\nb"


def test_postprocess_preserves_indentation():
    """Non-empty lines keep their leading whitespace during line processing, including the first line."""
    text = "  first line\n  middle line\n  last line"
    result = _postprocess_layout_text(text)
    # First line and all middle/last lines keep their indent
    assert "  first line" in result
    assert "  middle line" in result
    assert "  last line" in result


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


def test_compose_text_paragraph_break_line_survives_filter():
    """A line with paragraph_break=True survives the empty-line filter
    even though its text is empty — this is the layout engine's explicit
    paragraph separator (gap >= 1× line height)."""
    result = OcrRecognition(
        lines=[
            OcrLine(text="Para 1", bounding_box=OcrBox(0, 0, 100, 20)),
            OcrLine(text="", bounding_box=OcrBox(), paragraph_break=True),
            OcrLine(text="Para 2", bounding_box=OcrBox(0, 100, 100, 20)),
        ]
    )
    text = compose_text_from_result(result, language_tag="en-US")
    assert text == "Para 1\n\nPara 2"


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


def test_compose_text_default_adapter_preserves_punctuation_spacing():
    """Default adapter no longer tightens punctuation spacing - the layout
    engine's spaces (gap/indent) must survive through compose."""
    result = OcrRecognition(
        lines=[
            OcrLine(text="hello . world", bounding_box=OcrBox(0, 0, 100, 20)),
        ]
    )
    text = compose_text_from_result(result, language_tag="fr-FR")
    assert text == "hello . world"


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
    """A trailing \\n on a line survives through the compose pipeline
    and creates a paragraph break in the final text.
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


# ═══════════════════════════════════════════════════════════════════════
# URL detection — extract_urls / find_url_at_position / _iter_url_spans
# Shared by the OCR popup link highlighter and Ctrl+Click handling, so the
# "what gets coloured" and "what gets opened" must agree exactly.
# ═══════════════════════════════════════════════════════════════════════

class TestUrlExtraction:
    def test_basic_https(self):
        assert extract_urls("See https://example.com/path?q=1") == ["https://example.com/path?q=1"]

    def test_http_scheme(self):
        assert extract_urls("http://example.com") == ["http://example.com"]

    def test_case_insensitive_scheme(self):
        assert extract_urls("HTTPS://example.com") == ["HTTPS://example.com"]

    def test_no_scheme_not_a_link(self):
        # Bare domains are not highlighted — avoids false positives on OCR noise.
        assert extract_urls("visit example.com today") == []

    def test_dedup_preserves_order(self):
        text = "https://a.com and https://b.com and https://a.com again"
        assert extract_urls(text) == ["https://a.com", "https://b.com"]

    def test_multiple_on_one_line(self):
        text = "https://a.com / https://b.com"
        assert extract_urls(text) == ["https://a.com", "https://b.com"]

    def test_strips_trailing_ascii_punctuation(self):
        # OCR/text often has a comma/period/closing paren right after a URL.
        assert extract_urls("go to https://example.com.") == ["https://example.com"]
        assert extract_urls("(see https://example.com)") == ["https://example.com"]
        assert extract_urls("https://example.com, https://other.com;") == [
            "https://example.com", "https://other.com"
        ]

    def test_strips_trailing_cjk_punctuation(self):
        # Chinese/Japanese OCR frequently appends 。）after a URL.
        assert extract_urls("访问https://example.com。") == ["https://example.com"]
        assert extract_urls("（https://example.com）") == ["https://example.com"]

    def test_keeps_internal_punctuation(self):
        # Query strings, fragments, ports, paths must survive.
        url = "https://example.com:8080/path?q=a,b&x=1#frag-ment"
        assert extract_urls(url) == [url]

    def test_empty_and_none(self):
        assert extract_urls("") == []
        assert extract_urls("no links here at all") == []

    def test_url_does_not_cross_newline(self):
        # A URL broken across lines (rare in OCR) is treated per-line by callers;
        # the regex stops at whitespace, and \n is whitespace.
        text = "https://example.com\nmore text"
        assert extract_urls(text) == ["https://example.com"]


class TestFindUrlAtPosition:
    def test_inside_url(self):
        text = "See https://example.com now"
        # positions 4..22 cover "https://example.com"
        assert find_url_at_position(text, 4) == "https://example.com"
        assert find_url_at_position(text, 10) == "https://example.com"
        assert find_url_at_position(text, 21) == "https://example.com"

    def test_outside_url(self):
        text = "See https://example.com now"
        assert find_url_at_position(text, 0) is None
        assert find_url_at_position(text, 3) is None  # space before url
        # URL occupies indices 4..22 (len 19); index 23 is the space after.
        assert find_url_at_position(text, 23) is None
        assert find_url_at_position(text, 26) is None

    def test_trailing_punct_not_part_of_span(self):
        # The clickable span excludes the trailing period, so a click on the
        # period must NOT register as a URL hit.
        text = "https://example.com."
        url_len = len("https://example.com")
        assert find_url_at_position(text, url_len - 1) == "https://example.com"
        assert find_url_at_position(text, url_len) is None  # the period itself

    def test_negative_position(self):
        assert find_url_at_position("https://example.com", -1) is None

    def test_position_at_end_boundary(self):
        text = "https://example.com"
        assert find_url_at_position(text, len(text) - 1) == "https://example.com"
        assert find_url_at_position(text, len(text)) is None


class TestIterUrlSpansAgreement:
    def test_spans_match_extracted_urls(self):
        text = "a https://x.com, and https://y.com."
        spans = [(s, e, text[s:e]) for s, e, _ in _iter_url_spans(text)]
        # The slice from the span must equal the extracted url (no trailing junk)
        assert [u for _, _, u in spans] == ["https://x.com", "https://y.com"]
        # And match extract_urls output
        assert [u for _, _, u in spans] == extract_urls(text)


# ═══════════════════════════════════════════════════════════════════════
# OCR-dropped slash — single-slash https:/ is still a link, restored on open
# ═══════════════════════════════════════════════════════════════════════

class TestSingleSlashUrl:
    def test_single_slash_is_detected(self):
        # OCR dropped one slash: https:/ instead of https://
        text = "https:/tieba.baidu.com/p/10824444531?fr=personalize_page"
        assert extract_urls(text) == [text]

    def test_single_slash_hit_test(self):
        text = "see https:/tieba.baidu.com/p/10824444531 here"
        idx = text.find("https:/")
        assert find_url_at_position(text, idx + 5) == "https:/tieba.baidu.com/p/10824444531"

    def test_cleanup_does_not_split_single_slash_url(self):
        # Regression: because the URL wasn't recognised, cleanup_ocr_text_line
        # used to insert a space after every dot -> "tieba. baidu. com".
        raw = "https:/tieba.baidu.com/p/10824444531?fr=personalize_page"
        assert cleanup_ocr_text_line(raw) == raw
        assert extract_urls(cleanup_ocr_text_line(raw)) == [raw]


class TestNormalizeUrl:
    def test_restores_missing_slash(self):
        assert normalize_url("https:/tieba.baidu.com/p/1") == "https://tieba.baidu.com/p/1"
        assert normalize_url("http:/example.com") == "http://example.com"

    def test_double_slash_unchanged(self):
        assert normalize_url("https://example.com/path?q=1") == "https://example.com/path?q=1"
        assert normalize_url("http://example.com") == "http://example.com"

    def test_case_insensitive_scheme(self):
        assert normalize_url("HTTPS:/example.com") == "HTTPS://example.com"
