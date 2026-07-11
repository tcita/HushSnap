"""
Unit tests for the OCR JSON parsing module.
Covers PascalCase/camelCase flexibility, box computation from words,
and edge cases (None, missing keys, empty collections).
"""

import pytest

from hushsnap.ocr.parsing import (
    compute_line_box,
    parse_box,
    parse_line,
    parse_word,
)
from hushsnap.ocr.models import OcrBox, OcrLine, OcrWord


# ═══════════════════════════════════════════════════════════════════════
# parse_box
# ═══════════════════════════════════════════════════════════════════════

def test_parse_box_pascal_case():
    box = parse_box({"X": 10, "Y": 20, "Width": 100, "Height": 50})
    assert box.x == 10.0
    assert box.y == 20.0
    assert box.width == 100.0
    assert box.height == 50.0


def test_parse_box_camel_case():
    box = parse_box({"x": 5, "y": 15, "width": 80, "height": 40})
    assert box.x == 5.0
    assert box.y == 15.0
    assert box.width == 80.0
    assert box.height == 40.0


def test_parse_box_mixed_case_prefers_pascal():
    """When both PascalCase and camelCase keys are present, PascalCase wins
    (because dict.get checks X before x)."""
    box = parse_box({"X": 10, "x": 99, "Height": 50, "height": 99})
    assert box.x == 10.0  # PascalCase wins
    assert box.height == 50.0


def test_parse_box_partial_fields():
    box = parse_box({"x": 7})
    assert box.x == 7.0
    assert box.y == 0.0
    assert box.width == 0.0
    assert box.height == 0.0


def test_parse_box_none_and_empty():
    assert parse_box(None) == OcrBox()
    assert parse_box({}) == OcrBox()
    assert parse_box("not a dict") == OcrBox()


def test_parse_box_null_values():
    """None values in JSON should default to 0.0."""
    box = parse_box({"x": None, "y": None, "width": None, "height": None})
    assert box.x == 0.0
    assert box.y == 0.0
    assert box.width == 0.0
    assert box.height == 0.0


def test_parse_box_string_numbers():
    """Numeric strings should be converted to float (via dict.get returning
    string, then float() casting)."""
    # Actually float("10.5") works on strings, so string values should parse
    box = parse_box({"x": "10.5", "y": "20.7"})
    assert box.x == 10.5
    assert box.y == 20.7


# ═══════════════════════════════════════════════════════════════════════
# compute_line_box
# ═══════════════════════════════════════════════════════════════════════

def test_compute_line_box_empty():
    assert compute_line_box([]) == OcrBox()


def test_compute_line_box_single_word():
    words = [OcrWord(text="hello", bounding_box=OcrBox(10, 20, 100, 30))]
    box = compute_line_box(words)
    assert box.x == 10.0
    assert box.y == 20.0
    assert box.width == 100.0
    assert box.height == 30.0


def test_compute_line_box_multiple_words():
    words = [
        OcrWord(text="hello", bounding_box=OcrBox(10, 20, 50, 30)),
        OcrWord(text="world", bounding_box=OcrBox(70, 15, 60, 40)),
    ]
    box = compute_line_box(words)
    assert box.x == 10.0   # leftmost
    assert box.y == 15.0   # topmost
    assert box.width == 120.0  # (70+60) - 10 = 120
    # bottom = max(20+30=50, 15+40=55) = 55, height = 55-15 = 40
    assert box.height == 40.0


def test_compute_line_box_negative_handling():
    """If words have unexpected negative bounds, width/height should be ≥ 0."""
    words = [
        OcrWord(text="a", bounding_box=OcrBox(100, 100, -50, -30)),
    ]
    box = compute_line_box(words)
    # right = 100 + (-50) = 50, which is < left (100), so width should be 0 via max(0, ...)
    assert box.width == 0.0
    assert box.height == 0.0


# ═══════════════════════════════════════════════════════════════════════
# parse_word
# ═══════════════════════════════════════════════════════════════════════

