"""Automated OCR stress-test for the MSIX-packaged HushSnap.

Drives the real app purely through synthesized keyboard/mouse input — no
in-process hooks, no auto-restart. You launch the MSIX app yourself and keep
the screen on an interface that contains text; this script then repeats:

    Alt+Q  →  left-click (full-screen capture)  →  click bottom-right
    thumbnail  →  wait for ``[OCR_CHAIN] show_text done`` in the log.

Each step is synced off the ``[OCR_CHAIN]`` log markers added across the
capture→thumbnail→OCR pipeline (see hotkey.py / capture_*.py /
ocr_controller.py / ocr_service.py / ocr_popup.py). If the process dies or a
round times out, the script stops and saves the log slice for that round so
the crash can be localized to the exact pipeline stage that halted.

Why log-marker sync instead of fixed sleeps: the rare crash under
investigation manifests as "thumbnail stuck ~2s then crash". The 2s is the
native OCR inference window. By tailing markers we (a) never click before the
thumbnail actually exists, and (b) know precisely which stage the process
was in when it died — e.g. a log that ends after
``recognize() engine call begin`` but before ``engine call end`` points at a
native onnxruntime crash, which faulthandler cannot capture and only a WER
minidump (see setup_wer_dumps.ps1) can stack-trace.

Usage:
    # 1. (once, as admin) enable minidump capture:
    #    powershell -ExecutionPolicy Bypass -File scripts/setup_wer_dumps.ps1
    # 2. launch the MSIX app, leave it on a screen with text
    # 3. run:
    python scripts/stress_test_ocr.py --rounds 500

    # If the auto-detected log path is wrong, pass it explicitly:
    python scripts/stress_test_ocr.py --log "C:\\path\\to\\hushsnap.log"

Only depends on the Python standard library + ctypes (no pyautogui / psutil /
pywin32) so it runs on any Windows Python.
"""

import argparse
import ctypes
import ctypes.wintypes as wintypes
import os
import sys
import time
from pathlib import Path

# ── Win32 setup ───────────────────────────────────────────────────────────────
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

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
    for rc, is_primary in get_monitors():
        if is_primary:
            return rc
    rcs = get_monitors()
    return rcs[0][0] if rcs else wintypes.RECT(0, 0, 1920, 1080)


# The thumbnail is NOT located by enumerating windows. It is deterministic:
# see run_round() for the geometry derivation (primary screen bottom-right,
# inset 140,95 physical px). A previous revision tried matching a frameless
# Qt tool window by EnumWindows + geometry heuristics, but that was fragile
# across multi-monitor / DPR setups.


# ── process liveness ──────────────────────────────────────────────────────────

def is_hushsnap_running():
    """True if at least one HushSnap.exe process is alive.

    Uses tasklist (always present) rather than a third-party dep.
    """
    import subprocess
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq HushSnap.exe", "/NH", "/FO", "CSV"],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except Exception:
        return True  # assume alive if we cannot tell — safer than a false crash
    return "HushSnap.exe" in out


# ── log path auto-detection ───────────────────────────────────────────────────

def autodetect_log_path():
    """Find hushsnap.log under the MSIX package data folder.

    MSIX redirects %LOCALAPPDATA% writes into
    %LOCALAPPDATA%\\Packages\\<PackageFamilyName>\\LocalCache\\Local\\HushSnap\\.
    We glob for it so the exact PFN (with publisher hash) need not be known
    ahead of time. Falls back to the unpackaged path %LOCALAPPDATA%\\HushSnap.
    """
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    candidates = []
    pkg_root = Path(local) / "Packages"
    if pkg_root.exists():
        candidates.extend(pkg_root.glob("*HushSnap*/LocalCache/Local/HushSnap/hushsnap.log"))
    candidates.append(Path(local) / "HushSnap" / "hushsnap.log")  # unpackaged / dev
    for c in candidates:
        if c.exists():
            return c
    return None


# ── log tailing ───────────────────────────────────────────────────────────────

CHAIN = "[OCR_CHAIN]"
SUCCESS_MARKER = "[OCR_CHAIN] show_text done"


def read_new_lines(log_path, offset):
    """Return (new_text, new_offset) for bytes added since offset."""
    try:
        size = log_path.stat().st_size
    except OSError:
        return "", offset
    if size < offset:
        # log was rotated/truncated — start from the beginning
        offset = 0
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            text = f.read()
            new_offset = f.tell()
    except OSError:
        return "", offset
    return text, new_offset


