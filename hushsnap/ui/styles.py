"""
HushSnap UI 样式常量模块
定义设置对话框及其他 UI 组件的尺寸、颜色和 CSS 样式。
"""

# 窗口与对话框尺寸 (像素)
# 热键捕获对话框的最小宽度
SETTINGS_CAPTURE_DIALOG_MIN_WIDTH = 340
# 设置窗口内按钮的标准高度
SETTINGS_BUTTON_HEIGHT = 24
# “修改热键”按钮的最大宽度
SETTINGS_CHANGE_BUTTON_MAX_WIDTH = 140
# “卸载”按钮的最大宽度
SETTINGS_UNINSTALL_BUTTON_MAX_WIDTH = 84

# 颜色定义
# 用于显示错误消息的红色 (Material Design Error Color)
SETTINGS_ERROR_COLOR = "#B00020"

# QSS (Qt Style Sheets) 样式
# 卸载按钮的特殊红色样式，以起到警示作用
SETTINGS_UNINSTALL_BUTTON_STYLE = (
    "QPushButton { background-color: #C62828; color: white; border: 1px solid #9E1F1F; padding: 2px 8px; }"
    "QPushButton:hover { background-color: #B71C1C; }"
)