def test_parse_word_basic():
    word = parse_word({"Text": "hello", "BoundingBox": {"x": 0, "y": 0, "width": 50, "height": 20}})
    assert word is not None
    assert word.text == "hello"
    assert word.bounding_box.width == 50.0


def test_parse_word_missing_text():
    word = parse_word({"BoundingBox": {"x": 0, "y": 0, "width": 50, "height": 20}})
    assert word.text == ""


def test_parse_word_missing_bounding_box():
    word = parse_word({"Text": "hello"})
    assert word.bounding_box == OcrBox()


def test_parse_word_none_and_invalid():
    assert parse_word(None) is None
    assert parse_word("not a dict") is None
    assert parse_word(42) is None


# ═══════════════════════════════════════════════════════════════════════
# parse_line
# ═══════════════════════════════════════════════════════════════════════

def test_parse_line_basic():
    line = parse_line({
        "Text": "hello world",
        "BoundingBox": {"x": 10, "y": 20, "width": 200, "height": 30},
        "Words": [
            {"Text": "hello", "BoundingBox": {"x": 10, "y": 20, "width": 80, "height": 30}},
            {"Text": "world", "BoundingBox": {"x": 100, "y": 20, "width": 90, "height": 30}},
        ],
    })
    assert line is not None
    assert line.text == "hello world"
    assert line.bounding_box.x == 10.0
    assert len(line.words) == 2
    assert line.words[0].text == "hello"
    assert line.words[1].text == "world"


def test_parse_line_no_bounding_box_backfills_from_words():
    """When line has no valid bounding box, compute from word boxes."""
    line = parse_line({
        "Text": "hello world",
        "Words": [
            {"Text": "hello", "BoundingBox": {"x": 10, "y": 20, "width": 80, "height": 30}},
            {"Text": "world", "BoundingBox": {"x": 100, "y": 20, "width": 90, "height": 30}},
        ],
    })
    # box computed from words: left=10, top=20, width=180, height=30
    assert line.bounding_box.x == 10.0
    assert line.bounding_box.y == 20.0
    assert line.bounding_box.width == 180.0
    assert line.bounding_box.height == 30.0


def test_parse_line_zero_sized_bounding_box_triggers_backfill():
    """A bounding box with width=0 and height=0 should trigger word backfill."""
    line = parse_line({
        "Text": "hello",
        "BoundingBox": {"x": 0, "y": 0, "width": 0, "height": 0},
        "Words": [
            {"Text": "hello", "BoundingBox": {"x": 5, "y": 5, "width": 50, "height": 20}},
        ],
    })
    assert line.bounding_box.x == 5.0
    assert line.bounding_box.width == 50.0


def test_parse_line_empty_words():
    line = parse_line({
        "Text": "hello",
        "BoundingBox": {"x": 10, "y": 20, "width": 200, "height": 30},
        "Words": [],
    })
    assert line.words == []
    assert line.bounding_box.x == 10.0


def test_parse_line_missing_text():
    line = parse_line({
        "BoundingBox": {"x": 0, "y": 0, "width": 100, "height": 20},
    })
    assert line.text == ""


def test_parse_line_none_and_invalid():
    assert parse_line(None) is None
    assert parse_line("not a dict") is None


def test_parse_line_words_is_none():
    line = parse_line({
        "Text": "hello",
        "BoundingBox": {"x": 0, "y": 0, "width": 100, "height": 20},
        "Words": None,
    })
    assert line.words == []


def test_parse_line_words_contains_invalid_entries():
    """Invalid entries in Words list are skipped."""
    line = parse_line({
        "Text": "valid",
        "BoundingBox": {"x": 0, "y": 0, "width": 100, "height": 20},
        "Words": [
            {"Text": "ok", "BoundingBox": {"x": 0, "y": 0, "width": 40, "height": 20}},
            None,
            "bad",
            {"Text": "also ok", "BoundingBox": {"x": 50, "y": 0, "width": 60, "height": 20}},
        ],
    })
    assert len(line.words) == 2
    assert line.words[0].text == "ok"
    assert line.words[1].text == "also ok"