# ── the test loop ─────────────────────────────────────────────────────────────

def run_round(round_idx, log_path, start_offset, cfg):
    """Execute one capture→OCR round. Returns (status, end_offset, detail).

    status ∈ {"ok", "crash", "hang"}.

    Thumbnail location is deterministic — ui/thumbnail.py:185-186 places the
    window at the bottom-right of the screen the cursor was on when capture
    ended, so we compute its center from the constants rather than enumerating
    windows (EnumWindows matching on a frameless Qt tool window with no title
    was fragile across multi-monitor/DPR setups). The capture click lands at
    the primary screen center, so the thumbnail lands at the primary screen's
    bottom-right; its center is the screen's bottom-right corner inset by
    (140, 95) physical px:
        display_width = card_width + 2*shadow_padding = 240 + 24 = 264
        display_height = card_height + 2*shadow_padding = 150 + 24 = 174
        end_x = screen.right - 264 - MARGIN(20) + shadow_padding(12) = right-272
        end_y = screen.bottom - 174 - 20 + 12 = bottom-182
        center = (end_x + 132, end_y + 87) = (right-140, bottom-95)
    """
    primary = primary_monitor_rect()

    # 0. Dismiss any OCR popup left over from the previous round so it is NOT
    #    captured into this round's screenshot (the popup auto-hides on losing
    #    focus; clicking the taskbar steals focus). Keeps each round's OCR
    #    input identical to the static text screen the user prepared.
    #    SKIPPED in hammer mode: dismissing would serialize rounds and destroy
    #    the overlap we want (round N's callback in flight while round N+1
    #    acquires the lock). In hammer mode overlapping popups/thumbnails are
    #    expected; the Alt+Q overlay itself steals focus and hides the old
    #    popup when capture begins.
    if not cfg.hammer:
        dismiss_popup(primary)

    # 1. Resolve the capture-click point. Default to the primary screen center
    #    (where the user is expected to keep text); --capture-point overrides
    #    for multi-monitor setups where the text is on a secondary screen.
    if cfg.capture_point:
        cap_x, cap_y = cfg.capture_point
    else:
        cap_x = primary.left + (primary.right - primary.left) // 2
        cap_y = primary.top + (primary.bottom - primary.top) // 2
    move_to(cap_x, cap_y)
    time.sleep(0.2)
    print(f"  [round {round_idx}] Alt+Q (capture click at {cap_x},{cap_y})")
    send_alt_q()

    # 2. Wait for the capture overlay to come up, then clean-click for a
    #    full-screen capture. A fixed delay is fine here — the overlay is
    #    fullscreen and the click just needs to land after it appears.
    time.sleep(cfg.overlay_delay)
    move_to(cap_x, cap_y)
    click_here()

    # 3. Wait for the thumbnail to appear, then click its center. The slide-in
    #    animation is 300ms; 1s is enough for capture→callback→show to settle.
    #    Crash during this window is caught below by the liveness check.
    thumb_x = primary.right - 140
    thumb_y = primary.bottom - 95
    time.sleep(cfg.thumbnail_delay)
    if not is_hushsnap_running():
        return "crash", start_offset, "process died before thumbnail click"
    print(f"  [round {round_idx}] click thumbnail at {thumb_x},{thumb_y}")
    move_to(thumb_x, thumb_y)
    time.sleep(0.15)
    click_here()

    # 4. Wait for the OCR result. Two modes:
    #    - wait mode (default): tail the log until show_text done / crash / timeout.
    #    - hammer mode: wait a FIXED interval (cfg.hammer_interval) regardless of
    #      whether OCR finished, then proceed to the next round immediately. This
    #      lets multiple OCR workers overlap — round N's callback emit can collide
    #      with round N+1's lock acquisition, which is exactly the race the
    #      pre-0019fde code (callback emitted INSIDE self._lock) crashed on.
    #      We still tail markers + check liveness during the interval so a crash
    #      is caught and the last marker is recorded.
    offset = start_offset
    last_markers = []

    if cfg.hammer:
        deadline = time.monotonic() + cfg.hammer_interval
        while time.monotonic() < deadline:
            text, offset = read_new_lines(log_path, offset)
            if text:
                for line in text.splitlines():
                    if CHAIN in line:
                        last_markers.append(line.split(CHAIN, 1)[1].strip())
                        print(f"  [round {round_idx}] log: {line.split(CHAIN, 1)[1].strip()}")
            if not is_hushsnap_running():
                return "crash", offset, "process died during hammer interval; last marker: " + (last_markers[-1] if last_markers else "(none)")
            time.sleep(0.1)
        # Interval elapsed — move on. Don't dismiss popup here: in hammer mode
        # we WANT overlapping work; dismissing would serialize it. The next
        # round's dismiss_popup() at step 0 still runs, but only after this
        # round's interval, so overlap window is preserved.
        if not is_hushsnap_running():
            return "crash", offset, "process died after hammer interval; last marker: " + (last_markers[-1] if last_markers else "(none)")
        return "ok", offset, f"hammer tick (last: {last_markers[-1] if last_markers else '(none)'})"

    # wait mode
    deadline = time.monotonic() + cfg.ocr_timeout
    while time.monotonic() < deadline:
        text, offset = read_new_lines(log_path, offset)
        if text:
            for line in text.splitlines():
                if CHAIN in line:
                    # keep just the marker portion for a compact timeline
                    last_markers.append(line.split(CHAIN, 1)[1].strip())
                    print(f"  [round {round_idx}] log: {line.split(CHAIN, 1)[1].strip()}")
            if SUCCESS_MARKER in text:
                dismiss_popup(primary)  # clean the screen for the next round
                return "ok", offset, "show_text done"
        if not is_hushsnap_running():
            return "crash", offset, "process died during OCR; last marker: " + (last_markers[-1] if last_markers else "(none)")
        time.sleep(0.15)

    # Timed out without success marker.
    if not is_hushsnap_running():
        return "crash", offset, "process died after timeout; last marker: " + (last_markers[-1] if last_markers else "(none)")
    return "hang", offset, "OCR did not complete; last marker: " + (last_markers[-1] if last_markers else "(none)")


