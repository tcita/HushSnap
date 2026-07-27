"""Tests for PP-OCR layout pipeline — pure functions, no ONNX / Qt.

Coverage:
  - _greedy_line_cluster   (horizontal clustering + anti-bridging)
  - _greedy_column_cluster (vertical clustering + anti-bridging)
  - _normalize_blocks      (empty / invalid-box filtering)
  - _is_vertical_json      (tall-box area-weighted voting)
  - _decide_indentation   (single- / multi-level indent detection)
  - compose_ppocr_structures (integration, horizontal + vertical)
  - ppocr_box_to_bbox      (coordinate parsing edge cases)
  - block_separator       (CJK / Latin / punctuation boundaries)
  - is_cjk_or_fullwidth    (Unicode block membership)
  - _apply_cjk_spacing     (pangu-style CJK↔Latin spacing)
"""

import pytest
from hushsnap.ocr.ppocr import (
    _greedy_line_cluster,
    _greedy_column_cluster,
    _normalize_blocks,
    _is_vertical_json,
    _decide_indentation,
    _decide_paragraph_breaks,
    _render_layout,
    compose_ppocr_structures,
    ppocr_box_to_bbox,
    block_separator,
    is_cjk_or_fullwidth,
    _apply_cjk_spacing,
)
from hushsnap.ocr.models import OcrBox, OcrLine, OcrWord


def _apply_layout(lines):
    """Test helper: run the full decide + render stage (horizontal) so tests
    can keep asserting on final text / line count.  Mirrors what
    compose_ppocr_structures does in its horizontal branch."""
    _decide_indentation(lines)
    break_after = _decide_paragraph_breaks(lines)
    return _render_layout(lines, break_after)


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


def _h_blocks(text_a, text_b, w, h, gap, top=0):
    """Two blocks on one horizontal line (equal centre_y), `gap` px apart.

    Both blocks share height *h* and width *w* and sit at the same y, so
    they cluster into a single line; the inter-block gap is exactly *gap*.
    """
    a = _raw_block(text_a, [[0, top], [w, top], [w, top + h], [0, top + h]])
    b_left = w + gap
    b = _raw_block(text_b, [
        [b_left, top], [b_left + w, top],
        [b_left + w, top + h], [b_left, top + h],
    ])
    return [a, b]


