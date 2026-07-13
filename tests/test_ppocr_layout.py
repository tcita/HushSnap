"""Tests for PP-OCR layout pipeline — pure functions, no ONNX / Qt.

Coverage:
  - _greedy_line_cluster   (horizontal clustering + anti-bridging)
  - _greedy_column_cluster (vertical clustering + anti-bridging)
  - _normalize_blocks      (empty / invalid-box filtering)
  - _is_vertical_json      (tall-box area-weighted voting)
  - _apply_indentation     (single- / multi-level indent detection)
  - compose_ppocr_structures (integration, horizontal + vertical)
  - ppocr_box_to_bbox      (coordinate parsing edge cases)
  - word_separator         (CJK / Latin / punctuation boundaries)
  - is_cjk_or_fullwidth    (Unicode block membership)
  - _apply_cjk_spacing     (pangu-style CJK↔Latin spacing)
"""

import pytest
from hushsnap.ocr.ppocr import (
    _greedy_line_cluster,
    _greedy_column_cluster,
    _normalize_blocks,
    _is_vertical_json,
    _apply_indentation,
    compose_ppocr_structures,
    ppocr_box_to_bbox,
    word_separator,
    is_cjk_or_fullwidth,
    _apply_cjk_spacing,
)
from hushsnap.ocr.models import OcrBox, OcrLine


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _box(text, top, bottom, left, right, height=None, width=None,
         center_x=None, center_y=None):
    """Create a normalized block dict matching _normalize_blocks output."""
    h = height if height is not None else bottom - top
    w = width if width is not None else right - left
    return {
        "text": text,
        "top": float(top), "bottom": float(bottom),
        "left": float(left), "right": float(right),
        "width": float(w), "height": float(h),
        "center_x": center_x if center_x is not None else (left + right) / 2,
        "center_y": center_y if center_y is not None else (top + bottom) / 2,
    }


def _raw_block(text, box_points):
    """Create a raw PP-OCR block dict (as from engine output)."""
    return {"text": text, "box": box_points}


def _line(text, x, y, w, h):
    """Create an OcrLine with bounding box."""
    return OcrLine(text=text, bounding_box=OcrBox(x=x, y=y, width=w, height=h))


# ===================================================================
# _greedy_line_cluster
# ===================================================================

class TestGreedyLineCluster:
    """Horizontal LTR line clustering with anti-bridging guard."""

    def test_same_line_cjk_merged(self):
        """Two CJK characters with high vertical overlap -> same line."""
        blocks = [
            _box("鬼",   top=10, bottom=46, left=80,  right=116),
            _box("入侵", top=9,  bottom=47, left=99,  right=169),
        ]
        lines = _greedy_line_cluster(blocks)
        assert len(lines) == 1
        assert [b["text"] for b in lines[0]] == ["鬼", "入侵"]

    def test_different_lines_split(self):
        """CJK line above, short English line below -> two lines."""
        blocks = [
            _box("鬼",   top=10, bottom=46, left=80,  right=116),
            _box("入侵", top=9,  bottom=47, left=99,  right=169),
            _box("THE",  top=42, bottom=54, left=26,  right=223),
        ]
        lines = _greedy_line_cluster(blocks)
        assert len(lines) == 2
        # Chinese line first (top)
        assert [b["text"] for b in lines[0]] == ["鬼", "入侵"]
        # English line second (bottom)
        assert [b["text"] for b in lines[1]] == ["THE"]

    def test_anti_bridging_overlap_passes_center_rejects(self):
        """Overlap > 0.5 but centre distance too large -> anti-bridging splits.

        English text shifted up so its top edge overlaps >50 % of its height
        with the tall CJK box's bottom.  Without the centre check this would
        merge into one line and left-to-right sort would put English first.

        overlap = min(47,52) - max(9,40) = 7 px, min_h = min(38,12) = 12
        → ratio = 7/12 = 0.583 > 0.5  ✓ first gate
        |centre_y(46) - median(28)| = 18
        0.4 × median_h(38) = 15.2
        → 18 >= 15.2  ✗ second gate — ANTI-BRIDGING SPLITS
        """
        blocks = [
            _box("鬼",   top=10, bottom=46, left=80,  right=116),  # center_y=28
            _box("入侵", top=9,  bottom=47, left=99,  right=169),  # center_y=28
            _box("THE",  top=40, bottom=52, left=26,  right=223),  # center_y=46
        ]
        lines = _greedy_line_cluster(blocks)
        assert len(lines) == 2, "anti-bridging should split despite overlap > 0.5"
        assert "鬼" in [b["text"] for b in lines[0]]

    def test_drop_cap_same_line(self):
        """Tall drop cap at line start merges with body text on same baseline."""
        blocks = [
            _box("T",     top=10, bottom=58, left=5,  right=30,  height=48),
            _box("he",    top=30, bottom=44, left=35, right=55,  height=14),
            _box("quick", top=30, bottom=44, left=60, right=105, height=14),
        ]
        lines = _greedy_line_cluster(blocks)
        assert len(lines) == 1
        # Within-line: sorted left -> right
        assert [b["text"] for b in lines[0]] == ["T", "he", "quick"]

    def test_empty_input(self):
        assert _greedy_line_cluster([]) == []

    def test_single_block(self):
        lines = _greedy_line_cluster([_box("A", 0, 12, 0, 10)])
        assert len(lines) == 1
        assert len(lines[0]) == 1
        assert lines[0][0]["text"] == "A"

    def test_three_clear_lines(self):
        """Three well-separated text lines -> three clusters."""
        blocks = [
            _box("Line1", top=0,  bottom=12, left=0, right=40),
            _box("Line2", top=30, bottom=42, left=0, right=40),
            _box("Line3", top=60, bottom=72, left=0, right=40),
        ]
        lines = _greedy_line_cluster(blocks)
        assert len(lines) == 3

    def test_within_line_sorted_left_to_right(self):
        """Boxes on the same line are sorted by x_left."""
        blocks = [
            _box("C", top=0, bottom=14, left=200, right=220),
            _box("A", top=0, bottom=14, left=0,   right=20),
            _box("B", top=0, bottom=14, left=100, right=120),
        ]
        lines = _greedy_line_cluster(blocks)
        assert [b["text"] for b in lines[0]] == ["A", "B", "C"]

    def test_lines_sorted_top_to_bottom(self):
        """Lines are sorted by average y_center."""
        blocks = [
            _box("Bottom", top=50, bottom=62, left=0, right=50),
            _box("Top",    top=0,  bottom=12, left=0, right=50),
        ]
        lines = _greedy_line_cluster(blocks)
        assert lines[0][0]["text"] == "Top"
        assert lines[1][0]["text"] == "Bottom"


