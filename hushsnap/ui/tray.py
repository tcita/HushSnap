"""
HushSnap system tray module.
Creates tray icon, context menu (right-click menu), and tray interaction logic.
"""

import io
from pathlib import Path
from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import get_resource_dir
from ..constants import APP_ICON_FILENAME
from ..dpi import grab_all_screens
from .styles import BRAND_GREEN


def _apply_round_mask(pil_img, radius_fraction=0.18):
    """Apply a rounded-corner alpha mask to a PIL RGBA image.

    Draws a rounded-rectangle mask so that the four corner pixels (and the
    anti-aliased fringe) are fully transparent.  This prevents Windows from
    rendering the residual dark anti-alias pixels in the corners as opaque
    blobs on a light (e.g. white) taskbar/menu background.

    Args:
        pil_img (PIL.Image.Image): Source image, must be RGBA.
        radius_fraction (float): Corner radius as a fraction of the shorter
            edge.  0.18 ≈ the visual rounding of the HushSnap logo.

    Returns:
        PIL.Image.Image: A new RGBA image with corners masked to alpha=0.
    """
    from PIL import Image, ImageDraw
    w, h = pil_img.size
    r = max(1, int(min(w, h) * radius_fraction))

    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)

    result = pil_img.copy()
    # Multiply existing alpha with the mask so we never *add* opacity
    orig_alpha = pil_img.split()[3]
    from PIL import ImageChops
    new_alpha = ImageChops.multiply(orig_alpha, mask)
    result.putalpha(new_alpha)
    return result


def load_app_icon(ico_path: Path) -> QtGui.QIcon:
    """Load a multi-size QIcon from an .ico file with correct corner transparency.

    Qt (and Windows) sometimes retain semi-transparent anti-alias fringe pixels
    in the corners of ICO frames.  On a light taskbar or menu background these
    dark pixels appear as four sharp 'ears', making the icon look like a black
    rounded-rectangle placed on a white canvas.

    This function:
    1. Opens every size frame stored in the .ico with PIL.
    2. Applies a rounded-corner alpha mask (_apply_round_mask) so the four
       corners are completely transparent.
    3. Converts each masked frame to QPixmap and adds it to a QIcon.

    Falls back to a plain QIcon(str(ico_path)) if PIL is not available or the
    file cannot be read.
    """
    try:
        from PIL import Image
    except ImportError:
        return QtGui.QIcon(str(ico_path))

    try:
        with open(ico_path, "rb") as f:
            raw = f.read()

        ico_img = Image.open(io.BytesIO(raw))
        sizes = sorted(ico_img.ico.sizes(), key=lambda s: s[0])

        q_icon = QtGui.QIcon()
        for sz in sizes:
            frame = ico_img.ico.getimage(sz).convert("RGBA")
            masked = _apply_round_mask(frame)

            data = masked.tobytes("raw", "RGBA")
            qimg = QtGui.QImage(
                data,
                masked.width,
                masked.height,
                QtGui.QImage.Format.Format_RGBA8888,
            ).copy()  # .copy() detaches from the Python buffer
            q_icon.addPixmap(QtGui.QPixmap.fromImage(qimg))

        return q_icon if not q_icon.isNull() else QtGui.QIcon(str(ico_path))
    except Exception:
        # Silently fall back to the unmasked icon
        return QtGui.QIcon(str(ico_path))


def load_tinted_pixmap(icon_path, color_hex):
    """
    Load an SVG icon as a QPixmap and tint it using CompositionMode_SourceIn.
    
    Args:
        icon_path (Path): Path to the SVG file.
        color_hex (str): Tint color in hex format (e.g. '#888888').
        
    Returns:
        QPixmap: Tinted pixmap.
    """
    icon = QtGui.QIcon(str(icon_path))
    pixmap = icon.pixmap(16, 16)
    if pixmap.isNull():
        return pixmap
    painter = QtGui.QPainter(pixmap)
    painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QtGui.QColor(color_hex))
    painter.end()
    return pixmap


