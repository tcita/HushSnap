"""
HushSnap UI style constants module.
Defines dimensions, colors, and CSS styles for settings dialog and other UI components.
"""

# Window/dialog dimensions (pixels)
SETTINGS_DIALOG_WIDTH = 460
SETTINGS_CAPTURE_DIALOG_MIN_WIDTH = 340

# Color definitions
SETTINGS_ERROR_COLOR = "#B00020"
SETTINGS_BG_COLOR = "#F2F2F2"
SETTINGS_CARD_BG = "#F9F9F9"
SETTINGS_CARD_BORDER = "#E5E5E5"
SETTINGS_LABEL_COLOR = "#333333"
SETTINGS_SUBTITLE_COLOR = "#999999"
SETTINGS_KBD_BORDER = "#D5D5D5"
SETTINGS_KBD_BG = "#F0F0F0"
SETTINGS_KBD_TEXT = "#555555"
SETTINGS_PLUS_COLOR = "#C0C0C0"
SETTINGS_GHOST_BORDER = "#D5D5D5"
SETTINGS_GHOST_TEXT = "#777777"
SETTINGS_GHOST_HOVER_BG = "#F0F0F0"
SETTINGS_HEADER_BG = "#FFFFFF"
SETTINGS_HEADER_BORDER = "#EBEBEB"
SETTINGS_HEADER_TEXT = "#333333"

# Dialog-level stylesheet
DIALOG_STYLE = f"""
QDialog {{
    background: {SETTINGS_BG_COLOR};
}}
"""

# Individual setting card
SETTING_CARD_STYLE = f"""
QFrame#settingCard {{
    background: {SETTINGS_CARD_BG};
    border: 0.5px solid {SETTINGS_CARD_BORDER};
    border-radius: 8px;
}}
"""

# Header bar
HEADER_BAR_STYLE = f"""
QFrame#headerBar {{
    background: {SETTINGS_HEADER_BG};
    border-bottom: 0.5px solid {SETTINGS_HEADER_BORDER};
}}
"""

HEADER_TITLE_STYLE = f"""
QLabel#headerTitle {{
    color: {SETTINGS_HEADER_TEXT};
    font-size: 14px;
    font-weight: 600;
    border: none;
    background: transparent;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
"""

HEADER_ICON_STYLE = f"""
QLabel#headerIcon {{
    color: {SETTINGS_SUBTITLE_COLOR};
    font-size: 16px;
    border: none;
    background: transparent;
}}
"""

# Row label (bold, main title of each setting)
ROW_LABEL_STYLE = f"""
QLabel#rowLabel {{
    color: {SETTINGS_LABEL_COLOR};
    font-size: 13px;
    font-weight: 600;
    border: none;
    background: transparent;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
"""

# Subtitle under label
SUBTITLE_STYLE = f"""
QLabel#subtitle {{
    color: {SETTINGS_SUBTITLE_COLOR};
    font-size: 12px;
    border: none;
    background: transparent;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
"""

# Individual kbd pill (one per key)
KBD_PILL_STYLE = f"""
QLabel#kbdPill {{
    border: 1px solid {SETTINGS_KBD_BORDER};
    border-radius: 4px;
    background: {SETTINGS_KBD_BG};
    padding: 3px 7px;
    font-family: "Consolas", "Segoe UI", monospace;
    font-size: 12px;
    color: {SETTINGS_KBD_TEXT};
}}
"""

# "+" separator between kbd pills
PLUS_LABEL_STYLE = f"""
QLabel#plusLabel {{
    color: {SETTINGS_PLUS_COLOR};
    font-size: 12px;
    font-family: "Consolas", "Segoe UI", monospace;
    border: none;
    background: transparent;
    padding: 0 1px;
}}
"""

# Ghost button (transparent, thin border)
GHOST_BUTTON_STYLE = f"""
QPushButton#ghostButton {{
    background: transparent;
    border: 0.5px solid {SETTINGS_GHOST_BORDER};
    border-radius: 4px;
    padding: 4px 10px;
    color: {SETTINGS_GHOST_TEXT};
    font-size: 11px;
    margin-left: 6px;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
QPushButton#ghostButton:hover {{
    background: {SETTINGS_GHOST_HOVER_BG};
    border-color: #BBBBBB;
}}
QPushButton#ghostButton:pressed {{
    background: #EBEBEB;
}}
"""

# Status label
STATUS_LABEL_STYLE = f"""
QLabel#statusLabel {{
    color: {SETTINGS_LABEL_COLOR};
    font-size: 12px;
    padding: 0 4px;
    border: none;
    background: transparent;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
"""

# Capture dialog styles
CAPTURE_DIALOG_STYLE = f"""
QDialog {{
    background: #FFFFFF;
}}
"""

