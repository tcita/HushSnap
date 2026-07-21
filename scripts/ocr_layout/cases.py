"""Declarative test-case definitions for OCR layout evaluation.

Each case describes a block of text with known font metrics.  The renderer
turns it into HTML + PNG; the pipeline runs OCR + clustering; the evaluator
checks whether the clustering matches the declared line structure.
"""

from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════════════════
# Word banks
# ═══════════════════════════════════════════════════════════════════════════

_EN_WORDS = [
    "About", "After", "Again", "Begin", "Black", "Bring", "Brown",
    "Carry", "Chair", "Clean", "Close", "Could", "Dance", "Dream",
    "Drink", "Drive", "Early", "Earth", "Eight", "Empty", "Enter",
    "Every", "Fancy", "Field", "First", "Float", "Floor", "Found",
    "Fresh", "Ghost", "Given", "Glass", "Going", "Grace", "Grand",
    "Great", "Green", "Guard", "Guide", "Happy", "Heart", "Horse",
    "Hotel", "House", "Human", "Ideal", "Index", "Judge", "Known",
    "Laugh", "Learn", "Leave", "Light", "Local", "Logic", "Lunch",
    "Major", "March", "Match", "Metal", "Model", "Money", "Month",
    "Mouse", "Music", "Never", "Night", "Noble", "Noise", "North",
    "Ocean", "Offer", "Order", "Organ", "Other", "Paint", "Panel",
    "Peace", "Phase", "Pilot", "Place", "Plain", "Plane", "Plant",
    "Plate", "Point", "Polar", "Pound", "Power", "Press", "Price",
    "Prime", "Print", "Prize", "Proof", "Proud", "Prove", "Queen",
    "Quick", "Quiet", "Quite", "Quote", "Radio", "Raise", "Range",
    "Rapid", "Ratio", "Reach", "Ready", "Right", "River", "Round",
    "Route", "Royal", "Ruler", "Scale", "Scene", "Scope", "Sense",
    "Serve", "Seven", "Shape", "Share", "Sharp", "Sheet", "Shelf",
    "Shift", "Shoot", "Short", "Sight", "Since", "Skill", "Sleep",
    "Slide", "Small", "Smart", "Smile", "Smoke", "Solid", "Solve",
    "Sorry", "Sound", "South", "Space", "Spare", "Speak", "Speed",
    "Spell", "Spend", "Spirit", "Split", "Sport", "Staff", "Stage",
    "Stamp", "Stand", "Start", "State", "Steam", "Steel", "Stick",
    "Still", "Stock", "Stone", "Store", "Storm", "Story", "Strip",
    "Study", "Stuff", "Style", "Sugar", "Suite", "Table", "Taste",
    "Teach", "Terms", "Thank", "Their", "Theme", "There", "Thick",
    "Thing", "Think", "Third", "Those", "Three", "Throw", "Tight",
    "Tired", "Title", "Today", "Token", "Total", "Touch", "Tough",
    "Tower", "Track", "Trade", "Train", "Treat", "Trend", "Trial",
    "Tribe", "Trick", "Tried", "Truck", "Truly", "Trust", "Truth",
    "Twice", "Under", "Union", "Unity", "Until", "Upper", "Usage",
    "Usual", "Valid", "Value", "Video", "Visit", "Vital", "Voice",
    "Voter", "Waste", "Watch", "Water", "Weigh", "Wheat", "Wheel",
    "Where", "Which", "While", "White", "Whole", "Whose", "Woman",
    "World", "Worry", "Worse", "Worth", "Would", "Write", "Wrong",
    "Wrote", "Yield", "Young", "Youth", "Zebra", "Zones",
]

_CJK_WORDS = [
    "中文", "排版", "测试", "识别", "聚类", "算法", "高度", "引擎",
    "日本", "語学", "漢字", "文化", "幽灵", "深渊", "灵魂", "永恆",
    "命运", "光明", "黑暗", "宇宙", "星球", "海洋", "森林", "山脉",
]

# Prefix pool for unambiguous block identification
_PREFIXES = [
    "ax", "by", "cz", "dw", "ev", "fu", "gr", "hs", "it", "jq",
    "kp", "lm", "nz", "oc", "pd", "qf", "rg", "sh", "ti", "uj",
    "vk", "wl", "xm", "yn", "zo", "ap", "bq", "cr", "ds", "et",
    "fv", "gw", "hx", "iy", "jz", "ka", "lb", "mc", "nd", "oe",
    "pf", "qg", "rh", "si", "tj", "uk", "vl", "wm", "xn", "yo",
    "zp", "aq", "br", "cs", "dt", "eu", "fw", "gx", "hy", "iz",
]


