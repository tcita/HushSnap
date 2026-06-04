"""
HushSnap system tray module.
Creates tray icon, context menu (right-click menu), and tray interaction logic.
"""

from pathlib import Path
from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import get_resource_dir
from ..constants import APP_ICON_FILENAME


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
            "color: #888888; font-size: 11px; padding: 2px 6px; "
            "background-color: #2b2b2b; border-radius: 4px; font-weight: 500;"
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
                background-color: #2e2e2e;
                border-radius: 6px;
            }
        """
        self.setStyleSheet(self.normal_style)
        
        # Text tinting
        if is_danger:
            self.text_label.setStyleSheet("color: #e05555; font-size: 13px; font-weight: bold; background-color: transparent;"
                " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;")
        else:
            self.text_label.setStyleSheet("color: #e8e8e8; font-size: 13px; background-color: transparent;"
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
    # Load tray icon.
    tray_icon_image = QtGui.QIcon(str(get_resource_dir() / APP_ICON_FILENAME))
    app.setWindowIcon(tray_icon_image)
    
    # Create tray icon object.
    tray_icon = QtWidgets.QSystemTrayIcon(tray_icon_image, app)
    # Create right-click context menu.
    tray_menu = QtWidgets.QMenu()
    tray_icon.setContextMenu(tray_menu)
    # Tray is shown after OCR warmup completes (see app.py).
    # Delaying the tray gives a subtle "still loading" signal to the user.

    # Style QMenu window with modern dark theme and translucent corners
    tray_menu.setStyleSheet("""
        QMenu {
            background-color: #1e1e1e;
            border: 1px solid #3a3a3a;
            border-radius: 10px;
            padding: 4px;
            font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
        }
        QMenu::item {
            font-family: "Microsoft YaHei", "Microsoft JhengHei", sans-serif;
        }
        QMenu::separator {
            height: 1px;
            background: #333333;
            margin: 3px 8px;
        }
    """)
    tray_menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
    # FramelessWindowHint helps render anti-aliased rounded corners nicely on Windows
    tray_menu.setWindowFlags(
        tray_menu.windowFlags() 
        | QtCore.Qt.WindowType.FramelessWindowHint 
        | QtCore.Qt.WindowType.NoDropShadowWindowHint
    )

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
            screen = QtWidgets.QApplication.primaryScreen()
            if screen:
                dpr = screen.devicePixelRatio()
                # Capture current screen state.
                pixmap = screen.grabWindow(0)
                pixmap.setDevicePixelRatio(dpr)
                # Trigger screenshot selection UI.
                on_trigger(pixmap)

    # Bind activation event (e.g. single click).
    tray_icon.activated.connect(on_tray_icon_activated)

    # Define delay capture helper
    def do_capture():
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            dpr = screen.devicePixelRatio()
            pixmap = screen.grabWindow(0)
            pixmap.setDevicePixelRatio(dpr)
            on_trigger(pixmap)

    # 1. Screenshot Action
    def on_screenshot_triggered():
        QtCore.QTimer.singleShot(200, do_capture)

    screenshot_action = StyledMenuAction(
        text=translate("menu_screenshot"),
        icon_path=screenshot_icon_path,
        color_hex="#888888",
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
        color_hex="#e05555",
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
