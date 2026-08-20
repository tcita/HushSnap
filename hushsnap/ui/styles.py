"""
HushSnap UI style constants module.
Defines dimensions, colors, and CSS styles for settings dialog and other UI components.
"""

from PyQt6 import QtCore, QtGui, QtWidgets

# Window/dialog dimensions (pixels)
SETTINGS_DIALOG_WIDTH = 460
SETTINGS_CAPTURE_DIALOG_MIN_WIDTH = 400

# Color definitions

# ── Brand identity colour ──────────────────────────────────────────
# Single source of truth for HushSnap green.
# Used across capture selection, toggle switches, button accents,
# progress bars, icon drawing, and web landing pages.
BRAND_GREEN = "#5FC98A"
BRAND_GREEN_RGB = (95, 201, 138)  # for QColor(*BRAND_GREEN_RGB, alpha)
# Brand green shaded down for primary-action buttons (hover/press states):
# deepened one shade for resting/press contrast, two shades for pressed.
BRAND_GREEN_DEEP = "#4AB87A"
BRAND_GREEN_DEEPER = "#3DA86B"

SETTINGS_ERROR_COLOR = "#B00020"
# Amber for soft warnings (e.g. hotkey captures a conflict-prone combo):
# distinct from brand green (safe) — the only risk signal we now surface.
SETTINGS_WARNING_COLOR = "#E8941A"
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
    font-size: 15px;
    font-weight: 600;
    border: none;
    background: transparent;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
"""

HEADER_ICON_STYLE = f"""
QLabel#headerIcon {{
    color: {SETTINGS_SUBTITLE_COLOR};
    font-size: 17px;
    border: none;
    background: transparent;
}}
"""

# Row label (bold, main title of each setting)
ROW_LABEL_STYLE = f"""
QLabel#rowLabel {{
    color: {SETTINGS_LABEL_COLOR};
    font-size: 14px;
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
    font-size: 13px;
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
    font-size: 13px;
    color: {SETTINGS_KBD_TEXT};
}}
"""

# "+" separator between kbd pills
PLUS_LABEL_STYLE = f"""
QLabel#plusLabel {{
    color: {SETTINGS_PLUS_COLOR};
    font-size: 13px;
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
    font-size: 12px;
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
    font-size: 13px;
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
    font-size: 19px;
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
    font-size: 13px;
    border: none;
    background: transparent;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
"""

CAPTURE_FEEDBACK_STYLE = f"""
QLabel#captureFeedback {{
    font-size: 13px;
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
    font-size: 13px;
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
    font-size: 13px;
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
    font-size: 14px;
    color: {SETTINGS_LABEL_COLOR};
    min-width: 120px;
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
    font-size: 14px;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
QComboBox#settingsCombo QAbstractItemView::item {{
    padding: 7px 14px;
    min-height: 30px;
    color: {SETTINGS_LABEL_COLOR};
    font-size: 14px;
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
    font-size: 12px;
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
    font-size: 14px;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
QMessageBox QPushButton {{
    background-color: #FFFFFF;
    border: 1px solid {SETTINGS_GHOST_BORDER};
    border-radius: 4px;
    padding: 5px 15px;
    color: {SETTINGS_LABEL_COLOR};
    font-size: 13px;
    min-width: 60px;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}}
QMessageBox QPushButton:hover {{
    background-color: {SETTINGS_GHOST_HOVER_BG};
}}
"""

# Modern Dark Context Menu Style.
# Opaque card: the rounded corners are drawn by the OS (DWM corner preference,
# set in RoundedMenu.showEvent) instead of QSS border-radius or a self-drawn
# QPainter card.  A translucent menu (per-pixel alpha) composites black corners
# on systems without DWM composition (VMs without GPU, RDP, basic theme), so we
# stay fully opaque and let the system round the corners.  On Windows 10 the
# corner preference is ignored and the menu renders as a clean square.
MODERN_MENU_STYLE = """
QMenu {
    background-color: #252525;
    border: 1px solid rgba(255, 255, 255, 30);
    padding: 6px 0px;
}
QMenu::item {
    padding: 8px 32px 8px 16px;
    font-size: 14px;
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


# Light menu style for the tray menu.  Same opaque + OS-rounded approach as
# MODERN_MENU_STYLE, just a light theme.
TRAY_MENU_STYLE = """
QMenu {
    background-color: #FFFFFF;
    border: 1px solid #E5E5E5;
    padding: 8px;
    font-size: 13px;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}
QMenu::item {
    font-size: 13px;
    font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
}
QMenu::separator {
    height: 1px;
    background: #EEEEEE;
    margin: 3px 8px;
}
"""


class RoundedMenu(QtWidgets.QMenu):
    """A QMenu with OS-drawn rounded corners.

    The card stays fully opaque (no ``WA_TranslucentBackground``), so there is
    no per-pixel alpha to composite as black corners on systems without DWM
    composition (VMs without a GPU, RDP, Windows basic theme).  On Windows 11
    ``showEvent`` asks DWM to round the popup's corners; on Windows 10 the
    attribute is ignored and the menu is a clean square.

    Defaults to the dark modern look (MODERN_MENU_STYLE).  Pass ``light=True``
    for the light tray theme (TRAY_MENU_STYLE).
    """

    def __init__(self, parent=None, light=False):
        super().__init__(parent)
        self._light = light
        self.setStyleSheet(TRAY_MENU_STYLE if light else MODERN_MENU_STYLE)

    def showEvent(self, event):
        super().showEvent(event)
        self._round_corners_via_dwm()

    def _round_corners_via_dwm(self):
        """Ask DWM to round the popup's corners (Windows 11).

        Uses ``DWMWA_WINDOW_CORNER_PREFERENCE`` = ``DWMWCP_ROUND``.  Silently
        ignored on Windows 10 and non-Windows platforms.
        """
        import sys
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = self.winId()
            if not hwnd:
                return
            dwmapi = ctypes.windll.dwmapi
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            pref = ctypes.c_int(DWMWCP_ROUND)
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(int(hwnd)),
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(pref),
                ctypes.sizeof(pref),
            )
        except Exception:
            pass


def apply_menu_shadow(menu):
    """No-op kept for call-site compatibility.

    The drop shadow used to be painted into a transparent 12px margin via
    QGraphicsDropShadowEffect. On some DWM/GPU combos that transparent
    margin (and the offscreen texture the effect forces) composites as solid
    black, producing a black ring around the menu, so the shadow and its
    margin were removed (see MODERN_MENU_STYLE - no margin). Callers still
    invoke this for uniformity; it now does nothing.
    """
    return

