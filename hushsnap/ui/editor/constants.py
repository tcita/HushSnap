from ..styles import BRAND_GREEN

# ── Style constants ──────────────────────────────────────────────────────────

# Text annotation is always white fill + black outline (drawn as two passes:
# the outline first, then the fill on top) so it stays readable on any
# screenshot background without a user-chosen color. Text has no color picker.
TEXT_FILL_COLOR = "#FFFFFF"
TEXT_OUTLINE_COLOR = "#000000"
TEXT_OUTLINE_WIDTH = 0.18  # as a fraction of font pixel size

EDITOR_WINDOW_STYLE = """
QWidget#editorWindow {
    background-color: #2d2d2d;
    color: #e0e0e0;
    font-size: 12px;
}
"""

EDITOR_TOOLBAR_ROW_STYLE = """
QWidget#toolbarRow {
    background-color: #252525;
    border-bottom: 1px solid rgba(95, 201, 138, 30);
}
"""

EDITOR_TOOL_BUTTON_STYLE = """
QToolButton {
    background-color: transparent;
    border: 1px solid rgba(255, 255, 255, 25);
    border-radius: 6px;
    padding: 5px 10px;
    color: #ccc;
    font-size: 12px;
    min-width: 28px;
    min-height: 28px;
}
QToolButton:hover {
    background-color: rgba(95, 201, 138, 50);
    border-color: rgba(95, 201, 138, 120);
    color: #fff;
}
QToolButton:checked {
    background-color: rgba(95, 201, 138, 70);
    border-color: #5FC98A;
    color: #fff;
}
QToolButton:pressed {
    background-color: rgba(95, 201, 138, 100);
    border-color: #5FC98A;
    color: #fff;
}
"""

EDITOR_PUSH_BUTTON_STYLE = """
QPushButton {
    background-color: #3a3a3a;
    border: 1px solid rgba(255, 255, 255, 25);
    border-radius: 5px;
    padding: 5px 14px;
    color: #ccc;
    font-size: 12px;
}
QPushButton:hover {
    background-color: rgba(95, 201, 138, 50);
    border-color: rgba(95, 201, 138, 120);
    color: #fff;
}
QPushButton:pressed {
    background-color: #2c2c2c;
    border-color: rgba(95, 201, 138, 160);
    color: #fff;
}
QPushButton:disabled {
    background-color: #262626;
    color: #555;
    border-color: rgba(255, 255, 255, 8);
}
"""

EDITOR_OPTION_TOGGLE_STYLE = """
QToolButton {
    background-color: transparent;
    border: 1px solid rgba(255, 255, 255, 25);
    border-radius: 5px;
    padding: 3px 8px;
    color: #ccc;
    font-size: 11px;
    min-width: 24px;
    min-height: 22px;
}
QToolButton:hover {
    background-color: rgba(95, 201, 138, 50);
    border-color: rgba(95, 201, 138, 120);
    color: #fff;
}
QToolButton:checked {
    background-color: rgba(95, 201, 138, 70);
    border-color: #5FC98A;
    color: #fff;
}
QToolButton:pressed {
    background-color: rgba(95, 201, 138, 100);
    border-color: #5FC98A;
    color: #fff;
}
"""

EDITOR_STATUS_STYLE = """
QLabel#statusLabel {
    color: #999;
    font-size: 11px;
    padding: 4px 10px;
    background-color: #222;
    border-top: 1px solid rgba(255, 255, 255, 10);
}
"""

EDITOR_OPTIONS_STYLE = """
QWidget#optionsArea {
    background-color: #282828;
    border-bottom: 1px solid rgba(255, 255, 255, 15);
}
QLabel#optionLabel {
    color: #aaa;
    font-size: 11px;
    padding: 0 6px;
}
QSlider::groove:horizontal {
    border: none;
    height: 4px;
    background-color: #444;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background-color: #5FC98A;
    border: none;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background-color: #5FC98A;
    border-radius: 2px;
}
QComboBox {
    background-color: #353535;
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 5px;
    padding: 5px 28px 5px 10px;
    color: #ccc;
    font-size: 12px;
        min-width: 80px;
}
QComboBox:hover {
    border-color: rgba(95, 201, 138, 140);
    background-color: #3a3a3a;
}
QComboBox:focus {
    border-color: #5FC98A;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #999;
    width: 0;
    height: 0;
    margin-right: 6px;
}
QComboBox QAbstractItemView {
    background-color: #2d2d2d;
    border: 1px solid rgba(255, 255, 255, 20);
    border-radius: 5px;
    outline: 0px;
    padding: 4px 0;
    color: #ccc;
    font-size: 12px;
        selection-background-color: rgba(95, 201, 138, 50);
    selection-color: #fff;
}
QComboBox QAbstractItemView::item {
    padding: 7px 14px;
    min-height: 28px;
}
QComboBox QAbstractItemView::item:hover {
    background-color: rgba(95, 201, 138, 25);
    color: #fff;
}
QSpinBox {
    background-color: #353535;
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 5px;
    padding: 5px 10px;
    color: #ccc;
    font-size: 12px;
}
QSpinBox:hover {
    border-color: rgba(95, 201, 138, 140);
    background-color: #3a3a3a;
}
QSpinBox:focus {
    border-color: #5FC98A;
}
"""

# Preset color palette: 4 columns × 4 rows
_SWATCH_COLORS = [
    # Row 1
    ("#FF4444", "Red"),     ("#FF8800", "Orange"),  ("#FFFF00", "Yellow"),  ("#5FC98A", "Green"),
    # Row 2
    ("#00CCCC", "Cyan"),    ("#4488FF", "Blue"),    ("#8844FF", "Purple"),  ("#FF44AA", "Pink"),
    # Row 3
    ("#FFFFFF", "White"),   ("#CCCCCC", "LtGray"),  ("#888888", "Gray"),   ("#444444", "DkGray"),
    # Row 4
    ("#000000", "Black"),
]

_SWATCH_COLS = 4
_SWATCH_SIZE = 26  # diameter
_SWATCH_PAD = 4
_SWATCH_GAP = 2