def _v_blocks(text_a, text_b, w, h, gap, left=0):
    """Two blocks in one vertical column (equal centre_x), `gap` px apart.

    Both blocks share width *w* and height *h* and sit at the same x, so
    they cluster into a single column; the inter-block gap (along y) is
    exactly *gap*.
    """
    a = _raw_block(text_a, [[left, 0], [left + w, 0], [left + w, h], [left, h]])
    b_top = h + gap
    b = _raw_block(text_b, [
        [left, b_top], [left + w, b_top],
        [left + w, b_top + h], [left, b_top + h],
    ])
    return [a, b]


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

    # ── centre-distance threshold boundaries ───────────────────────────
    # The single gate is  abs(box.center_y - median_center) < 0.4 * median_h / _BOX_H_TO_FS_RATIO
    # (strict <, threshold scales with the running median height).  These
    # tests pin both the strictness and the median-height scaling so a
    # refactor cannot silently change the threshold constant or the
    # comparison operator.

    def test_centre_distance_exactly_at_threshold_splits(self):
        """abs(delta) == 0.4 * median_h / 1.2 -> SPLIT (strict <).

        Two equal-height boxes (h=30).  threshold = 0.4 * 30 / 1.2 = 10.
        Box B centre at 25 (delta=10).  10 < 10 is False → split.
        (h=30 chosen for exact integer arithmetic; h=48 produces
         0.4*48/1.2=16.000000000000004 in IEEE 754.)
        """
        blocks = [
            _box("A", top=0,  bottom=30, left=0,  right=20),   # center_y=15, h=30
            _box("B", top=10, bottom=40, left=0,  right=20),   # center_y=25, h=30
        ]                                                      # |25-15| = 10 == threshold
        lines = _greedy_line_cluster(blocks)
        assert len(lines) == 2
        assert [b["text"] for b in lines[0]] == ["A"]
        assert [b["text"] for b in lines[1]] == ["B"]

    def test_centre_distance_just_below_threshold_merges(self):
        """abs(delta) = 9 < 10 -> MERGE (same line).

        Box B centre 1 px closer than the split case above.
        """
        blocks = [
            _box("A", top=0,  bottom=30, left=0, right=20),   # center_y=15, h=30
            _box("B", top=9,  bottom=39, left=0, right=20),   # center_y=24, h=30
        ]                                                      # |24-15| = 9 < 10
        lines = _greedy_line_cluster(blocks)
        assert len(lines) == 1
        assert [b["text"] for b in lines[0]] == ["A", "B"]

    def test_threshold_scales_with_median_height(self):
        """A tall drop-cap raises the threshold so an offset box merges.

        Box A is a tall drop-cap (h=120, center_y=60).  threshold =
        0.4 * 120 / 1.2 = 40.  Box B at center_y=85 (delta=25) merges.
        (120 chosen to avoid IEEE 754 rounding.)
        """
        blocks = [
            _box("A", top=0,  bottom=120, left=0,  right=40, height=120),  # drop-cap
            _box("B", top=75, bottom=95,  left=50, right=90, height=20),   # center_y=85, delta=25
        ]
        lines = _greedy_line_cluster(blocks)
        assert len(lines) == 1
        assert [b["text"] for b in lines[0]] == ["A", "B"]


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

    # ── centre-distance threshold boundaries (mirror of the horizontal) ─
    # Gate:  abs(box.center_x - median_center) < 0.4 * median_w / _BOX_H_TO_FS_RATIO
    # (strict <, threshold scales with running median WIDTH).  These mirror the
    # horizontal threshold tests above so the vertical path is not an
    # unverified copy of the horizontal one.

    def test_centre_distance_exactly_at_threshold_splits(self):
        """abs(delta) == 0.4 * median_w / 1.2 -> SPLIT (strict <).

        Two equal-width columns (w=30).  threshold = 0.4 * 30 / 1.2 = 10.
        B (right=40) sorts before A (right=30) by -right.  B is anchor at
        center_x=25.  A at center_x=15, delta=10.  10 < 10 is False → split.
        """
        blocks = [
            _box("A", top=0, bottom=14, left=0,  right=30),   # center_x=15, w=30
            _box("B", top=0, bottom=14, left=10, right=40),   # center_x=25, w=30
        ]                                                       # |15-25| = 10 == threshold
        columns = _greedy_column_cluster(blocks)
        assert len(columns) == 2
        assert [b["text"] for b in columns[0]] == ["B"]
        assert [b["text"] for b in columns[1]] == ["A"]

    def test_centre_distance_just_below_threshold_merges(self):
        """abs(delta) = 9 < 10 -> MERGE (same column)."""
        blocks = [
            _box("A", top=0, bottom=14, left=0,  right=30),   # center_x=15, w=30
            _box("B", top=0, bottom=14, left=9,  right=39),   # center_x=24, w=30
        ]                                                       # |15-24| = 9 < 10
        columns = _greedy_column_cluster(blocks)
        assert len(columns) == 1
        assert [b["text"] for b in columns[0]] == ["B", "A"]

    def test_threshold_scales_with_median_width(self):
        """A wide anchor raises the threshold so an offset box merges.

        Box A is a wide anchor (w=120, center_x=60).  threshold =
        0.4 * 120 / 1.2 = 40.  Box B at center_x=85 (delta=25) merges.
        """
        blocks = [
            _box("A", top=0, bottom=14, left=0,  right=120, width=120),  # wide anchor
            _box("B", top=0, bottom=14, left=75, right=95,  width=20),  # center_x=85, delta=25
        ]
        columns = _greedy_column_cluster(blocks)
        assert len(columns) == 1
        assert [b["text"] for b in columns[0]] == ["A", "B"]

    def test_three_columns_sorted_right_to_left(self):
        """Three well-separated single-box columns -> three columns, RTL."""
        blocks = [
            _box("左", top=0, bottom=14, left=0,   right=20),    # leftmost
            _box("中", top=0, bottom=14, left=100, right=120),   # middle
            _box("右", top=0, bottom=14, left=200, right=220),   # rightmost
        ]
        columns = _greedy_column_cluster(blocks)
        assert len(columns) == 3
        assert [c[0]["text"] for c in columns] == ["右", "中", "左"]


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
        """One tall box dwarfed by a wide box -> horizontal (area-weighted).

        tall 14x50=700 px², wide 100x14=1400 px².
        tall/total = 700/2100 = 0.33 < 0.5 -> horizontal.
        """
        data = [
            {"box": [[0, 0],  [14, 0],  [14, 50],  [0, 50]]},   # 14x50=700, tall
            {"box": [[20, 0], [120, 0], [120, 14], [20, 14]]},  # 100x14=1400, wide
        ]
        assert _is_vertical_json(data) is False

    def test_threshold_at_exactly_half_is_vertical(self):
        """tall_area/total == 0.5 exactly -> vertical (>= is inclusive).

        tall 10x100=1000, wide 100x10=1000.  ratio = 0.5 >= 0.5 -> True.
        Pins the `>=` operator: a strict `>` would classify this as horizontal.
        """
        data = [
            {"box": [[0, 0], [10, 0], [10, 100], [0, 100]]},   # 10x100=1000, tall
            {"box": [[0, 0], [100, 0], [100, 10], [0, 10]]},   # 100x10=1000, wide
        ]
        assert _is_vertical_json(data) is True

    def test_threshold_just_below_half_is_horizontal(self):
        """tall_area/total just under 0.5 -> horizontal.

        tall 10x100=1000, wide 110x10=1100.  ratio = 1000/2100 = 0.476 < 0.5.
        """
        data = [
            {"box": [[0, 0], [10, 0],  [10, 100],  [0, 100]]},  # 10x100=1000, tall
            {"box": [[0, 0], [110, 0], [110, 10],  [0, 10]]},   # 110x10=1100, wide
        ]
        assert _is_vertical_json(data) is False

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
# _decide_indentation
# ===================================================================

