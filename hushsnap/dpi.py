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
    """Grab the **entire virtual desktop** (all monitors) as one composite pixmap.

    Each screen is captured at its native resolution and composited onto a
    single canvas laid out in virtual-desktop logical space, scaled into
    physical pixels by the **highest DPR** among the screens. Using the max
    DPR (rather than the primary's) means lower-DPR monitors are at worst
    upscaled — never downsampled — so no captured detail is lost. The
    composite's devicePixelRatio is set to that max DPR, which keeps the
    selection → physical-pixel crop math uniform regardless of which monitor
    a region falls on.

    A single-monitor setup degenerates to the native grab (max DPR equals
    that screen's DPR, scale 1:1).

    Returns ``None`` when no screen is available.
    """
    screens = QtGui.QGuiApplication.screens()
    if not screens:
        return None

    primary = QtGui.QGuiApplication.primaryScreen()
    if primary is None:
        return None
    virtual = primary.virtualGeometry()
    if virtual.isNull() or virtual.width() <= 0 or virtual.height() <= 0:
        return None

    max_dpr = max(max(s.devicePixelRatio() for s in screens), 1.0)
    ox, oy = virtual.x(), virtual.y()
    canvas_w = int(round(virtual.width() * max_dpr))
    canvas_h = int(round(virtual.height() * max_dpr))

    # Paint in physical pixels with DPR left at 1 — setting devicePixelRatio
    # before painting would make QPainter treat coords as logical and
    # double-scale them. DPR is stamped on at the very end.
    canvas = QtGui.QPixmap(canvas_w, canvas_h)
    if canvas.isNull():
        return None
    canvas.fill(QtCore.Qt.GlobalColor.black)

    painter = QtGui.QPainter(canvas)
    painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    for screen in screens:
        grab = screen.grabWindow(0)
        if grab.isNull():
            continue
        g = screen.geometry()
        target = QtCore.QRectF(
            (g.x() - ox) * max_dpr,
            (g.y() - oy) * max_dpr,
            g.width() * max_dpr,
            g.height() * max_dpr,
        )
        painter.drawPixmap(
            target, grab, QtCore.QRectF(0, 0, grab.width(), grab.height())
        )
    painter.end()

    canvas.setDevicePixelRatio(max_dpr)
    return canvas
