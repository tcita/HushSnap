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
