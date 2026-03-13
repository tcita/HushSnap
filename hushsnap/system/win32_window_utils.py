"""Win32 窗口底层诊断工具。用于置顶与焦点丢失的深度排查。"""

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

def get_hwnd_value(hwnd):
    """提取各种类型的 HWND 原始数值。"""
    if hwnd is None: return 0
    try: return int(hwnd)
    except Exception: pass
    try: return int(hwnd.__index__())
    except Exception: pass
    try:
        if isinstance(hwnd, int): return hwnd
        # Handle pointers/bytes if needed
        if hasattr(hwnd, "value"):
            v = hwnd.value
            if isinstance(v, int): return v
        casted = ctypes.cast(hwnd, ctypes.c_void_p)
        return int(casted.value or 0)
    except Exception: return 0

def get_window_snapshot(hwnd):
    """获取窗口的详细状态快照（PID, TID, Class, Title, Style, Topmost）。"""
    user32 = ctypes.windll.user32
    h_val = get_hwnd_value(hwnd)
    if not h_val: return "hwnd=0x0"

    h = wintypes.HWND(h_val)
    pid = wintypes.DWORD(0)
    tid = user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    
    style = user32.GetWindowLongW(h, GWL_STYLE) & 0xFFFFFFFF
    ex_style = user32.GetWindowLongW(h, GWL_EXSTYLE) & 0xFFFFFFFF
    
    rect = wintypes.RECT()
    has_rect = user32.GetWindowRect(h, ctypes.byref(rect))
    rect_text = f"{rect.left},{rect.top},{rect.right},{rect.bottom}" if has_rect else "n/a"

    # Class Name
    buf_cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, buf_cls, len(buf_cls))
    # Window Title
    length = user32.GetWindowTextLengthW(h)
    buf_title = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(h, buf_title, len(buf_title))

    visible = int(bool(user32.IsWindowVisible(h)))
    topmost = int(bool(ex_style & 0x00000008))

    return (
        f"hwnd=0x{h_val:08X},tid={tid},pid={pid.value},class={buf_cls.value!r},"
        f"title={buf_title.value.replace('\n', ' ').strip()!r},"
        f"visible={visible},topmost={topmost},style=0x{style:08X},ex=0x{ex_style:08X},rect={rect_text}"
    )
