"""
Win32 window utility module.
Provides low-level Windows helper functions, including HWND handling and window-state auditing.
"""

import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)

# Win32 constant definitions
GWL_STYLE = -16           # Window style index
GWL_EXSTYLE = -20         # Extended window style index
WM_CANCELMODE = 0x001F    # Cancel mode message (used to force menu dismissal, etc.)
HWND_TOPMOST = -1         # Place window in topmost z-order
SWP_NOSIZE = 0x0001       # Keep current size
SWP_NOMOVE = 0x0002       # Keep current position
SWP_FRAMECHANGED = 0x0020 # Send frame-changed message
SWP_SHOWWINDOW = 0x0040   # Show window
SW_SHOW = 5               # Activate and show window

def get_hwnd_value(hwnd):
    """
    Robustly extract raw HWND value from many input types.
    Supports PyQt winId (SIP wrapper), plain integers, ctypes pointers, etc.
    
    :param hwnd: Window handle object
    :return: Integer HWND value
    """
    if hwnd is None: return 0
    
    # 1. If already integer, return directly.
    if isinstance(hwnd, int):
        return hwnd
    
    # 2. Try __index__ conversion (PyQt winId object).
    try:
        if hasattr(hwnd, "__index__"):
            return int(hwnd.__index__())
    except Exception as e:
        logger.debug(f"Failed __index__ conversion for {hwnd}: {e}")
        
    # 3. Try direct integer conversion (including numeric strings).
    try:
        if isinstance(hwnd, (str, bytes)):
            # Only convert pure numeric strings to avoid ctypes.cast reading string memory address.
            if hwnd.strip().isdigit() or (hwnd.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in hwnd[2:])):
                return int(hwnd, 0)
            return 0
        return int(hwnd)
    except Exception as e:
        logger.debug(f"Failed direct int conversion for {hwnd}: {e}")

    # 4. Handle ctypes objects.
    try:
        if hasattr(hwnd, "value"):
            v = hwnd.value
            if isinstance(v, int): return v
        
        # Cast only known ctypes pointer/handle types.
        if hasattr(hwnd, "_as_parameter_") or isinstance(hwnd, (ctypes.c_void_p, wintypes.HANDLE)):
            casted = ctypes.cast(hwnd, ctypes.c_void_p)
            return int(casted.value or 0)
    except Exception as e:
        logger.debug(f"Failed ctypes conversion for {hwnd}: {e}")
        
    return 0

def get_window_snapshot(hwnd):
    """
    Capture detailed status snapshot of a given window.
    Useful for debugging: process/thread IDs, class, title, visibility, topmost, rect, and styles.
    
    :param hwnd: Target window handle
    :return: Formatted status string
    """
    user32 = ctypes.windll.user32
    h_val = get_hwnd_value(hwnd)
    if not h_val: return "hwnd=0x0"

    h = wintypes.HWND(h_val)
    pid = wintypes.DWORD(0)
    # Get owning thread ID and process ID.
    tid = user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    
    # Get style bits.
    style = user32.GetWindowLongW(h, GWL_STYLE) & 0xFFFFFFFF
    ex_style = user32.GetWindowLongW(h, GWL_EXSTYLE) & 0xFFFFFFFF
    
    # Get on-screen rectangle.
    rect = wintypes.RECT()
    has_rect = user32.GetWindowRect(h, ctypes.byref(rect))
    rect_text = f"{rect.left},{rect.top},{rect.right},{rect.bottom}" if has_rect else "n/a"

    # Get window class name (e.g. "Qt660QWindowIcon" or "CabinetWClass").
    buf_cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, buf_cls, len(buf_cls))
    
    # Get window title text.
    length = user32.GetWindowTextLengthW(h)
    buf_title = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(h, buf_title, len(buf_title))

    visible = int(bool(user32.IsWindowVisible(h)))
    # Check WS_EX_TOPMOST extended style bit (0x00000008).
    topmost = int(bool(ex_style & 0x00000008))

    return (
        f"hwnd=0x{h_val:08X},tid={tid},pid={pid.value},class={buf_cls.value!r},"
        f"title={buf_title.value.replace('\n', ' ').strip()!r},"
        f"visible={visible},topmost={topmost},style=0x{style:08X},ex=0x{ex_style:08X},rect={rect_text}"
    )