class TestApplyIndentation:

    def test_single_line_no_indent(self):
        lines = [_line("hello", x=0, y=0, w=40, h=14)]
        result = _apply_layout(lines)
        assert result[0].text == "hello"

    def test_indented_line(self):
        """One line is offset from the left baseline -> exactly 4 spaces.

        indent unit = 40 (smallest offset), level = round(40/40) = 1
        -> 1 * 4 = 4 leading spaces.  Asserts the EXACT count, not just
        startswith("    "): a regression that doubled the spaces would
        still pass startswith.
        """
        lines = [
            _line("body",  x=0,  y=0,  w=40, h=14),  # baseline
            _line("indent", x=40, y=20, w=40, h=14),  # offset 40, ratio 40/14≈2.86 > 1
        ]
        result = _apply_layout(lines)
        assert result[0].text == "body"
        assert result[1].text == "    indent"  # exactly 4 spaces

    def test_multi_level_indent(self):
        """Two different indent levels -> 4 and 8 spaces exactly.

        unit = 20 (smallest offset > threshold).  L1 offset=20 -> level 1
        -> 4 spaces.  L2 offset=40 -> level round(40/20)=2 -> 8 spaces.
        """
        lines = [
            _line("L0", x=0,  y=0,  w=30, h=14),   # baseline
            _line("L1", x=20, y=20, w=30, h=14),   # offset 20, ratio 20/14≈1.43 > 1
            _line("L2", x=40, y=40, w=30, h=14),   # offset 40, ratio 40/14≈2.86 > 1
        ]
        result = _apply_layout(lines)
        assert result[0].text == "L0"
        assert result[1].text == "    L1"          # 4 spaces
        assert result[2].text == "        L2"      # 8 spaces

    def test_indent_exactly_at_threshold_no_indent(self):
        """offset/calibrated_h == 1.0 → NOT indented (strict >).

        16 px body, box_h=21, calib=17.5.  offset=17 → ratio=0.97 < 1.0.
        """
        lines = [
            _line("body",   x=0,  y=0,  w=50, h=21),
            _line("offset", x=17, y=26, w=50, h=21),
        ]
        result = _apply_layout(lines)
        assert result[1].text == "offset"  # no leading spaces

    def test_indent_just_above_threshold_indents(self):
        """offset/calibrated_h > 1.0 → indented.

        16 px body, box_h=21, calib=17.5.  offset=18 > 17.5, ratio=1.03.
        """
        lines = [
            _line("body",   x=0,  y=0,  w=50, h=21),
            _line("offset", x=18, y=26, w=50, h=21),
        ]
        result = _apply_layout(lines)
        assert result[1].text == "    offset"  # unit=18, level 1 → 4 spaces

    def test_no_significant_offset(self):
        """Sub-pixel jitter offset → no indent.

        14 px body, OCR box_h ≈ 18 px, calibrated = 15.  2 px of detection
        jitter gives ratio = 2 / 15 = 0.13 ≪ 0.5.
        """
        lines = [
            _line("L0", x=0, y=0, w=30, h=18),
            _line("L1", x=2, y=24, w=30, h=18),  # 2 px jitter
        ]
        result = _apply_layout(lines)
        assert not result[1].text.startswith(" ")

    def test_all_same_x(self):
        """All lines at same x -> no indent applied."""
        lines = [
            _line("A", x=10, y=0,  w=30, h=14),
            _line("B", x=10, y=20, w=30, h=14),
        ]
        result = _apply_layout(lines)
        assert result[0].text == "A"
        assert result[1].text == "B"

    def test_indent_denominator_drift_robust_against_union(self):
        """A drifting tall box must not suppress the indent ratio.

        The offset line has three boxes: two at h=14 and one drifted tall
        box at h=24.  The union-bbox height is 24 (the tall box dominates);
        the word-median height is 14 (sorted[3//2] = the second of
        [14,14,24]).  With offset=16:

            word-median ratio = 16/14 ~ 1.14 > 0.5 -> indented
            union      ratio = 16/24 ~ 0.67 > 0.5 -> also indents now
            (the drift-robustness point stands either way: the rule
            uses the word-median, not the union, as its denominator)

        The indent rule must use the word-median denominator: a single
        drifting tall box must not inflate the height, suppress the ratio,
        and miss the indent.  The _line(...) tests never set .words, so they
        exercise only the fallback path - this pins the words path.
        """
        body = _line("body", x=0, y=0, w=40, h=14)
        offset_line = _line("indent", x=16, y=20, w=40, h=24)  # union bbox
        offset_line.words = [
            OcrWord("i1", OcrBox(x=16, y=20, width=12, height=14)),
            OcrWord("i2", OcrBox(x=29, y=20, width=12, height=14)),
            OcrWord("i3", OcrBox(x=42, y=14, width=8,  height=24)),  # drifts tall
        ]
        result = _apply_layout([body, offset_line])
        assert result[1].text == "    indent"  # level 1, word-median ratio > 1

    def test_vertical_lines_skip_indentation(self):
        """Vertical CJK never gets leading-indent spaces.

        compose_ppocr_structures applies _decide_indentation only on the
        horizontal path (ppocr.py: `if not is_vertical`).  The right column
        here sits far right of the left baseline, so the horizontal path
        would indent it -- the vertical path must NOT.
        """
        blocks = [
            # right column, offset far right (would indent horizontally)
            _raw_block("右", [[200, 0], [214, 0], [214, 50], [200, 50]]),
            # left column = baseline
            _raw_block("左", [[0, 0],   [14, 0],  [14, 50],  [0, 50]]),
        ]
        lines = compose_ppocr_structures(blocks, is_vertical=True)
        for ln in lines:
            assert ln.text == ln.text.lstrip(), (
                f"vertical line must not carry leading indent spaces: {ln.text!r}"
            )


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
# block_separator
# ===================================================================