CAPTURE_INPUT_STYLE = f"""
QLineEdit {{
    border: 1px solid {SETTINGS_CARD_BORDER};
    border-radius: 6px;
    padding: 10px 14px;
    font-family: "Consolas", "Segoe UI", monospace;
    font-size: 18px;
    color: {SETTINGS_LABEL_COLOR};
    background: {SETTINGS_KBD_BG};
}}
QLineEdit:focus {{
    border-color: #A0A0A0;
}}
"""

CAPTURE_HINT_STYLE = f"""
QLabel#captureHint {{
    color: #999;
    font-size: 12px;
    border: none;
    background: transparent;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
"""

CAPTURE_FEEDBACK_STYLE = f"""
QLabel#captureFeedback {{
    font-size: 12px;
    border: none;
    background: transparent;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
"""

CAPTURE_SAVE_BUTTON_STYLE = f"""
QPushButton#saveButton {{
    background: #333;
    border: none;
    border-radius: 5px;
    padding: 7px 20px;
    color: white;
    font-size: 12px;
    font-weight: 500;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
QPushButton#saveButton:hover {{
    background: #444;
}}
QPushButton#saveButton:pressed {{
    background: #222;
}}
QPushButton#saveButton:disabled {{
    background: #CCC;
    color: #999;
}}
"""

CAPTURE_CANCEL_BUTTON_STYLE = f"""
QPushButton#cancelButton {{
    background: transparent;
    border: 0.5px solid {SETTINGS_GHOST_BORDER};
    border-radius: 5px;
    padding: 7px 16px;
    color: {SETTINGS_GHOST_TEXT};
    font-size: 12px;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
QPushButton#cancelButton:hover {{
    background: #F5F5F5;
}}
QPushButton#cancelButton:pressed {{
    background: #EBEBEB;
}}
"""


COMBOBOX_STYLE = f"""
QComboBox#settingsCombo {{
    background: #FFFFFF;
    border: 0.5px solid {SETTINGS_GHOST_BORDER};
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 13px;
    color: {SETTINGS_LABEL_COLOR};
    min-width: 140px;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
QComboBox#settingsCombo:hover {{
    border-color: #BBBBBB;
}}
QComboBox#settingsCombo::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox#settingsCombo::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {SETTINGS_SUBTITLE_COLOR};
    width: 0;
    height: 0;
    subcontrol-position: center;
}}
QComboBox#settingsCombo QAbstractItemView {{
    background-color: #FFFFFF;
    border: 0.5px solid {SETTINGS_GHOST_BORDER};
    border-radius: 6px;
    outline: 0px;
    selection-background-color: {SETTINGS_GHOST_HOVER_BG};
    selection-color: {SETTINGS_LABEL_COLOR};
    color: {SETTINGS_LABEL_COLOR};
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
QComboBox#settingsCombo QAbstractItemView::item {{
    padding: 7px 14px;
    min-height: 30px;
    color: {SETTINGS_LABEL_COLOR};
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
QComboBox#settingsCombo QAbstractItemView::item:hover {{
    background-color: {SETTINGS_GHOST_HOVER_BG};
}}
"""

# Section header label (uppercase, muted)
SECTION_HEADER_STYLE = f"""
QLabel#sectionHeader {{
    color: {SETTINGS_SUBTITLE_COLOR};
    font-size: 11px;
    font-weight: 600;
    padding: 10px 4px 4px 4px;
    border: none;
    background: transparent;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
"""

# Message box style (for alerts)
MESSAGE_BOX_STYLE = f"""
QMessageBox {{
    background-color: {SETTINGS_BG_COLOR};
}}
QMessageBox QLabel {{
    color: {SETTINGS_LABEL_COLOR};
    font-size: 13px;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
QMessageBox QPushButton {{
    background-color: #FFFFFF;
    border: 1px solid {SETTINGS_GHOST_BORDER};
    border-radius: 4px;
    padding: 5px 15px;
    color: {SETTINGS_LABEL_COLOR};
    font-size: 12px;
    min-width: 60px;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
QMessageBox QPushButton:hover {{
    background-color: {SETTINGS_GHOST_HOVER_BG};
}}
"""

# Modern Dark Context Menu Style
MODERN_MENU_STYLE = """
QMenu {
    background-color: #252525;
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 10px;
    padding: 6px 0px;
    margin: 12px;
}
QMenu::item {
    padding: 8px 32px 8px 16px;
    font-size: 13px;
    color: #e0e0e0;
    background-color: transparent;
    border-radius: 6px;
    margin: 2px 8px;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}
QMenu::item:selected {
    background-color: rgba(95, 201, 138, 50);
    color: #ffffff;
}
QMenu::item:disabled {
    color: #666666;
}
QMenu::separator {
    height: 1px;
    background: rgba(255, 255, 255, 15);
    margin: 4px 8px;
}
"""