# ===================================================================
# _greedy_column_cluster
# ===================================================================

class TestGreedyColumnCluster:
    """Vertical RTL column clustering with anti-bridging guard."""

    def test_same_column_merged(self):
        """Two vertically stacked boxes -> same column."""
        blocks = [
            _box("A", top=0,  bottom=14, left=0,  right=20),
            _box("B", top=30, bottom=44, left=0,  right=20),
        ]
        columns = _greedy_column_cluster(blocks)
        assert len(columns) == 1
        # Within-column: sorted top -> bottom
        assert [b["text"] for b in columns[0]] == ["A", "B"]

    def test_different_columns_split(self):
        """Two side-by-side columns -> two columns, right -> left order."""
        blocks = [
            _box("右", top=0,  bottom=14, left=100, right=120),
            _box("左", top=0,  bottom=14, left=0,   right=20),
        ]
        columns = _greedy_column_cluster(blocks)
        assert len(columns) == 2
        # Rightmost column first (RTL)
        assert columns[0][0]["text"] == "右"
        assert columns[1][0]["text"] == "左"

    def test_anti_bridging_width(self):
        """A wide box does not bridge a distant narrow column.

        The wide box (right=200) sorts first.  The narrow box overlaps
        it horizontally (20 px out of 20 → ratio 1.0) but its centre is
        far right of the wide box's centre, scaled by the wide width.

        overlap = min(200,160) - max(0,140) = 20 px, min_w = min(200,20) = 20
        → ratio = 1.0 > 0.5  ✓ first gate
        |centre_x(150) - median(100)| = 50
        0.4 × median_w(200) = 80
        → 50 < 80  — passes (not a strong anti-bridging case)

        For a real rejection we need the centre difference to exceed
        0.4 × median_w.  Use two equal-width columns far apart with a
        bridging wide element.
        """
        blocks = [
            # Narrow first column (sorts second by right=20)
            _box("colA", top=0,  bottom=50, left=0,  right=20),
            # Wide element that spans both (sorts first by right=300)
            _box("wide", top=10, bottom=14, left=0,  right=300),
            # Narrow second column, overlaps wide but centre is far
            _box("colB", top=0,  bottom=50, left=280, right=300),
        ]
        columns = _greedy_column_cluster(blocks)
        # "wide" should NOT bridge colA and colB together.
        # colB (right=300) and wide (right=300) sort near each other;
        # colA (right=20) is far left.
        assert len(columns) >= 2, (
            "anti-bridging should keep columns separate despite wide element"
        )

    def test_empty_input(self):
        assert _greedy_column_cluster([]) == []

    def test_columns_sorted_right_to_left(self):
        """Columns are ordered right-to-left by average x_center."""
        blocks = [
            _box("A", top=0, bottom=14, left=0,   right=20),   # leftmost
            _box("B", top=0, bottom=14, left=100, right=120),  # rightmost
        ]
        columns = _greedy_column_cluster(blocks)
        assert columns[0][0]["text"] == "B"  # rightmost first
        assert columns[1][0]["text"] == "A"  # leftmost second


