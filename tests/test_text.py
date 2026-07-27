"""
Unit tests for OCR text utilities: URL detection / highlight agreement.

The former language-adapter / compose / normalize layer was removed - the
layout engine (ppocr.compose_ppocr_structures) now owns all text mutation and
emits clean text, so OcrResponse.text is consumed directly.  What remains in
ocr.text is the URL toolkit shared by the popup highlighter and Ctrl+Click
handling.
"""

import pytest

from hushsnap.ocr.text import (
    apply_outside_urls,
    extract_urls,
    find_url_at_position,
    normalize_url,
    _iter_url_spans,
)


# ═══════════════════════════════════════════════════════════════════════
# apply_outside_urls
# ═══════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════
# URL detection - extract_urls / find_url_at_position / _iter_url_spans
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
        # Bare domains are not highlighted - avoids false positives on OCR noise.
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
# OCR-dropped slash - single-slash https:/ is still a link, restored on open
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

    def test_single_slash_url_extracted_intact(self):
        # Regression: a single-slash https:/ URL must still be recognised and
        # extracted as one piece (the missing slash is restored only at open
        # time by normalize_url).  No cleanup layer rewrites it.
        raw = "https:/tieba.baidu.com/p/10824444531?fr=personalize_page"
        assert extract_urls(raw) == [raw]


class TestNormalizeUrl:
    def test_restores_missing_slash(self):
        assert normalize_url("https:/tieba.baidu.com/p/1") == "https://tieba.baidu.com/p/1"
        assert normalize_url("http:/example.com") == "http://example.com"

    def test_double_slash_unchanged(self):
        assert normalize_url("https://example.com/path?q=1") == "https://example.com/path?q=1"
        assert normalize_url("http://example.com") == "http://example.com"

    def test_case_insensitive_scheme(self):
        assert normalize_url("HTTPS:/example.com") == "HTTPS://example.com"