# ═══════════════════════════════════════════════════════════════════════════
# Case types
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class LineClusteringCase:
    """A multi-line text block for testing line-break clustering.

    Attributes:
        id: unique case identifier.
        font_size_px: CSS font-size in pixels.
        line_height_ratio: line-height ÷ font-size (baseline-to-baseline).
        font_family: CSS font-family string.
        n_lines: how many lines of text.
        words_per_line: how many words on each line.
        word_spacing_px: gap between consecutive words.
        is_vertical: use vertical-rl writing mode.
        use_cjk: pick CJK words instead of English.
        prefix: two-letter token prepended to every word for block
                identification during matching.
        word_offset: index into the word bank for the first word.
    """

    id: str
    font_size_px: int
    line_height_ratio: float
    font_family: str = "Arial"
    n_lines: int = 2
    words_per_line: int = 5
    word_spacing_px: int = 48
    is_vertical: bool = False
    use_cjk: bool = False
    prefix: str = ""
    word_offset: int = 0


@dataclass
class BoxHeightCase:
    """A single-line text block for measuring OCR box height vs font-size.

    Attributes:
        id: unique case identifier.
        font_size_px: CSS font-size in pixels.
        font_family: CSS font-family string.
        words: the exact words to render (one per detection box).
        is_vertical: use vertical-rl writing mode.
        prefix: two-letter token for identification.
    """

    id: str
    font_size_px: int
    font_family: str
    words: list[str]
    is_vertical: bool = False
    prefix: str = ""


@dataclass
class MixedLineCase:
    """A multi-token line that deliberately mixes token shapes.

    Used to measure within-line box-height drift - the gap between
    a line's union-bbox height and its word-box median height.  Unlike
    :class:`BoxHeightCase` (uniform-height words) this renders tokens
    that naturally produce different detection-box heights on the same
    baseline: punctuation, mixed case (descenders), Latin+CJK, and an
    optional smaller-font run.

    Attributes:
        id: unique case identifier.
        font_size_px: CSS font-size in pixels for the main run.
        font_family: CSS font-family string.
        tokens: exact strings to render, left -> right.  Each becomes
            one detection box (matched back by the ``prefix`` token).
        small_token_indices: indices into ``tokens`` rendered at
            ``small_font_size_px`` (simulates super/subscript or a
            smaller inline run).
        small_font_size_px: font size for the small run.
        is_vertical: use vertical-rl writing mode.
        prefix: two-letter token for identification.
    """

    id: str
    font_size_px: int
    font_family: str
    tokens: list[str]
    small_token_indices: list[int] = field(default_factory=list)
    small_font_size_px: int = 0
    is_vertical: bool = False
    prefix: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Case generators
# ═══════════════════════════════════════════════════════════════════════════


def make_line_clustering_cases(
    sizes: list[int] | None = None,
    ratios: list[float] | None = None,
    families: list[str] | None = None,
    n_lines: int = 2,
    words_per_line: int = 5,
) -> list[LineClusteringCase]:
    """Generate the Cartesian product of sizes × ratios × families.

    Each case gets a unique two-letter prefix so OCR boxes can be matched
    back to their ground-truth lines unambiguously.
    """
    if sizes is None:
        sizes = [14, 16, 18, 20, 24, 32]
    if ratios is None:
        ratios = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0]
    if families is None:
        families = ["Arial"]

    cases: list[LineClusteringCase] = []
    prefix_idx = 0
    word_offset = 0

    for fs in sizes:
        for ratio in ratios:
            for fam in families:
                px = _PREFIXES[prefix_idx % len(_PREFIXES)]
                prefix_idx += 1
                cases.append(LineClusteringCase(
                    id=f"{px}_{fs}_{ratio:.1f}",
                    font_size_px=fs,
                    line_height_ratio=ratio,
                    font_family=fam,
                    n_lines=n_lines,
                    words_per_line=words_per_line,
                    prefix=px,
                    word_offset=word_offset,
                ))
                word_offset = (word_offset + n_lines * words_per_line) % len(_EN_WORDS)

    return cases


def make_box_height_cases(
    sizes: list[int] | None = None,
    families: list[str] | None = None,
    samples_per_combo: int = 10,
) -> list[BoxHeightCase]:
    """Generate single-word cases for measuring box-height inflation."""
    if sizes is None:
        sizes = [8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 64]
    if families is None:
        families = ["Arial", "Times New Roman", "Consolas", "Microsoft YaHei"]

    cases: list[BoxHeightCase] = []
    prefix_idx = 0
    word_idx = 0

    for fs in sizes:
        for fam in families:
            px = _PREFIXES[prefix_idx % len(_PREFIXES)]
            prefix_idx += 1
            words = []
            for _ in range(samples_per_combo):
                w = _EN_WORDS[word_idx % len(_EN_WORDS)]
                words.append(f"{px}{w}")
                word_idx += 1
            cases.append(BoxHeightCase(
                id=f"bh_{px}_{fs}",
                font_size_px=fs,
                font_family=fam,
                words=words,
                prefix=px,
            ))

    return cases


# ═══════════════════════════════════════════════════════════════════════════
# Defaults for quick access
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_CLUSTERING_SIZES = [14, 16, 18, 20, 24, 32]
DEFAULT_CLUSTERING_RATIOS = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0]
DEFAULT_BOX_HEIGHT_SIZES = [12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 64]