# ===================================================================
# _normalize_blocks
# ===================================================================

class TestNormalizeBlocks:

    def test_valid_blocks_preserved(self):
        blocks = [
            _raw_block("hello", [[0, 0], [10, 0], [10, 12], [0, 12]]),
            _raw_block("world", [[20, 0], [50, 0], [50, 12], [20, 12]]),
        ]
        result = _normalize_blocks(blocks)
        assert len(result) == 2
        assert result[0]["text"] == "hello"
        assert result[1]["text"] == "world"

    def test_empty_text_filtered(self):
        blocks = [
            _raw_block("",    [[0, 0], [10, 0], [10, 12], [0, 12]]),
            _raw_block("   ", [[0, 0], [10, 0], [10, 12], [0, 12]]),
            _raw_block("ok",  [[0, 0], [10, 0], [10, 12], [0, 12]]),
        ]
        result = _normalize_blocks(blocks)
        assert len(result) == 1
        assert result[0]["text"] == "ok"

    def test_invalid_bbox_filtered(self):
        blocks = [
            _raw_block("zero_w", [[0, 0], [0, 0], [0, 12], [0, 12]]),
            _raw_block("zero_h", [[0, 0], [10, 0], [10, 0], [0, 0]]),
            _raw_block("ok",     [[0, 0], [10, 0], [10, 12], [0, 12]]),
        ]
        result = _normalize_blocks(blocks)
        assert len(result) == 1
        assert result[0]["text"] == "ok"

    def test_mixed_blocks(self):
        blocks = [
            _raw_block("",       [[0, 0], [10, 0], [10, 12], [0, 12]]),  # empty
            _raw_block("no_box", []),                                      # invalid box
            _raw_block("keep",   [[0, 0], [10, 0], [10, 12], [0, 12]]),  # valid
        ]
        result = _normalize_blocks(blocks)
        assert len(result) == 1
        assert result[0]["text"] == "keep"

    def test_none_input(self):
        assert _normalize_blocks(None) == []

    def test_empty_list(self):
        assert _normalize_blocks([]) == []


# ===================================================================
# _is_vertical_json
# ===================================================================

class TestIsVerticalJson:

    def test_all_tall_boxes(self):
        """All boxes are taller than wide -> vertical."""
        data = [
            {"box": [[0, 0], [14, 0], [14, 50], [0, 50]]},  # 14×50, ratio 3.6
            {"box": [[20, 0], [34, 0], [34, 48], [20, 48]]}, # 14×48, ratio 3.4
        ]
        assert _is_vertical_json(data) is True

    def test_all_wide_boxes(self):
        """All boxes are wider than tall -> horizontal."""
        data = [
            {"box": [[0, 0], [100, 0], [100, 14], [0, 14]]},
            {"box": [[0, 20], [80, 20], [80, 34], [0, 34]]},
        ]
        assert _is_vertical_json(data) is False

    def test_mixed_but_horizontal_dominant(self):
        """One tall box among many wide ones -> horizontal (area-weighted)."""
        data = [
            {"box": [[0, 0], [100, 0], [100, 14], [0, 14]]},   # wide:   1400 px²
            {"box": [[0, 20], [100, 0], [100, 14], [0, 34]]},  # wide:   1400 px² -- hmm this is wrong
            # Let me use proper coordinates
        ]
        # Redo: tall box 14×50=700 px², wide box 100×14=1400 px²
        # tall/total = 700/2100 = 0.33 < 0.5 -> horizontal
        data2 = [
            {"box": [[0, 0], [14, 0], [14, 50], [0, 50]]},     # 14×50=700, tall
            {"box": [[20, 0], [120, 0], [120, 14], [20, 14]]}, # 100×14=1400, wide
        ]
        assert _is_vertical_json(data2) is False

    def test_mixed_vertical_dominant(self):
        """Tall boxes dominate by area -> vertical."""
        data = [
            {"box": [[0, 0], [14, 0], [14, 50], [0, 50]]},     # 14×50=700, tall
            {"box": [[20, 0], [34, 0], [34, 50], [20, 50]]},   # 14×50=700, tall
            {"box": [[40, 0], [60, 0], [60, 14], [40, 14]]},   # 20×14=280, wide
        ]
        # tall/total = 1400/1680 = 0.83 > 0.5 -> vertical
        assert _is_vertical_json(data) is True

    def test_empty(self):
        assert _is_vertical_json([]) is False
        assert _is_vertical_json(None) is False

    def test_zero_width_boxes_ignored(self):
        data = [
            {"box": [[0, 0], [0, 0], [0, 50], [0, 50]]},  # zero width -> skipped
            {"box": [[10, 0], [24, 0], [24, 50], [10, 50]]},  # 14×50=700, tall
        ]
        assert _is_vertical_json(data) is True


