"""
HushSnap UI style constants module.
Defines dimensions, colors, and CSS styles for settings dialog and other UI components.
"""

# Window/dialog dimensions (pixels)
# Minimum width for hotkey capture dialog
SETTINGS_CAPTURE_DIALOG_MIN_WIDTH = 340
# Standard button height in settings window
SETTINGS_BUTTON_HEIGHT = 24
# Maximum width for "Change Hotkey" button
SETTINGS_CHANGE_BUTTON_MAX_WIDTH = 140
# Maximum width for "Uninstall" button
SETTINGS_UNINSTALL_BUTTON_MAX_WIDTH = 84

# Color definitions
# Red color used for error messages (Material Design error color)
SETTINGS_ERROR_COLOR = "#B00020"

# QSS (Qt Style Sheets)
# Special red style for uninstall button to emphasize caution
SETTINGS_UNINSTALL_BUTTON_STYLE = (
    "QPushButton { background-color: #C62828; color: white; border: 1px solid #9E1F1F; padding: 2px 8px; }"
    "QPushButton:hover { background-color: #B71C1C; }"
)