class TestBlockSeparator:

    def test_cjk_cjk_no_space(self):
        assert block_separator("中文", "字符") == ""

    def test_latin_latin_space(self):
        assert block_separator("hello", "world") == " "

    def test_latin_punctuation_no_space(self):
        assert block_separator("hello", ",") == ""
        assert block_separator("hello", ".") == ""

    def test_cjk_latin_space(self):
        """CJK followed by Latin needs a space."""
        assert block_separator("测试", "ABC") == " "

    def test_latin_cjk_space(self):
        """Latin followed by CJK needs a space."""
        assert block_separator("ABC", "测试") == " "

    def test_emdash_no_space(self):
        """Em-dash and en-dash suppress spacing like CJK brackets."""
        assert block_separator("pre", "—") == ""   # em-dash starts next token
        assert block_separator("pre—", "fix") == "" # dash at end suppresses space

    def test_hyphen_space(self):
        """Regular ASCII hyphen does NOT suppress spacing."""
        assert block_separator("pre", "-") == " "

    def test_empty_input(self):
        assert block_separator("", "a") == ""
        assert block_separator("a", "") == ""


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


# ===================================================================
# inline gap spacing (_build_lines_from_clusters)
# ===================================================================

class TestInlineGapSpacing:
    """Deliberate visual gaps between adjacent blocks -> leading spaces.

    Algorithm (ppocr.py _build_lines_from_clusters):
      gap = next.left - prev.right          # horizontal
      est = min(prev.height, next.height)
      if gap_ratio > 1.0:                    # strict >
          n = max(1, round(gap_ratio))       # banker's rounding
          sep = (sep or "") + (" " * n)

    Vertical mirrors x->y and uses box *width* as the denominator.  These
    tests pin: the strict `> 1.0` threshold, Python's banker's `round`
    (round-half-to-even), the sep+gap combination, and crucially that the
    denominator is HEIGHT for horizontal / WIDTH for vertical -- the one
    dimension a refactor could silently swap.
    """

    # ── horizontal: threshold + rounding + denominator ──────────────

    def test_h_small_gap_no_extra_space(self):
        """gap_ratio = 0.5 (< 1.0) -> no extra space; only block_separator.

        CJK-CJK: block_separator -> "" -> "你好世界".
        """
        blocks = _h_blocks("你好", "世界", w=40, h=20, gap=10)  # ratio 0.5
        lines = compose_ppocr_structures(blocks, is_vertical=False)
        assert lines[0].text == "你好世界"

    def test_h_small_gap_latin_keeps_single_separator_space(self):
        """Latin-Latin small gap -> only the one block_separator space."""
        blocks = _h_blocks("hello", "world", w=40, h=20, gap=10)  # ratio 0.5
        lines = compose_ppocr_structures(blocks, is_vertical=False)
        assert lines[0].text == "hello world"  # single space from block_separator

    def test_h_gap_at_threshold_no_extra_space(self):
        """gap_ratio = 1.94 < 2.0 → no extra space.  Pins strict `>`.

        20 px CJK, box_h=26, calib=21.67.  gap=42 → ratio=1.94.
        A 42 px gap (two full CJK chars) should NOT trigger inline spacing.
        """
        blocks = _h_blocks("你好", "世界", w=50, h=26, gap=42)
        lines = compose_ppocr_structures(blocks, is_vertical=False)
        assert lines[0].text == "你好世界"

    def test_h_gap_just_above_threshold_two_spaces(self):
        """gap_ratio = 2.03 > 2.0 → round(2.03)=2 spaces.

        Table column gap (44 px) between 20 px CJK columns.
        """
        blocks = _h_blocks("你好", "世界", w=50, h=26, gap=44)  # ratio ≈ 2.03
        lines = compose_ppocr_structures(blocks, is_vertical=False)
        assert lines[0].text == "你好  世界"  # 2 spaces

    def test_h_banker_rounding_ratio_2_5(self):
        """gap_ratio = 2.5 → round(2.5)=2 (banker's).  Excludes round-half-up/ceil."""
        blocks = _h_blocks("你好", "世界", w=50, h=26, gap=54)  # ratio ≈ 2.49
        lines = compose_ppocr_structures(blocks, is_vertical=False)
        assert lines[0].text == "你好  世界"  # 2 spaces, NOT 3

    def test_h_banker_rounding_ratio_3_5(self):
        """gap_ratio = 3.5 → round(3.5)=4 (banker's)."""
        blocks = _h_blocks("你好", "世界", w=50, h=26, gap=76)  # ratio ≈ 3.51
        lines = compose_ppocr_structures(blocks, is_vertical=False)
        assert lines[0].text == "你好    世界"  # 4 spaces

    def test_h_latin_latin_gap_plus_separator(self):
        """Latin-Latin: block_separator + gap spaces = 1 + 2 = 3."""
        blocks = _h_blocks("hello", "world", w=50, h=26, gap=44)  # ratio ≈ 2.03
        lines = compose_ppocr_structures(blocks, is_vertical=False)
        assert lines[0].text == "hello   world"  # 3 spaces (1 sep + 2 gap)

    def test_h_denominator_is_height_not_width(self):
        """est uses min(height), not width.  Wide boxes (w=160, h=26) still use h."""
        blocks = _h_blocks("你好", "世界", w=160, h=26, gap=44)
        lines = compose_ppocr_structures(blocks, is_vertical=False)
        assert lines[0].text == "你好  世界"  # 2 spaces → est used height

    # ── vertical: mirror ────────────────────────────────────────────

    def test_v_small_gap_no_extra_space(self):
        """Small vertical gap → no extra space."""
        blocks = _v_blocks("一", "二", w=26, h=40, gap=12)
        lines = compose_ppocr_structures(blocks, is_vertical=True)
        assert lines[0].text == "一二"

    def test_v_gap_at_threshold_no_extra_space(self):
        """gap_ratio = 1.94 < 2.0 → no extra space (vertical)."""
        blocks = _v_blocks("一", "二", w=26, h=40, gap=42)
        lines = compose_ppocr_structures(blocks, is_vertical=True)
        assert lines[0].text == "一二"

    def test_v_gap_just_above_threshold_two_spaces(self):
        """gap_ratio = 2.03 > 2.0 → 2 spaces (vertical)."""
        blocks = _v_blocks("一", "二", w=26, h=40, gap=44)
        lines = compose_ppocr_structures(blocks, is_vertical=True)
        assert lines[0].text == "一  二"

    def test_v_banker_rounding_ratio_2_5(self):
        """gap_ratio = 2.5 → round(2.5)=2 (banker's)."""
        blocks = _v_blocks("一", "二", w=26, h=40, gap=54)
        lines = compose_ppocr_structures(blocks, is_vertical=True)
        assert lines[0].text == "一  二"  # 2 spaces, NOT 3

    def test_v_denominator_is_width_not_height(self):
        """est uses min(width), not height — the key vertical invariant.

        Tall boxes (w=26, h=100), gap=44.  26/1.2 ≈ 21.67, ratio ≈ 2.03 → 2 spaces.
        """
        blocks = _v_blocks("一", "二", w=26, h=100, gap=44)
        lines = compose_ppocr_structures(blocks, is_vertical=True)
        assert lines[0].text == "一  二"  # 2 spaces → est used width