# ===================================================================
# _apply_indentation
# ===================================================================

class TestApplyIndentation:

    def test_single_line_no_indent(self):
        lines = [_line("hello", x=0, y=0, w=40, h=14)]
        result = _apply_indentation(lines)
        assert result[0].text == "hello"

    def test_indented_line(self):
        """One line is offset from the left baseline -> gets leading spaces."""
        lines = [
            _line("body",  x=0,  y=0,  w=40, h=14),  # baseline
            _line("indent", x=40, y=20, w=40, h=14),  # offset
        ]
        result = _apply_indentation(lines)
        # indent unit = 40, level = 1 -> 4 spaces
        assert result[1].text.startswith("    ")
        assert "indent" in result[1].text

    def test_multi_level_indent(self):
        """Two different indent levels."""
        lines = [
            _line("L0", x=0,  y=0,  w=30, h=14),   # baseline
            _line("L1", x=20, y=20, w=30, h=14),   # level 1
            _line("L2", x=40, y=40, w=30, h=14),   # level 2
        ]
        result = _apply_indentation(lines)
        assert result[1].text.startswith("    ")      # 4 spaces
        assert result[2].text.startswith("        ")  # 8 spaces

    def test_no_significant_offset(self):
        """Small offsets within jitter threshold -> no indent."""
        lines = [
            _line("L0", x=0, y=0, w=30, h=14),
            _line("L1", x=3, y=20, w=30, h=14),  # 3px < 0.5*14=7 -> jitter
        ]
        result = _apply_indentation(lines)
        assert not result[1].text.startswith(" ")

    def test_all_same_x(self):
        """All lines at same x -> no indent applied."""
        lines = [
            _line("A", x=10, y=0,  w=30, h=14),
            _line("B", x=10, y=20, w=30, h=14),
        ]
        result = _apply_indentation(lines)
        assert result[0].text == "A"
        assert result[1].text == "B"


# ===================================================================
# compose_ppocr_structures (integration)
# ===================================================================

class TestComposePpocrStructures:

    def test_horizontal_integration(self):
        """Full pipeline: raw blocks -> clustered, sorted, spaced OcrLines."""
        blocks = [
            _raw_block("鬼",  [[80, 10], [116, 10], [116, 46], [80, 46]]),
            _raw_block("入侵", [[99, 9],  [169, 9],  [169, 47], [99, 47]]),
            _raw_block("THE", [[26, 42], [223, 42], [223, 54], [26, 54]]),
        ]
        lines = compose_ppocr_structures(blocks, is_vertical=False)
        assert len(lines) == 2
        # First line: CJK (top)
        assert "鬼" in lines[0].text
        assert "入侵" in lines[0].text

    def test_vertical_integration(self):
        """Vertical CJK: raw blocks -> column-clustered OcrLines."""
        blocks = [
            _raw_block("一", [[0, 0],  [14, 0],  [14, 50], [0, 50]]),
            _raw_block("二", [[0, 60], [14, 60], [14, 110], [0, 110]]),
            _raw_block("三", [[20, 0], [34, 0],  [34, 50], [20, 50]]),
        ]
        lines = compose_ppocr_structures(blocks, is_vertical=True)
        assert len(lines) >= 1

    def test_empty_blocks(self):
        assert compose_ppocr_structures([], is_vertical=False) == []
        assert compose_ppocr_structures(None, is_vertical=False) == []

    def test_junk_filtered(self):
        """Empty-text and invalid-bbox blocks are silently dropped."""
        blocks = [
            _raw_block("",    [[0, 0], [10, 0], [10, 12], [0, 12]]),
            _raw_block("ok",  [[0, 0], [20, 0], [20, 12], [0, 12]]),
        ]
        lines = compose_ppocr_structures(blocks)
        assert len(lines) == 1
        assert lines[0].text == "ok"


