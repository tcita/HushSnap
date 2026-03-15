"""
HushSnap 系统托盘模块
负责创建系统托盘图标、上下文菜单（右键菜单）以及托盘图标的交互逻辑。
"""

from PyQt6 import QtGui, QtWidgets

from ..config import get_resource_dir
from ..constants import APP_ICON_FILENAME


def create_tray(app, translate, on_trigger, on_open_settings, on_open_config_dir, on_quit):
    """
    初始化并创建系统托盘图标及其配套菜单。
    
    Args:
        app (QApplication): 应用程序实例。
        translate (callable): 翻译函数。
        on_trigger (callable): 点击托盘触发截图的回调。
        on_open_settings (callable): 打开设置窗口的回调。
        on_open_config_dir (callable): 打开配置目录的回调。
        on_quit (callable): 退出程序的回调。
        
    Returns:
        tuple: (tray_icon, settings_action) 返回托盘对象和设置菜单项，以便后续动态操作。
    """
    # 加载托盘图标
    tray_icon_image = QtGui.QIcon(str(get_resource_dir() / APP_ICON_FILENAME))
    app.setWindowIcon(tray_icon_image)
    
    # 创建托盘图标对象
    tray_icon = QtWidgets.QSystemTrayIcon(tray_icon_image, app)
    # 创建右键上下文菜单
    tray_menu = QtWidgets.QMenu()
    tray_icon.setContextMenu(tray_menu)
    tray_icon.show()

    def on_tray_icon_activated(reason):
        """
        处理托盘图标的交互事件。
        如果用户单击（Trigger）图标，则直接执行截图流程。
        """
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen:
                dpr = screen.devicePixelRatio()
                # 抓取当前屏幕状态
                pixmap = screen.grabWindow(0)
                pixmap.setDevicePixelRatio(dpr)
                # 触发截图选取界面
                on_trigger(pixmap)

    # 绑定激活事件（如单击）
    tray_icon.activated.connect(on_tray_icon_activated)

    # 添加菜单项
    settings_action = tray_menu.addAction(translate("menu_settings"))
    if on_open_settings is not None:
        settings_action.triggered.connect(on_open_settings)
    
    config_dir_action = tray_menu.addAction(translate("menu_open_install_dir"))
    config_dir_action.triggered.connect(on_open_config_dir)

    tray_menu.addSeparator()
    
    quit_action = tray_menu.addAction(translate("menu_quit"))
    quit_action.triggered.connect(on_quit)

    return tray_icon, settings_action