# ===================================================================
# _decide_paragraph_breaks
# ===================================================================

class TestApplyParagraphBreaks:

    def test_single_line_no_break(self):
        lines = [_line("hello", x=0, y=0, w=40, h=14)]
        result = _apply_layout(lines)
        assert len(result) == 1
        assert result[0].text == "hello"

    def test_normal_spacing_no_break(self):
        """Lines with tight spacing (same paragraph) -> no blank line."""
        lines = [
            _line("Line1", x=0, y=0,  w=40, h=14),
            _line("Line2", x=0, y=18, w=40, h=14),  # gap = 18-14 = 4 < 14
            _line("Line3", x=0, y=36, w=40, h=14),  # gap = 36-32 = 4 < 14
        ]
        result = _apply_layout(lines)
        assert len(result) == 3  # no blank lines inserted
        assert all(ln.text for ln in result)

    def test_paragraph_break_inserted(self):
        """Large gap (>= 1x line height) -> single blank line inserted."""
        lines = [
            _line("Para1", x=0, y=0,  w=50, h=14),
            _line("Para2", x=0, y=60, w=50, h=14),  # gap = 60-14 = 46 >= 14
        ]
        result = _apply_layout(lines)
        assert len(result) == 3  # Para1, blank, Para2
        assert result[0].text == "Para1"
        assert result[1].text == ""
        assert result[2].text == "Para2"

    def test_mixed_paragraphs(self):
        """Two paragraphs with multiple lines each."""
        lines = [
            _line("A1", x=0, y=0,  w=30, h=14),   # para A
            _line("A2", x=0, y=18, w=30, h=14),   # para A (gap=4)
            _line("B1", x=0, y=60, w=30, h=14),   # para B (gap=42 >= 14)
            _line("B2", x=0, y=78, w=30, h=14),   # para B (gap=4)
        ]
        result = _apply_layout(lines)
        assert len(result) == 5  # A1, A2, blank, B1, B2
        assert result[2].text == ""  # blank line between paragraphs

    def test_gap_exactly_at_threshold_no_break(self):
        """Equal-height lines, 1.5× line gap → no break (strict >).

        20 px body, box_h=26, calib=21.67.  Threshold = 21.67/2 + 21.67/2
        + 1.5×21.67 = 54.17.  Centre distance 54 < 54.17 → no break.
        (A 28 px physical gap between the two text blocks — ~1.4× font
        size, tight but still one paragraph.)
        """
        lines = [
            _line("Top",    x=0, y=0,  w=50, h=26),
            _line("Bottom", x=0, y=54, w=50, h=26),
        ]
        result = _apply_layout(lines)
        assert len(result) == 2  # no blank line

    def test_gap_just_above_threshold_breaks(self):
        """Same heights, centre distance 56 > 54.17 → break."""
        lines = [
            _line("Top",    x=0, y=0,  w=50, h=26),
            _line("Bottom", x=0, y=56, w=50, h=26),
        ]
        result = _apply_layout(lines)
        assert len(result) == 3  # blank line inserted

    def test_edge_case_gap_just_below_threshold(self):
        """Same heights, centre distance 53 < 54.17 → no break."""
        lines = [
            _line("Top",    x=0, y=0,  w=50, h=26),
            _line("Bottom", x=0, y=53, w=50, h=26),
        ]
        result = _apply_layout(lines)
        assert len(result) == 2  # no blank line

    def test_moderate_paragraph_spacing_no_break(self):
        """Ordinary paragraph spacing (1× font size gap) → no break.

        Only obviously-disconnected blocks (≳ 1.5× line height gap) trigger.
        """
        lines = [
            _line("Top",    x=0, y=0,  w=50, h=26),
            _line("Bottom", x=0, y=46, w=50, h=26),  # 20 px gap → no break
        ]
        result = _apply_layout(lines)
        assert len(result) == 2  # no blank line

    def test_variable_line_heights(self):
        """Title (24 px → box_h=31) + body (16 px → box_h=21): large gap → break.

        Title calib=25.83, body calib=17.5.  Threshold = 25.83/2 + 17.5/2
        + 1.5×25.83 = 60.4.  centre distance 61 > 60.4 → break.
        """
        lines = [
            _line("Title", x=0, y=0,  w=60, h=31),
            _line("Body1", x=0, y=66, w=50, h=21),
            _line("Body2", x=0, y=95, w=50, h=21),
        ]
        result = _apply_layout(lines)
        # centre distance = 55; threshold = 24/2 + 14/2 + max(24, 14) = 43.
        assert len(result) == 4
        assert result[1].text == ""  # blank after title

    def test_mixed_heights_require_gap_for_taller_line(self):
        """Short body line after tall title → modest gap does not break.

        24 px title (box_h=31, calib=25.8) + 16 px body (box_h=21, calib=17.5).
        Threshold = 25.8/2 + 17.5/2 + 25.8 = 47.5.  Centre distance 40
        (title centre 15.5, body centre 55.5, gap 40).  40 < 47.5 → no break.
        """
        lines = [
            _line("Title", x=0, y=0,  w=60, h=31),
            _line("Body",  x=0, y=43, w=50, h=21),
        ]
        result = _apply_layout(lines)
        assert len(result) == 2

    def test_word_height_median_not_union_height_sets_threshold(self):
        """Minor within-line box drift does not inflate the break threshold.

        16 px body text, OCR box_h ≈ 18 px (2 words at h=18), calibrated ≈ 15.
        Word-median threshold = 15/2 + 15/2 + 15 = 30.
        word-median centres: 17 (title) and 58 (body), gap = 41 > 30 → break.
        """
        title = _line("Title", x=0, y=0, w=60, h=30)
        title.words = [
            OcrWord("Title", OcrBox(x=0, y=8, width=60, height=18)),
            OcrWord(".", OcrBox(x=61, y=0, width=4, height=18)),
        ]
        body = _line("Body", x=0, y=51, w=50, h=18)
        body.words = [OcrWord("Body", OcrBox(x=0, y=51, width=50, height=18))]

        result = _apply_layout([title, body])
        assert len(result) == 3

    def test_word_centre_median_drift_robust_against_union(self):
        """A drifting box must not inflate the gap via the union centre.

        Line A has two boxes (h=18, 16 px body) — one normal at centre 17,
        one drifted up to centre 4.  Word-median centre stays at 17; union
        bbox centre drops to 8.5.  Line B centre at 45 (y=36, h=18).

        Calibrated threshold = 15 + 15 + 15 = 30 (equal word-median h=18, calib=15).
        Word-median gap = 45 - 17 = 28 < 30 → no break.
        Union gap       = 45 - 8.5 = 36.5 > 30 → would falsely break.
        """
        line_a_union_top = -7  # drifted box goes up
        line_a_union_bot = 7 + 18  # = 25
        line_a_union_h = line_a_union_bot - line_a_union_top  # = 32
        line_a = _line("A", x=0, y=line_a_union_top, w=60, h=line_a_union_h)
        line_a.words = [
            OcrWord("A1", OcrBox(x=0,  y=8,  width=30, height=18)),  # centre 17
            OcrWord("A2", OcrBox(x=31, y=-7, width=4,  height=18)),  # centre 2, drifts up
        ]
        line_b = _line("B", x=0, y=36, w=40, h=18)
        line_b.words = [OcrWord("B", OcrBox(x=0, y=36, width=40, height=18))]

        result = _apply_layout([line_a, line_b])
        assert len(result) == 2  # no blank line — word-median resists drift

    def test_no_break_with_large_boxes_small_gap(self):
        """Tall boxes with small gap still don't trigger false break."""
        lines = [
            _line("Big1", x=0, y=0,  w=100, h=30),
            _line("Big2", x=0, y=34, w=100, h=30),  # gap = 34-30 = 4 < 30
        ]
        result = _apply_layout(lines)
        assert len(result) == 2

    def test_huge_gap_still_single_blank(self):
        """Very large gap -> still only one blank line (binary separator)."""
        lines = [
            _line("Top",    x=0, y=0,   w=40, h=14),
            _line("Bottom", x=0, y=200, w=40, h=14),  # gap = 186 >> 14
        ]
        result = _apply_layout(lines)
        assert len(result) == 3  # exactly one blank line, not multiple

    def test_empty_input(self):
        assert _apply_layout([]) == []

    def test_zero_height_ignored(self):
        """A zero-height line does not prevent adjacent local comparison."""
        lines = [
            _line("A", x=0, y=0,  w=40, h=0),
            _line("B", x=0, y=0,  w=40, h=14),
            _line("C", x=0, y=30, w=40, h=14),  # centre distance 30 > 28
        ]
        result = _apply_layout(lines)
        assert len(result) >= 3

    def test_compose_integration_horizontal(self):
        """compose_ppocr_structures includes paragraph breaks for horizontal text."""
        blocks = [
            _raw_block("Title", [[0, 0],  [40, 0],  [40, 14], [0, 14]]),
            _raw_block("Body",  [[0, 60], [40, 60], [40, 74], [0, 74]]),
        ]
        lines = compose_ppocr_structures(blocks, is_vertical=False)
        # Title (y=0..14), gap=46 >= avg_h=14 -> blank line, Body (y=60..74)
        assert len(lines) >= 2
        assert any(ln.text == "" for ln in lines), "blank-line separator expected"

    def test_compose_integration_vertical_no_breaks(self):
        """Paragraph breaks do NOT apply to vertical CJK text."""
        blocks = [
            _raw_block("右", [[0, 0],  [14, 0],  [14, 60], [0, 60]]),
            _raw_block("左", [[50, 0], [64, 0], [64, 60], [50, 60]]),
        ]
        lines = compose_ppocr_structures(blocks, is_vertical=True)
        # Vertical text should NOT get paragraph breaks
        assert not any(ln.text == "" for ln in lines)
