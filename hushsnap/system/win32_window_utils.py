import ctypes
from ctypes import wintypes

# Win32 Constants
GWL_STYLE = -16
GWL_EXSTYLE = -20
WM_CANCELMODE = 0x001F
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SW_SHOW = 5

"""
Win32 窗口管理工具模块。
提供与 Windows OS 底层交互的实用函数，包括 HWND 处理、窗口状态审计等。
"""

import ctypes
from ctypes import wintypes

# Win32 常量定义
GWL_STYLE = -16           # 获取窗口样式
GWL_EXSTYLE = -20         # 获取扩展窗口样式
WM_CANCELMODE = 0x001F    # 取消模式消息（用于强制收起菜单等）
HWND_TOPMOST = -1         # 将窗口设置为置顶层
SWP_NOSIZE = 0x0001       # 维持当前尺寸
SWP_NOMOVE = 0x0002       # 维持当前位置
SWP_FRAMECHANGED = 0x0020 # 发送窗口尺寸改变的消息
SWP_SHOWWINDOW = 0x0040   # 显示窗口
SW_SHOW = 5               # 激活并显示窗口

def get_hwnd_value(hwnd):
    """
    鲁棒性地提取各种类型的 HWND 原始数值。
    支持 PyQt 的 winId (SIP 包装对象)、原始整数、ctypes 指针等。
    
    :param hwnd: 窗口句柄对象
    :return: 整数类型的 HWND 值
    """
    if hwnd is None: return 0
    
    # 1. 如果本身是整数，直接返回
    if isinstance(hwnd, int):
        return hwnd
    
    # 2. 尝试通过 __index__ (PyQt winId 对象) 转换
    try:
        if hasattr(hwnd, "__index__"):
            return int(hwnd.__index__())
    except Exception:
        pass
        
    # 3. 尝试直接转换为整数（兼容字符串形式的数字）
    try:
        if isinstance(hwnd, (str, bytes)):
            # 只对纯数字字符串尝试转换，防止 ctypes.cast 提取字符串内存地址
            if hwnd.strip().isdigit() or (hwnd.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in hwnd[2:])):
                return int(hwnd, 0)
            return 0
        return int(hwnd)
    except Exception:
        pass

    # 4. 处理 ctypes 对象
    try:
        if hasattr(hwnd, "value"):
            v = hwnd.value
            if isinstance(v, int): return v
        
        # 仅对已知的 ctypes 指针/句柄类型进行 cast
        if hasattr(hwnd, "_as_parameter_") or isinstance(hwnd, (ctypes.c_void_p, wintypes.HANDLE)):
            casted = ctypes.cast(hwnd, ctypes.c_void_p)
            return int(casted.value or 0)
    except Exception:
        pass
        
    return 0

def get_window_snapshot(hwnd):
    """
    获取指定窗口的详细状态快照。
    用于调试排查：记录进程ID、线程ID、窗口类名、标题、可见性、置顶状态、坐标及样式。
    
    :param hwnd: 目标窗口句柄
    :return: 格式化的状态字符串
    """
    user32 = ctypes.windll.user32
    h_val = get_hwnd_value(hwnd)
    if not h_val: return "hwnd=0x0"

    h = wintypes.HWND(h_val)
    pid = wintypes.DWORD(0)
    # 获取窗口所属的 线程ID 和 进程ID
    tid = user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    
    # 获取窗口样式位
    style = user32.GetWindowLongW(h, GWL_STYLE) & 0xFFFFFFFF
    ex_style = user32.GetWindowLongW(h, GWL_EXSTYLE) & 0xFFFFFFFF
    
    # 获取窗口在屏幕上的矩形坐标
    rect = wintypes.RECT()
    has_rect = user32.GetWindowRect(h, ctypes.byref(rect))
    rect_text = f"{rect.left},{rect.top},{rect.right},{rect.bottom}" if has_rect else "n/a"

    # 获取窗口类名（例如 "Qt660QWindowIcon" 或 "CabinetWClass"）
    buf_cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, buf_cls, len(buf_cls))
    
    # 获取窗口标题文字
    length = user32.GetWindowTextLengthW(h)
    buf_title = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(h, buf_title, len(buf_title))

    visible = int(bool(user32.IsWindowVisible(h)))
    # 检查 WS_EX_TOPMOST 扩展样式位 (0x00000008)
    topmost = int(bool(ex_style & 0x00000008))

    return (
        f"hwnd=0x{h_val:08X},tid={tid},pid={pid.value},class={buf_cls.value!r},"
        f"title={buf_title.value.replace('\n', ' ').strip()!r},"
        f"visible={visible},topmost={topmost},style=0x{style:08X},ex=0x{ex_style:08X},rect={rect_text}"
    )