class MenuItemWidget(QtWidgets.QWidget):
    """
    Custom widget designed for tray menu items to support custom QSS,
    beautiful layout padding, tinted icons, and rounded shortcut badges.
    """
    triggered = QtCore.pyqtSignal()
    
    def __init__(self, text, icon_path, color_hex, shortcut="", is_danger=False, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.is_danger = is_danger
        self.setObjectName("MenuItemWidget")
        
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        
        # Icon label
        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setFixedSize(16, 16)
        if icon_path:
            pixmap = load_tinted_pixmap(icon_path, color_hex)
            if not pixmap.isNull():
                self.icon_label.setPixmap(pixmap)
        layout.addWidget(self.icon_label)
        
        # Text label
        self.text_label = QtWidgets.QLabel(text)
        layout.addWidget(self.text_label)
        
        layout.addStretch()
        
        # Shortcut badge
        self.shortcut_label = QtWidgets.QLabel()
        self.shortcut_label.setStyleSheet(
            "color: #666666; font-size: 11px; padding: 2px 6px; "
            "background-color: #F0F0F0; border-radius: 4px; font-weight: 500;"
            "font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
        )
        layout.addWidget(self.shortcut_label)
        
        if shortcut:
            self.shortcut_label.setText(shortcut)
        else:
            self.shortcut_label.hide()
            
        self.setLayout(layout)
        
        # QSS Stylesheets for hover highlight effects
        self.normal_style = """
            #MenuItemWidget {
                background-color: transparent;
                border-radius: 6px;
            }
        """
        self.hover_style = """
            #MenuItemWidget {
                background-color: #F0F0F0;
                border-radius: 6px;
            }
        """
        self.setStyleSheet(self.normal_style)
        
        # Text tinting
        if is_danger:
            self.text_label.setStyleSheet("color: #D32F2F; font-size: 13px; font-weight: bold; background-color: transparent;"
                " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;")
        else:
            self.text_label.setStyleSheet("color: #333333; font-size: 13px; background-color: transparent;"
                " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;")
            
    def enterEvent(self, event):
        self.setStyleSheet(self.hover_style)
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        self.setStyleSheet(self.normal_style)
        super().leaveEvent(event)
        
    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.triggered.emit()
        super().mouseReleaseEvent(event)


class StyledMenuAction(QtWidgets.QWidgetAction):
    """
    Custom QWidgetAction that integrates the MenuItemWidget.
    Supports standard action signals and dynamic shortcut updates.
    """
    def __init__(self, text, icon_path, color_hex, shortcut="", is_danger=False, on_triggered=None, parent_menu=None):
        super().__init__(parent_menu)
        self.widget = MenuItemWidget(text, icon_path, color_hex, shortcut, is_danger, parent_menu)
        self.setDefaultWidget(self.widget)
        
        # Propagate widget click to standard QAction.trigger()
        self.widget.triggered.connect(self.trigger)
        
        if on_triggered:
            self.triggered.connect(on_triggered)
            
        # Ensure context menu closes after click
        self.triggered.connect(parent_menu.close)
            
    def update_shortcut(self, shortcut):
        """Update the text of the shortcut badge dynamically."""
        if shortcut:
            self.widget.shortcut_label.setText(shortcut)
            self.widget.shortcut_label.show()
        else:
            self.widget.shortcut_label.hide()


def create_tray(
    app,
    translate,
    on_trigger,
    on_open_settings,
    on_open_config_dir,
    on_quit,
    initial_hotkey="",
):
    """
    Initialize and create the system tray icon and its menu.
    
    Args:
        app (QApplication): Application instance.
        translate (callable): Translation function.
        on_trigger (callable): Callback to trigger screenshot from tray.
        on_open_settings (callable): Callback to open settings window.
        on_open_config_dir (callable): Callback to open config directory.
        on_quit (callable): Callback to quit application.
        initial_hotkey (str): Initial main screenshot hotkey name.
        
    Returns:
        tuple: (tray_icon, settings_action) for later dynamic operations.
    """
    # Load tray icon with rounded-corner alpha mask applied to every ICO frame.
    # This prevents dark anti-alias pixels in the four corners from appearing as
    # white 'ears' on a light taskbar/menu background (common in MSIX packages).
    tray_icon_image = load_app_icon(get_resource_dir() / APP_ICON_FILENAME)
    app.setWindowIcon(tray_icon_image)
    
    # Create tray icon object.
    tray_icon = QtWidgets.QSystemTrayIcon(tray_icon_image, app)
    # Tooltip shown when hovering the tray icon (otherwise Windows renders an empty bubble).
    tray_icon.setToolTip(translate("tray_tooltip"))
    # Create right-click context menu.
    tray_menu = QtWidgets.QMenu()
    tray_icon.setContextMenu(tray_menu)
    # Tray is shown after OCR warmup completes (see app.py).
    # Delaying the tray gives a subtle "still loading" signal to the user.

    # Style QMenu window with modern light theme, translucent corners, and margin for shadow
    tray_menu.setStyleSheet("""
        QMenu {
            background-color: #FFFFFF;
            border: 1px solid #E5E5E5;
            border-radius: 10px;
            padding: 8px;
            margin: 10px;
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
    """)
    tray_menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

    # Create a premium soft drop shadow effect
    shadow = QtWidgets.QGraphicsDropShadowEffect(tray_menu)
    shadow.setBlurRadius(15)
    shadow.setColor(QtGui.QColor(0, 0, 0, 45))
    shadow.setOffset(0, 3)
    tray_menu.setGraphicsEffect(shadow)

    # Resolve local icon directory paths
    icons_dir = Path(__file__).resolve().parent / "icons"
    screenshot_icon_path = icons_dir / "screenshot.svg"
    settings_icon_path = icons_dir / "settings.svg"
    folder_icon_path = icons_dir / "folder.svg"
    power_icon_path = icons_dir / "power.svg"

    def on_tray_icon_activated(reason):
        """
        Handle tray icon activation.
        If user single-clicks (Trigger), start screenshot flow directly.
        """
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            screens_and_pixmaps = grab_all_screens()
            if screens_and_pixmaps:
                # Trigger screenshot selection UI.
                on_trigger(screens_and_pixmaps)

    # Bind activation event (e.g. single click).
    tray_icon.activated.connect(on_tray_icon_activated)

    # Define delay capture helper
    def do_capture():
        screens_and_pixmaps = grab_all_screens()
        if screens_and_pixmaps:
            on_trigger(screens_and_pixmaps)

    # 1. Screenshot Action
    def on_screenshot_triggered():
        QtCore.QTimer.singleShot(200, do_capture)

    screenshot_action = StyledMenuAction(
        text=translate("menu_screenshot"),
        icon_path=screenshot_icon_path,
        color_hex=BRAND_GREEN,
        shortcut=initial_hotkey,
        is_danger=False,
        on_triggered=on_screenshot_triggered,
        parent_menu=tray_menu
    )
    tray_menu.addAction(screenshot_action)

    tray_menu.addSeparator()

    # 2. Settings Action
    settings_action = StyledMenuAction(
        text=translate("menu_settings"),
        icon_path=settings_icon_path,
        color_hex="#888888",
        shortcut="",
        is_danger=False,
        on_triggered=on_open_settings,
        parent_menu=tray_menu
    )
    tray_menu.addAction(settings_action)

    # 3. Config Directory Action
    config_dir_action = StyledMenuAction(
        text=translate("menu_open_install_dir"),
        icon_path=folder_icon_path,
        color_hex="#888888",
        shortcut="",
        is_danger=False,
        on_triggered=on_open_config_dir,
        parent_menu=tray_menu
    )
    tray_menu.addAction(config_dir_action)

    tray_menu.addSeparator()
    
    # 4. Quit Action
    quit_action = StyledMenuAction(
        text=translate("menu_quit"),
        icon_path=power_icon_path,
        color_hex="#D32F2F",
        shortcut="",
        is_danger=True,
        on_triggered=on_quit,
        parent_menu=tray_menu
    )
    tray_menu.addAction(quit_action)

    # Expose dynamic text and shortcut updater method
    def update_shortcuts(hotkey):
        screenshot_action.update_shortcut(hotkey)

    tray_icon.update_shortcuts = update_shortcuts

    return tray_icon, settings_action
