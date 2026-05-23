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
}}
"""

# Subtitle under label
SUBTITLE_STYLE = f"""
QLabel#subtitle {{
    color: {SETTINGS_SUBTITLE_COLOR};
    font-size: 12px;
    border: none;
    background: transparent;
}}
"""

# Individual kbd pill (one per key)
KBD_PILL_STYLE = f"""
QLabel#kbdPill {{
    border: 1px solid {SETTINGS_KBD_BORDER};
    border-radius: 4px;
    background: {SETTINGS_KBD_BG};
    padding: 2px 7px;
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
    padding: 3px 10px;
    color: {SETTINGS_GHOST_TEXT};
    font-size: 11px;
    margin-left: 6px;
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
}}
"""

CAPTURE_FEEDBACK_STYLE = f"""
QLabel#captureFeedback {{
    font-size: 12px;
    border: none;
    background: transparent;
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
}}
QPushButton#cancelButton:hover {{
    background: #F5F5F5;
}}
QPushButton#cancelButton:pressed {{
    background: #EBEBEB;
}}
"""


COMBOBOX_STYLE = f"""
QComboBox {{
    background: #FFFFFF;
    border: 0.5px solid {SETTINGS_GHOST_BORDER};
    border-radius: 4px;
    padding: 3px 10px;
    color: {SETTINGS_GHOST_TEXT};
    font-size: 11px;
    margin-left: 6px;
    min-width: 120px;
}}
QComboBox:hover {{
    background: {SETTINGS_GHOST_HOVER_BG};
    border-color: #BBBBBB;
}}
QComboBox QAbstractItemView {{
    background-color: #FFFFFF;
    border: 0.5px solid {SETTINGS_GHOST_BORDER};
    selection-background-color: {SETTINGS_GHOST_HOVER_BG};
    selection-color: {SETTINGS_LABEL_COLOR};
    color: {SETTINGS_GHOST_TEXT};
    outline: 0px;
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
}}
QMessageBox QPushButton {{
    background-color: #FFFFFF;
    border: 1px solid {SETTINGS_GHOST_BORDER};
    border-radius: 4px;
    padding: 5px 15px;
    color: {SETTINGS_LABEL_COLOR};
    font-size: 12px;
    min-width: 60px;
}}
QMessageBox QPushButton:hover {{
    background-color: {SETTINGS_GHOST_HOVER_BG};
}}
"""
