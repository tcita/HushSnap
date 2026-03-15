"""
HushSnap 设置对话框模块
提供一个用户界面，用于查看当前热键、录制并更改新热键以及启动卸载程序。
"""

from PyQt6 import QtCore, QtWidgets

from ..config import parse_hotkey, update_hotkey_in_config
from .styles import (
    SETTINGS_BUTTON_HEIGHT,
    SETTINGS_CAPTURE_DIALOG_MIN_WIDTH,
    SETTINGS_CHANGE_BUTTON_MAX_WIDTH,
    SETTINGS_ERROR_COLOR,
    SETTINGS_UNINSTALL_BUTTON_MAX_WIDTH,
    SETTINGS_UNINSTALL_BUTTON_STYLE,
)


def _qt_key_to_hotkey_token(key):
    """
    将 Qt 的按键枚举值转换为程序内部使用的热键文本标记。
    
    Args:
        key (int): Qt.Key 值。
        
    Returns:
        str: 对应的文本标记（如 'A', 'F1', 'ESC'），不支持则返回 None。
    """
    if QtCore.Qt.Key.Key_A <= key <= QtCore.Qt.Key.Key_Z:
        return chr(key)
    if QtCore.Qt.Key.Key_0 <= key <= QtCore.Qt.Key.Key_9:
        return chr(key)
    if QtCore.Qt.Key.Key_F1 <= key <= QtCore.Qt.Key.Key_F24:
        return f"F{key - QtCore.Qt.Key.Key_F1 + 1}"

    # 特殊功能键映射
    special_map = {
        QtCore.Qt.Key.Key_Escape: "ESC",
        QtCore.Qt.Key.Key_Tab: "TAB",
        QtCore.Qt.Key.Key_Enter: "ENTER",
        QtCore.Qt.Key.Key_Return: "ENTER",
        QtCore.Qt.Key.Key_Space: "SPACE",
        QtCore.Qt.Key.Key_Left: "LEFT",
        QtCore.Qt.Key.Key_Up: "UP",
        QtCore.Qt.Key.Key_Right: "RIGHT",
        QtCore.Qt.Key.Key_Down: "DOWN",
    }
    return special_map.get(key)


