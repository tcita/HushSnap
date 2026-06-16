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
    """Grab the entire desktop and tag the pixmap with the current DPR.

    Returns ``None`` when no primary screen is available.
    """
    screen = QtWidgets.QApplication.primaryScreen()
    if not screen:
        return None
    pixmap = screen.grabWindow(0)
    pixmap.setDevicePixelRatio(screen.devicePixelRatio())
    return pixmap