def save_round_log(log_path, round_idx, status, detail):
    """Copy this round's log slice to results/ for offline analysis."""
    results = Path(__file__).resolve().parent.parent / "stress_results"
    results.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = results / f"round_{round_idx:04d}_{status}_{stamp}.log"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        # Slice from the last session-start marker so the file is just this run.
        idx = text.rfind("Logging initialized.")
        body = text[idx:] if idx != -1 else text
        dest.write_text(
            f"=== stress test round {round_idx} | status={status} ===\n"
            f"=== detail: {detail} ===\n"
            f"=== saved {stamp} ===\n\n{body}",
            encoding="utf-8",
        )
        print(f"\n  >> log slice saved: {dest}")
    except Exception as exc:
        print(f"\n  !! failed to save log slice: {exc}")


def main():
    ap = argparse.ArgumentParser(description="OCR stress-test for MSIX HushSnap (keyboard/mouse only).")
    ap.add_argument("--rounds", type=int, default=500, help="max rounds to run (default 500)")
    ap.add_argument("--log", type=str, default=None, help="path to hushsnap.log (auto-detected if omitted)")
    ap.add_argument("--capture-point", type=str, default=None,
                    help='"x,y" physical pixel for the full-screen capture click (default: primary screen center). '
                         'Note: the thumbnail is always clicked at the PRIMARY screen bottom-right, '
                         'so keep the capture on the primary screen unless you also move the thumbnail click.')
    ap.add_argument("--overlay-delay", type=float, default=0.8,
                    help="seconds to wait for the capture overlay after Alt+Q (default 0.8)")
    ap.add_argument("--thumbnail-delay", type=float, default=1.0,
                    help="seconds to wait after the capture click before clicking the thumbnail (default 1.0)")
    ap.add_argument("--ocr-timeout", type=float, default=20.0,
                    help="seconds to wait for OCR to complete per round in wait mode (default 20.0)")
    ap.add_argument("--cooldown", type=float, default=1.5,
                    help="seconds between rounds (default 1.5)")
    ap.add_argument("--hammer", action="store_true",
                    help="hammer mode: don't wait for OCR to finish — fire the next round after a fixed "
                         "interval. Lets OCR workers overlap, exercising the lock race that the pre-0019fde "
                         "code (callback emitted inside self._lock) crashed on. Use with --hammer-interval.")
    ap.add_argument("--hammer-interval", type=float, default=1.5,
                    help="seconds per round in hammer mode (default 1.5). Must be long enough for the "
                         "thumbnail to fully appear (~1.3s from Alt+Q); the script raises it automatically "
                         "if set lower. Lower (down to that floor) = more OCR overlap = more race pressure.")
    ap.add_argument("--no-stop-on-fail", action="store_true",
                    help="keep running after a crash/hang instead of stopping (you must restart the app manually)")
    args = ap.parse_args()

    log_path = Path(args.log) if args.log else autodetect_log_path()
    if log_path is None or not log_path.exists():
        print("ERROR: could not find hushsnap.log. Pass it explicitly with --log.")
        print("       (MSIX stores it under %LOCALAPPDATA%\\Packages\\<PFN>\\LocalCache\\Local\\HushSnap\\)")
        return 2
    print(f"log file: {log_path}")

    class Cfg:
        pass
    cfg = Cfg()
    cfg.overlay_delay = args.overlay_delay
    cfg.thumbnail_delay = args.thumbnail_delay
    cfg.ocr_timeout = args.ocr_timeout
    cfg.hammer = args.hammer
    cfg.hammer_interval = args.hammer_interval
    cfg.capture_point = None
    # In hammer mode the thumbnail must still be fully shown before we click
    # it, otherwise the click misses and the round does nothing. The thumbnail
    # needs ~1.3s from Alt+Q (overlay_delay + capture + 300ms slide-in). We
    # spend overlay_delay (0.8s) before the capture click, so thumbnail_delay
    # (click→thumbnail-click gap) only needs ~0.5s in hammer mode (vs 1.0s in
    # wait mode). Hammer interval must be >= overlay_delay + thumbnail_delay
    # for the round to fit; clamp both so the thumbnail always gets enough
    # time even at aggressive intervals.
    if cfg.hammer:
        cfg.thumbnail_delay = 0.5  # thumbnail appears ~0.5s after capture click
        min_interval = cfg.overlay_delay + cfg.thumbnail_delay + 0.2
        if cfg.hammer_interval < min_interval:
            print(f"NOTE: hammer-interval {cfg.hammer_interval}s too short for the "
                  f"thumbnail to appear; raised to {min_interval:.2f}s.")
            cfg.hammer_interval = min_interval

    if cfg.hammer:
        print(f"HAMMER MODE: rounds={args.rounds}  hammer_interval={cfg.hammer_interval}s  "
              f"thumbnail_delay={cfg.thumbnail_delay}s  cooldown={args.cooldown}s")
        print("OCR workers will overlap — this exercises the lock race.")
    else:
        print(f"WAIT MODE: rounds={args.rounds}  overlay_delay={args.overlay_delay}s  "
              f"ocr_timeout={args.ocr_timeout}s  cooldown={args.cooldown}s")
    print("make sure the MSIX app is running and the screen shows text.\n")
    if args.capture_point:
        try:
            parts = args.capture_point.split(",")
            cfg.capture_point = (int(parts[0]), int(parts[1]))
        except Exception:
            print(f"ERROR: bad --capture-point {args.capture_point!r}; expected 'x,y'")
            return 2

    ok = 0
    fail = 0
    for i in range(1, args.rounds + 1):
        if not is_hushsnap_running():
            print(f"\n[round {i}] HushSnap is not running. Start it and re-run, or it crashed earlier.")
            if not args.no_stop_on_fail:
                break
            time.sleep(2)
            continue

        # Snapshot the log offset so we only tail this round's lines.
        try:
            start_offset = log_path.stat().st_size
        except OSError:
            start_offset = 0

        t0 = time.monotonic()
        status, _end_offset, detail = run_round(i, log_path, start_offset, cfg)
        dt = time.monotonic() - t0

        if status == "ok":
            ok += 1
            print(f"  [round {i}] OK ({dt:.2f}s)\n")
        else:
            fail += 1
            print(f"\n  [round {i}] {status.upper()} after {dt:.2f}s — {detail}")
            save_round_log(log_path, i, status, detail)
            print(f"  cumulative: ok={ok}  fail={fail}\n")
            if not args.no_stop_on_fail:
                print("Stopping. Restart the MSIX app and re-run to continue.")
                break
        # In hammer mode the round itself already waited hammer_interval;
        # adding cooldown would lengthen the gap and reduce overlap pressure.
        if not args.hammer:
            time.sleep(args.cooldown)

    print(f"\n=== done: ok={ok}  fail={fail} ===")


if __name__ == "__main__":
    sys.exit(main() or 0)