class SettingsDialogController:
    """
    设置对话框控制器类。
    管理设置窗口的生命周期、UI 更新以及用户操作的回调。
    """
    def __init__(self, translate, config_path, hotkey_manager, on_uninstall):
        """
        初始化控制器。
        
        Args:
            translate (callable): 翻译函数。
            config_path (Path): 配置文件路径。
            hotkey_manager (HotkeyManager): 热键管理器实例。
            on_uninstall (callable): 卸载回调。
        """
        self.translate = translate
        self.config_path = config_path
        self.hotkey_manager = hotkey_manager
        self.on_uninstall = on_uninstall
        self._dialog = None
        self._hotkey_label = None

    def _refresh_hotkey_label(self):
        """更新 UI 上显示的热键文本。"""
        if self._hotkey_label is None:
            return
        try:
            self._hotkey_label.setText(
                self.translate("settings_current_hotkey", hotkey=self.hotkey_manager.current_hotkey_name)
            )
        except RuntimeError:
            # 窗口可能已关闭
            self._hotkey_label = None

    def show(self):
        """
        显示设置对话框。如果窗口已存在，则将其置于顶层。
        """
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.raise_()
            self._dialog.activateWindow()
            return

        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle(self.translate("settings_title"))
        dialog.setModal(False)
        dialog.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        # 确保关闭时自动释放内存
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._dialog = dialog

        def clear_settings_dialog(_obj=None):
            self._dialog = None
            self._hotkey_label = None

        dialog.destroyed.connect(clear_settings_dialog)

        layout = QtWidgets.QVBoxLayout(dialog)

        # 当前热键显示区域
        self._hotkey_label = QtWidgets.QLabel("")
        self._hotkey_label.setWordWrap(True)
        layout.addWidget(self._hotkey_label)
        self._refresh_hotkey_label()

        # 状态/错误提示区域
        status_label = QtWidgets.QLabel("")
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        def set_status(message, is_error=False):
            """设置状态提示文本及其颜色。"""
            status_label.setText(message)
            status_label.setStyleSheet(f"color: {SETTINGS_ERROR_COLOR};" if is_error else "")

        def capture_hotkey_dialog():
            """
            内部嵌套类和函数，用于弹出一个专门监听键盘输入以捕获新热键的模态对话框。
            """
            class HotkeyCaptureDialog(QtWidgets.QDialog):
                def __init__(self, parent=None):
                    super().__init__(parent)
                    self.captured_hotkey = None
                    self.setWindowTitle(self.translate("settings_hotkey_capture_title"))
                    self.setModal(True)
                    self.setMinimumWidth(SETTINGS_CAPTURE_DIALOG_MIN_WIDTH)
                    self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

                    layout = QtWidgets.QVBoxLayout(self)
                    # 显示已捕获热键的只读输入框
                    self.hotkey_display = QtWidgets.QLineEdit("")
                    self.hotkey_display.setReadOnly(True)
                    self.hotkey_display.setPlaceholderText(
                        self.translate("settings_hotkey_capture_placeholder")
                    )
                    self.hotkey_display.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
                    layout.addWidget(self.hotkey_display)

                    self.feedback_label = QtWidgets.QLabel("")
                    self.feedback_label.setWordWrap(True)
                    layout.addWidget(self.feedback_label)

                    button_row = QtWidgets.QHBoxLayout()
                    button_row.addStretch(1)
                    self.save_button = QtWidgets.QPushButton(
                        self.translate("settings_save_hotkey_btn")
                    )
                    self.save_button.setEnabled(False)
                    self.save_button.clicked.connect(self.accept)
                    button_row.addWidget(self.save_button)
                    
                    cancel_button = QtWidgets.QPushButton(
                        self.translate("settings_hotkey_capture_cancel_btn")
                    )
                    cancel_button.clicked.connect(self.reject)
                    button_row.addWidget(cancel_button)
                    layout.addLayout(button_row)

                    self._set_feedback(self.translate("settings_hotkey_capture_waiting"))
                    # 确保对话框立即获得焦点以接收键盘事件
                    QtCore.QTimer.singleShot(0, self.setFocus)

                def _set_feedback(self, message, is_error=False):
                    self.feedback_label.setText(message)
                    self.feedback_label.setStyleSheet(
                        f"color: {SETTINGS_ERROR_COLOR};" if is_error else ""
                    )

                def keyPressEvent(self, event):
                    """覆盖按键事件，拦截并解析用户按下的组合键。"""
                    modifier_only_keys = {
                        QtCore.Qt.Key.Key_Control,
                        QtCore.Qt.Key.Key_Shift,
                        QtCore.Qt.Key.Key_Alt,
                        QtCore.Qt.Key.Key_Meta,
                        QtCore.Qt.Key.Key_Super_L,
                        QtCore.Qt.Key.Key_Super_R,
                    }

                    key = event.key()
                    # 如果用户只按下了修饰键，暂不判定为有效热键
                    if key in modifier_only_keys:
                        self.captured_hotkey = None
                        self.save_button.setEnabled(False)
                        self._set_feedback(
                            self.translate("settings_hotkey_capture_invalid"),
                            is_error=True,
                        )
                        event.accept()
                        return

                    # 解析主按键
                    key_token = _qt_key_to_hotkey_token(key)
                    if key_token is None:
                        self.captured_hotkey = None
                        self.save_button.setEnabled(False)
                        self._set_feedback(
                            self.translate("settings_hotkey_capture_invalid"),
                            is_error=True,
                        )
                        event.accept()
                        return

                    # 解析修饰键状态
                    modifiers = event.modifiers()
                    modifier_tokens = []
                    if modifiers & QtCore.Qt.KeyboardModifier.ControlModifier:
                        modifier_tokens.append("Ctrl")
                    if modifiers & QtCore.Qt.KeyboardModifier.AltModifier:
                        modifier_tokens.append("Alt")
                    if modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier:
                        modifier_tokens.append("Shift")
                    if modifiers & QtCore.Qt.KeyboardModifier.MetaModifier:
                        modifier_tokens.append("Win")

                    # 构建完整的热键字符串并验证
                    requested_hotkey = "+".join(modifier_tokens + [key_token]) if modifier_tokens else key_token
                    try:
                        _, _, canonical_hotkey = parse_hotkey(requested_hotkey)
                    except Exception as exc:
                        self.captured_hotkey = None
                        self.save_button.setEnabled(False)
                        self._set_feedback(
                            self.translate("settings_hotkey_invalid", error=exc),
                            is_error=True,
                        )
                        event.accept()
                        return

                    # 更新 UI 显示捕获结果
                    self.captured_hotkey = canonical_hotkey
                    self.hotkey_display.setText(canonical_hotkey)
                    self._set_feedback(
                        self.translate(
                            "settings_hotkey_capture_captured",
                            hotkey=canonical_hotkey,
                        ),
                        is_error=False,
                    )
                    self.save_button.setEnabled(True)
                    event.accept()

            capture_dialog = HotkeyCaptureDialog(dialog)
            if capture_dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                return capture_dialog.captured_hotkey
            return None

        def change_hotkey_from_settings():
            """更改热键的业务流：弹出捕获框 -> 更新配置 -> 通知管理器重载。"""
            canonical_hotkey = capture_hotkey_dialog()
            if not canonical_hotkey:
                return

            try:
                # 写入配置文件
                update_hotkey_in_config(self.config_path, canonical_hotkey)
            except Exception as exc:
                set_status(
                    self.translate("settings_hotkey_save_failed", error=exc),
                    is_error=True,
                )
                return

            # 通知热键管理器读取新配置并注册
            self.hotkey_manager.apply_hotkey_reload()
            self._refresh_hotkey_label()

            # 检查是否真正应用成功（可能因系统占用而失败，管理器会回滚）
            if self.hotkey_manager.current_hotkey_name == canonical_hotkey:
                set_status("", is_error=False)
            else:
                set_status(
                    self.translate(
                        "settings_hotkey_apply_failed",
                        old_hotkey=self.hotkey_manager.current_hotkey_name,
                        new_hotkey=canonical_hotkey,
                    ),
                    is_error=True,
                )

        # 底部按钮区域
        button_row = QtWidgets.QHBoxLayout()
        change_hotkey_button = QtWidgets.QPushButton(self.translate("settings_change_hotkey_btn"))
        change_hotkey_button.clicked.connect(change_hotkey_from_settings)
        change_hotkey_button.setMaximumWidth(SETTINGS_CHANGE_BUTTON_MAX_WIDTH)
        change_hotkey_button.setMaximumHeight(SETTINGS_BUTTON_HEIGHT)
        button_row.addWidget(change_hotkey_button)

        uninstall_button = QtWidgets.QPushButton(self.translate("uninstall_btn"))
        uninstall_button.clicked.connect(self.on_uninstall)
        uninstall_button.setMaximumWidth(SETTINGS_UNINSTALL_BUTTON_MAX_WIDTH)
        uninstall_button.setMaximumHeight(SETTINGS_BUTTON_HEIGHT)
        uninstall_button.setStyleSheet(SETTINGS_UNINSTALL_BUTTON_STYLE)
        button_row.addWidget(uninstall_button)

        layout.addLayout(button_row)
        
        # 显示窗口并置顶
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
