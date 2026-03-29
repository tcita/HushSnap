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
# Muted text-link style for uninstall action to keep it visible but low emphasis
SETTINGS_UNINSTALL_BUTTON_STYLE = (
    "QPushButton {"
    " background: transparent;"
    " border: none;"
    " color: #7A7A7A;"
    " padding: 0 2px;"
    "}"
    "QPushButton:hover { color: #5F5F5F; }"
    "QPushButton:pressed { color: #4A4A4A; }"
)