# ===================================================================
# ppocr_box_to_bbox
# ===================================================================

class TestPpocrBoxToBbox:

    def test_normal_box(self):
        box = [[10, 20], [100, 20], [100, 50], [10, 50]]
        left, top, right, bottom = ppocr_box_to_bbox(box)
        assert left == 10.0
        assert top == 20.0
        assert right == 100.0
        assert bottom == 50.0

    def test_unordered_points(self):
        """Corners in any order -> correct AABB."""
        box = [[100, 50], [10, 50], [10, 20], [100, 20]]
        left, top, right, bottom = ppocr_box_to_bbox(box)
        assert left == 10.0
        assert right == 100.0
        assert top == 20.0
        assert bottom == 50.0

    def test_empty_list(self):
        assert ppocr_box_to_bbox([]) == (0.0, 0.0, 0.0, 0.0)

    def test_none(self):
        assert ppocr_box_to_bbox(None) == (0.0, 0.0, 0.0, 0.0)

    def test_string(self):
        assert ppocr_box_to_bbox("not_a_box") == (0.0, 0.0, 0.0, 0.0)

    def test_single_point(self):
        box = [[5, 5]]
        left, top, right, bottom = ppocr_box_to_bbox(box)
        assert left == 5.0 and right == 5.0 and top == 5.0 and bottom == 5.0


# ===================================================================
# word_separator
# ===================================================================

class TestWordSeparator:

    def test_cjk_cjk_no_space(self):
        assert word_separator("中文", "字符") == ""

    def test_latin_latin_space(self):
        assert word_separator("hello", "world") == " "

    def test_latin_punctuation_no_space(self):
        assert word_separator("hello", ",") == ""
        assert word_separator("hello", ".") == ""

    def test_cjk_latin_space(self):
        """CJK followed by Latin needs a space."""
        assert word_separator("测试", "ABC") == " "

    def test_latin_cjk_space(self):
        """Latin followed by CJK needs a space."""
        assert word_separator("ABC", "测试") == " "

    def test_emdash_no_space(self):
        """Em-dash and en-dash suppress spacing like CJK brackets."""
        assert word_separator("pre", "—") == ""   # em-dash starts next token
        assert word_separator("pre—", "fix") == "" # dash at end suppresses space

    def test_hyphen_space(self):
        """Regular ASCII hyphen does NOT suppress spacing."""
        assert word_separator("pre", "-") == " "

    def test_empty_input(self):
        assert word_separator("", "a") == ""
        assert word_separator("a", "") == ""


# ===================================================================
# is_cjk_or_fullwidth
# ===================================================================

class TestIsCjkOrFullwidth:

    @pytest.mark.parametrize("char", [
        "中",  # CJK Unified Ideograph
        "日",  # CJK Unified Ideograph
        "あ",  # Hiragana
        "ア",  # Katakana
        "。",  # CJK punctuation
        "한",  # Hangul
        "！",  # Fullwidth punctuation
    ])
    def test_cjk_and_fullwidth(self, char):
        assert is_cjk_or_fullwidth(char) is True

    @pytest.mark.parametrize("char", ["A", "z", "0", " ", ".", "!"])
    def test_latin(self, char):
        assert is_cjk_or_fullwidth(char) is False

    def test_empty(self):
        assert is_cjk_or_fullwidth("") is False


# ===================================================================
# _apply_cjk_spacing
# ===================================================================

class TestApplyCjkSpacing:

    def test_cjk_latin_boundary(self):
        assert _apply_cjk_spacing("中文ABC") == "中文 ABC"

    def test_latin_cjk_boundary(self):
        assert _apply_cjk_spacing("ABC中文") == "ABC 中文"

    def test_no_change_when_already_spaced(self):
        assert _apply_cjk_spacing("中文 ABC") == "中文 ABC"

    def test_idempotent(self):
        once = _apply_cjk_spacing("中文ABC测试")
        twice = _apply_cjk_spacing(once)
        assert once == twice

    def test_empty(self):
        assert _apply_cjk_spacing("") == ""

    def test_url_preserved(self):
        """URLs containing CJK/Latin boundaries are NOT spaced."""
        text = "visit https://example.com/测试 now"
        result = _apply_cjk_spacing(text)
        # The URL portion should be untouched
        assert "https://example.com/测试" in result

    def test_pure_latin_unchanged(self):
        assert _apply_cjk_spacing("hello world") == "hello world"

    def test_pure_cjk_unchanged(self):
        assert _apply_cjk_spacing("純中文測試") == "純中文測試"
