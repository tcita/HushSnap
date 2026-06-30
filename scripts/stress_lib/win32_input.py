"""Synthesized keyboard/mouse input + monitor enumeration for the stress test.

Pure ctypes (no pyautogui / pywin32) so it runs on any Windows Python. The
stress test drives the real MSIX app exclusively through SendInput: Alt+Q to
trigger capture, left-clicks for the full-screen capture and the thumbnail,
and a click on empty desktop to dismiss the OCR popup between rounds.
"""

import ctypes
import ctypes.wintypes as wintypes
import time

# ── Win32 setup ───────────────────────────────────────────────────────────────
user32 = ctypes.windll.user32

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002
VK_MENU = 0x12  # Alt
MONITORINFOF_PRIMARY = 0x0001


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    # Lay out { type, union{ki,mi} } with BOTH levels anonymous so that
    # inp.type, inp.ki.wVk and inp.mi.dwFlags all resolve directly on INPUT.
    class _INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]
    _anonymous_ = ("_input",)
    _fields_ = [("_input", _INPUT)]


# ── low-level input helpers ───────────────────────────────────────────────────

def _send_mouse(flags):
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi.dwFlags = flags
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def click_here():
    """Clean left click at the current cursor position (down+up, no move).

    A click must move < CAPTURE_CLICK_THRESHOLD_PX (8px) between down and up,
    otherwise the capture overlay treats it as a region selection instead of a
    full-screen capture. We never move the cursor between down and up.
    """
    _send_mouse(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.03)
    _send_mouse(MOUSEEVENTF_LEFTUP)


def move_to(x, y):
    """Move cursor to a physical virtual-desktop coordinate."""
    user32.SetCursorPos(int(x), int(y))


def _send_key(vk, up=False):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki.wVk = vk
    inp.ki.wScan = user32.MapVirtualKeyW(vk, 0)
    inp.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_alt_q():
    """Press Alt+Q (the default capture hotkey) and release.

    RegisterHotKey-registered global hotkeys fire from the synthesized input
    stream, so this triggers the app's WM_HOTKEY path just like a real press.
    """
    _send_key(VK_MENU)
    time.sleep(0.03)
    _send_key(0x51)  # 'Q'
    time.sleep(0.03)
    _send_key(0x51, up=True)
    time.sleep(0.03)
    _send_key(VK_MENU, up=True)


def dismiss_popup(primary_rect):
    """Click empty desktop so the OCR popup loses focus and auto-hides.

    The OCR popup (ui/ocr_popup.py:1091-1097) hides itself on
    ActivationChange when it is not pinned and stops being the active
    window — it does NOT respond to Esc. Clicking empty desktop steals
    focus and dismisses the popup before the next round's Alt+Q grabs the
    screen. Without this, the previous round's popup is captured into the
    next screenshot, making every round's OCR input different and
    potentially masking the rare crash's reproduction conditions.

    We click the LEFT-center of the primary screen: that vertical edge is
    empty desktop (no taskbar buttons, no icons in the middle of the side),
    so the click lands on nothing interactive. Avoid the right side and the
    bottom — the thumbnail/popup live bottom-right and the taskbar lives
    bottom-center, both of which would respond to the click.
    """
    tx = primary_rect.left + 6
    ty = primary_rect.top + (primary_rect.bottom - primary_rect.top) // 2
    move_to(tx, ty)
    time.sleep(0.1)
    click_here()
    time.sleep(0.2)


# ── monitor enumeration (physical coords) ─────────────────────────────────────

class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def get_monitors():
    """Return [(rcMonitor, is_primary), ...] in physical virtual-desktop pixels."""
    monitors = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def cb(hmon, _hdc, _lprc, _lparam):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            monitors.append((mi.rcMonitor, bool(mi.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    user32.EnumDisplayMonitors(None, None, cb, 0)
    return monitors


def primary_monitor_rect():
    """Return the primary monitor's RECT (falls back to 1920x1080)."""
    for rc, is_primary in get_monitors():
        if is_primary:
            return rc
    rcs = get_monitors()
    return rcs[0][0] if rcs else wintypes.RECT(0, 0, 1920, 1080)


# The thumbnail is NOT located by enumerating windows. It is deterministic:
# see run_round() in stress_test_ocr.py for the geometry derivation (primary
# screen bottom-right, inset 140,95 physical px). A previous revision tried
# matching a frameless Qt tool window by EnumWindows + geometry heuristics,
# but that was fragile across multi-monitor / DPR setups.
