"""
Device-pixel-ratio (DPR) utilities.

Qt works in *logical* (device-independent) pixels.  On high-DPI displays one
logical pixel maps to multiple physical pixels.  ``devicePixelRatio()`` is the
multiplier — typically 1.0, 1.25, 1.5, or 2.0.

This module is the **single source of truth** for acquiring the DPR and for the
most common logical ↔ physical coordinate conversions.  Every call site that
needs DPR information should go through the helpers here instead of calling
``QScreen.devicePixelRatio()`` / ``QWidget.devicePixelRatio()`` directly.
"""
from __future__ import annotations

import sys

from PyQt6 import QtCore, QtGui, QtWidgets


def current_dpr() -> float:
    """Primary screen device pixel ratio, or 1.0 when no screen is available."""
    screen = QtGui.QGuiApplication.primaryScreen()
    return screen.devicePixelRatio() if screen else 1.0


# ── Coordinate / size conversions ───────────────────────────────────────────

def logical_to_physical_rect(
    rect: QtCore.QRect, *, dpr: float | None = None
) -> QtCore.QRect:
    """Convert *rect* from logical (device-independent) to physical pixels."""
    r = dpr if dpr is not None else current_dpr()
    return QtCore.QRect(
        int(rect.x() * r),
        int(rect.y() * r),
        int(rect.width() * r),
        int(rect.height() * r),
    )


def logical_to_physical_size(
    w: int, h: int, *, dpr: float | None = None
) -> tuple[int, int]:
    """Convert *(width, height)* from logical to physical pixels."""
    r = dpr if dpr is not None else current_dpr()
    return (int(w * r), int(h * r))


def physical_to_logical_size(
    phys_w: int, phys_h: int, *, dpr: float | None = None
) -> tuple[float, float]:
    """Convert *(width, height)* from physical to logical pixels."""
    r = dpr if dpr is not None else current_dpr()
    return (phys_w / r, phys_h / r)


# ── Pixmap helpers ───────────────────────────────────────────────────────────

def grab_full_screen() -> QtGui.QPixmap | None:
    """Grab the **screen under the cursor** and tag the pixmap with its DPR.

    Multi-monitor aware: the capture is scoped to whichever monitor the
    cursor currently sits on (falling back to the primary screen when the
    cursor is outside any screen or no screen is available).  Only that one
    screen is frozen — other monitors stay live.

    Returns ``None`` when no screen is available.
    """
    screen = (
        QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
        or QtWidgets.QApplication.primaryScreen()
    )
    if not screen:
        return None
    pixmap = screen.grabWindow(0)
    pixmap.setDevicePixelRatio(screen.devicePixelRatio())
    return pixmap


def grab_all_screens() -> list[tuple[QtGui.QScreen, QtGui.QPixmap]]:
    """Grab all available screens and tag each pixmap with its corresponding DPR.

    Returns:
        A list of tuples (screen, pixmap) for all connected monitors.
    """
    screens = QtWidgets.QApplication.screens()
    results = []
    for screen in screens:
        pixmap = screen.grabWindow(0)
        pixmap.setDevicePixelRatio(screen.devicePixelRatio())
        results.append((screen, pixmap))
    return results


def screen_physical_rect(screen: QtGui.QScreen) -> QtCore.QRect:
    """This monitor's rect in the **contiguous physical** virtual desktop.

    Qt exposes geometry in *logical* pixels, and its logical virtual desktop is
    discontinuous across monitors of different DPR — a logical gap with no
    physical counterpart sits between them.  Naively scaling ``logical × DPR``
    therefore misplaces every non-origin monitor by that gapped-and-rescaled
    amount, producing a large transparent band in any cross-screen crop.

    Windows tiles the real monitors contiguously in physical device pixels, so
    we resolve the true physical rect via ``MonitorFromPoint`` +
    ``GetMonitorInfoW`` (fed the screen's logical centre).  This returns the
    monitor's actual ``rcMonitor`` in device coordinates, which tile edge to
    edge with every other monitor regardless of per-monitor DPR.

    On non-Windows platforms (or any Win32 failure) we fall back to the
    logical-×-DPR approximation, which is exact for single-monitor and
    uniform-DPR setups.
    """
    try:
        g = screen.geometry()
    except Exception:
        g = None

    # Fallback: logical × DPR (exact when all screens share the same DPR).
    def _fallback():
        dpr = screen.devicePixelRatio() or 1.0
        if g is None:
            return QtCore.QRect()
        return QtCore.QRect(
            round(g.x() * dpr),
            round(g.y() * dpr),
            round(g.width() * dpr),
            round(g.height() * dpr),
        )

    if sys.platform != "win32" or g is None:
        return _fallback()

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        class _MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32),
            ]

        centre = g.center()
        MONITOR_DEFAULTTONEAREST = 2
        hmon = user32.MonitorFromPoint(
            wintypes.POINT(centre.x(), centre.y()),
            MONITOR_DEFAULTTONEAREST,
        )
        if not hmon:
            return _fallback()

        mi = _MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return _fallback()

        r = mi.rcMonitor
        return QtCore.QRect(r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        return _fallback()


def cursor_physical_pos() -> QtCore.QPoint | None:
    """Cursor position in contiguous physical virtual-desktop pixels.

    Reads Win32 ``GetCursorPos`` directly, which returns physical device
    coords in the same space as ``screen_physical_rect`` (rcMonitor) — so the
    value is always continuous across monitors and never lands in a logical
    "dead zone" (a region of the logical desktop bounding box that sits on no
    screen because a high-DPR neighbour is logically shorter).  On non-Windows
    / Win32 failure, fall back to reconstructing from Qt logical coords via
    the screen under the cursor, anchored on its physical origin.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            pt = wintypes.POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return QtCore.QPoint(pt.x, pt.y)
        except Exception:
            pass
    try:
        logical = QtGui.QCursor.pos()
        screen = (
            QtWidgets.QApplication.screenAt(logical)
            or QtWidgets.QApplication.primaryScreen()
        )
        if screen is None:
            return None
        dpr = screen.devicePixelRatio() or 1.0
        phys_origin = screen_physical_rect(screen).topLeft()
        local = logical - screen.geometry().topLeft()
        return phys_origin + QtCore.QPoint(
            round(local.x() * dpr), round(local.y() * dpr)
        )
    except Exception:
        return None


def cursor_screen() -> QtGui.QScreen | None:
    """The real QScreen under the cursor, robust to mixed-DPR dead zones.

    ``QApplication.screenAt(QCursor.pos())`` resolves the screen from Qt's
    *logical* global coords; when a high-DPR monitor is logically shorter than
    its neighbour, the area below it (within the desktop bounding box but on
    no screen) makes ``screenAt`` return ``None``.  Callers that then fall
    back to ``primaryScreen()`` mis-place their UI on the wrong monitor.

    This resolves the screen from the cursor's *physical* position (see
    ``cursor_physical_pos``) by matching it against each screen's real
    ``screen_physical_rect`` — physical space has no dead zone, so the
    correct screen is always found.  Falls back to ``screenAt`` then
    ``primaryScreen``.
    """
    phys = cursor_physical_pos()
    if phys is not None:
        try:
            for screen in QtWidgets.QApplication.screens():
                if screen_physical_rect(screen).contains(phys):
                    return screen
        except Exception:
            pass
    try:
        return (
            QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
            or QtWidgets.QApplication.primaryScreen()
        )
    except Exception:
        return None

