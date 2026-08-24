"""
Screenshot interaction UI module.
Handles fullscreen overlay display, drag-to-select drawing, final crop, and clipboard write.
"""

import ctypes
import logging
import sys
import traceback

from PyQt6 import QtCore, QtGui, QtWidgets

from .dpi import (
    cursor_physical_pos,
    cursor_screen,
    logical_to_physical_rect,
    screen_physical_rect,
)
from .constants import (
    CAPTURE_CLICK_THRESHOLD_PX,
    CAPTURE_OVERLAY_RGBA,
    CAPTURE_SELECTION_MIN_PX,
    DEBUG_TOPMOST_DELAY_MS,
)
from .logging_config import get_logger
from .system.win32_window_utils import (
    get_hwnd_value,
    get_window_snapshot,
)
from .ui.styles import BRAND_GREEN, BRAND_GREEN_RGB

logger = get_logger(__name__)

# ── Selection-handle / dimension-label drawing constants ─────────────────────
_SELECTION_HANDLE_SIZE = 10
_DIMENSION_LABEL_FONT_SIZE = 9
_DIMENSION_LABEL_PAD_X = 6
_DIMENSION_LABEL_PAD_Y = 2
_DIMENSION_LABEL_RADIUS = 4
_DIMENSION_LABEL_BG = (0, 0, 0, 180)
_DIMENSION_LABEL_OFFSET = 5

# ── Window-foreground strategy (ShareX-style) ────────────────────────────────
#
# The capture overlay must reach the foreground so keyboard input (Esc) and
# the frozen background land on the right window. ShareX — a mature, battle-
# tested tool with tens of millions of users — handles this for its region-
# capture overlay with nothing more than ``TopMost=true`` + ``Activate()`` +
# ``BringToFront()`` on the Shown event, relying on the WM_HOTKEY foreground
# privilege the hotkey thread receives. We mirror exactly that: ``raise_`` +
# ``activateWindow``, with TopMost already guaranteed by the
# WindowStaysOnTopHint flag set at construction.
#
# There is deliberately NO hard fallback (SetForegroundWindow,
# AttachThreadInput, etc.). ShareX does not use any of those — not because
# they never fail, but because AttachThreadInput carries real risk (deadlock
# with a busy thread, input-state corruption if the process terminates mid-
# attach, false-positive anti-cheat detection) and the light path has proven
# sufficient across ShareX's entire user base. HushSnap cannot match that
# scale of testing, so we stay on the well-trodden road.
#
# A delayed audit (``GetForegroundWindow() == hwnd``) logs whether the light
# path won foreground; the log is the signal for whether this strategy holds
# in practice. No escalation is performed — the log is informational only.


def _is_foreground(hwnd) -> bool:
    """True iff *hwnd* is currently the desktop's foreground window."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        return ctypes.windll.user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def claim_foreground(widget, *, primary: bool = True):
    """Bring *widget* to the foreground, ShareX-style.

    This mirrors ShareX's ``ForceActivate``: ``raise_`` + ``activateWindow``,
    with TopMost already guaranteed by the WindowStaysOnTopHint flag set at
    construction. Only the cursor's screen (``primary=True``) claims the
    foreground; sibling overlays on other monitors stay topmost-only so the
    N overlay windows do not fight each other for focus.

    A delayed audit logs whether the foreground claim succeeded. No hard
    escalation is attempted — see the module-level strategy comment.
    """
    try:
        widget.raise_()
        if primary:
            widget.activateWindow()
    except Exception:
        logger.debug("claim_foreground: light raise/activate failed", exc_info=True)

    # Non-Windows or sibling overlay: light path is all we do.
    if sys.platform != "win32" or not primary:
        return

    hwnd = get_hwnd_value(widget.winId()) if widget.winId() else None
    if not hwnd:
        return

    def _audit():
        if _is_foreground(hwnd):
            logger.debug("topmost_audit | foreground claim succeeded")
        else:
            logger.info(
                "topmost_audit | foreground claim did not win; "
                "overlay may not receive keyboard input (Esc). "
                "No hard fallback is attempted — see module docstring."
            )

    # 15 ms delay: activateWindow() is async; checking immediately would
    # yield false negatives. Measured latency ~3 ms median, 15 ms is ~3x p95.
    QtCore.QTimer.singleShot(15, _audit)






class CaptureWindow(QtWidgets.QWidget):
    """
    Fullscreen screenshot window.
    Workflow:
    1. Receives fullscreen bitmap as background.
    2. Configures frameless fullscreen topmost window.
    3. Handles mouse interaction:
       - Left drag: create selection and capture region.
       - Left click: nothing (aligned with Windows Snipping Tool;
         also avoids wasteful full-screen OCR when auto-OCR is
         enabled).
       - Right click / Esc: exit.
    """
    def __init__(self, pixmap, screen=None, on_captured=None, on_closed=None):
        super().__init__()
        self.pixmap = pixmap
        self.on_captured = on_captured
        self.on_closed = on_closed
        self.session = None
        # The QScreen this overlay was bound to. Compared by CaptureSession to
        # decide which overlay is "primary" (the cursor's screen — the only one
        # that claims foreground). Stored separately because QWidget.screen()
        # is a method, not the bound screen attribute.
        self._bound_screen = screen

        # Configure window attributes: tool style, frameless, initially topmost.
        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

        if screen is None:
            screen = cursor_screen()

        # Bind to screen explicitly in Qt6
        self.winId()  # Forces native window handle creation
        if self.windowHandle() and screen and isinstance(screen, QtGui.QScreen):
            self.windowHandle().setScreen(screen)

        self.setWindowState(QtCore.Qt.WindowState.WindowFullScreen)
        self.setGeometry(screen.geometry())

        # Track this screen's geometry in *physical* virtual-desktop pixels.
        # Qt's logical virtual desktop is discontinuous across monitors of
        # different DPR (a logical gap with no physical counterpart), which
        # splits any cross-screen selection and makes the cursor "teleport"
        # across the boundary.  The Windows virtual screen tiles physical
        # monitors contiguously, so the selection is tracked in physical
        # pixels and converted back to per-screen logical coords only for
        # painting.  See revert commit 32bf764 for the prior failed attempt.
        #
        # NB: the physical origin must come from the real Win32 monitor rect
        # (screen_physical_rect), NOT from ``logical × DPR`` — that product
        # misplaces non-origin monitors by the rescaled logical gap, producing
        # a large transparent band in cross-screen crops.
        self.dpr = self.pixmap.devicePixelRatio() if self.pixmap is not None else 1.0
        try:
            sdpr = screen.devicePixelRatio()
            if isinstance(sdpr, (int, float)) and sdpr:
                self.dpr = float(sdpr)
            self.physical_rect_win = screen_physical_rect(screen)
            self.physical_top_left = self.physical_rect_win.topLeft()
        except Exception:
            # Mock screens (tests) / legacy single-screen path: fall back to
            # the logical top-left so behaviour is unchanged for non-session use.
            self.physical_rect_win = None
            self.physical_top_left = self.geometry().topLeft()

        # Initialize interaction state.
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        # Receive mouseMoveEvent while hovering (no button held) so the
        # cursor-position label can follow the pointer in real time. Without
        # this Qt only delivers moves while a button is pressed, leaving the
        # label frozen until a drag starts.
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.start_pos = None
        self.curr_pos = None
        # Last seen pointer position (widget-local) for the hover position
        # label; only used on the single-screen (non-session) path.
        self._cursor_local = None
        self.click_threshold = CAPTURE_CLICK_THRESHOLD_PX

        # Capture happens on a single (frozen) screen; only that screen's
        # pixels exist.  Clamp the selection cursor to this window's bounds so
        # a drag that strays onto a neighbouring (live, un-frozen) monitor
        # stops at the edge instead of silently selecting non-existent pixels.
        # Use the LOCAL rect (0,0,w,h): event.position() is widget-local, and
        # self.geometry() is global desktop coords — on a non-origin monitor
        # (e.g. secondary at x=2560) mixing them would clamp every local point
        # to the screen's global top-left, collapsing all drags into a click.
        self._selection_bounds = self.rect()

        self._topmost_debug_seq = 0

    def _debug_topmost_state(self, stage, extra=""):
        """Audit topmost state; expensive Win32 snapshot is only taken in DEBUG level."""
        if not logger.isEnabledFor(logging.DEBUG) or sys.platform != "win32":
            return

        try:
            user32 = ctypes.windll.user32
            self_hwnd = self.winId()
            fg_hwnd = user32.GetForegroundWindow()

            self._topmost_debug_seq += 1
            msg = (
                f"seq={self._topmost_debug_seq},stage={stage},"
                f"self=[{get_window_snapshot(self_hwnd)}],"
                f"fg=[{get_window_snapshot(fg_hwnd)}]"
            )
            if extra: msg += f", {extra}"
            logger.debug(f"topmost_audit | {msg}")
        except Exception:
            logger.debug(f"topmost_audit_err | {traceback.format_exc().strip()}")

    def showEvent(self, event):
        """Window show event: claim foreground/topmost and arm housekeeping."""
        super().showEvent(event)
        # Defer so the native window handle is fully ready, then claim focus
        # via the ShareX-style light path. Sibling overlays pass primary=False
        # so they stay topmost without fighting the cursor's screen for the
        # foreground.
        is_primary = not getattr(self, "_is_sibling_overlay", False)
        QtCore.QTimer.singleShot(0, lambda: claim_foreground(self, primary=is_primary))

        # In debug logging, run a delayed single-threaded audit.
        if logger.isEnabledFor(logging.DEBUG):
            QtCore.QTimer.singleShot(
                DEBUG_TOPMOST_DELAY_MS,
                lambda: self._debug_topmost_state(f"post_show_{DEBUG_TOPMOST_DELAY_MS}ms"),
            )

    def _set_clipboard_pixmap(self, pixmap, scene):
        """Write generated image into system clipboard.

        The screenshot image is always written here — it is the primary
        clipboard content.  When auto-OCR (prefetch) is enabled, OCR runs
        silently in the background *without* touching the clipboard; its
        result only fills an in-memory cache so a later thumbnail click is
        faster.  The image is never displaced from the clipboard.
        """
        try:
            if pixmap.isNull():
                logger.error(f"clip_err | scene={scene}, reason=null")
                return False

            cb = QtWidgets.QApplication.clipboard()
            cb.setPixmap(pixmap, mode=cb.Mode.Clipboard)
            
            # Compatibility fallback: process one event cycle if setPixmap is not immediate.
            if cb.pixmap(mode=cb.Mode.Clipboard).isNull():
                QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            
            if not cb.pixmap(mode=cb.Mode.Clipboard).isNull():
                return True

            # Fallback: try writing as QImage format.
            cb.setImage(pixmap.toImage(), mode=cb.Mode.Clipboard)
            success = not cb.image(mode=cb.Mode.Clipboard).isNull()
            if not success:
                logger.error(f"clip_failed | scene={scene}")
            return success
        except Exception:
            logger.error(f"clip_exc | scene={scene}, trace={traceback.format_exc().strip()}")
            return False

    def paintEvent(self, event):
        """Paint event: render fullscreen background, translucent overlay, and highlighted selection."""
        painter = QtGui.QPainter(self)
        # 1. Draw full-screen background.
        painter.drawPixmap(self.rect(), self.pixmap)
        # 2. Fill translucent overlay.
        painter.fillRect(self.rect(), QtGui.QColor(*CAPTURE_OVERLAY_RGBA))

        # 3. While dragging, render "cut-out" effect to reveal original area in selection.
        if self.session and self.session.global_start_pos and self.session.global_curr_pos:
            global_start = self.session.global_start_pos
            global_curr = self.session.global_curr_pos
            # global_rect is in contiguous physical virtual-desktop pixels.
            global_rect = QtCore.QRect(global_start, global_curr).normalized()
            wphys = self.physical_rect()
            dpr = self.dpr or 1.0
            # Map the full physical selection box into this screen's local
            # logical coords for drawing.  It may extend past this screen;
            # the painter clips to the widget, so each screen draws only its
            # own slice and the box reads as one seamless rectangle spanning
            # the boundary (no gap, because physical space is contiguous).
            local_rect = QtCore.QRect(
                round((global_rect.x() - wphys.x()) / dpr),
                round((global_rect.y() - wphys.y()) / dpr),
                round(global_rect.width() / dpr),
                round(global_rect.height() / dpr),
            )
        else:
            if self.start_pos and self.curr_pos:
                global_rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
                local_rect = global_rect
            else:
                local_rect = None

        show_label = bool(getattr(self.session, "show_dimension_label", True))
        if local_rect and local_rect.width() >= CAPTURE_SELECTION_MIN_PX and local_rect.height() >= CAPTURE_SELECTION_MIN_PX:
            intersected_rect = local_rect.intersected(self.rect())
            if not intersected_rect.isEmpty():
                painter.save()
                painter.setClipRect(intersected_rect)
                painter.drawPixmap(self.rect(), self.pixmap)
                painter.restore()

                # --- New Advanced Selection UI ---
                # Borders/handles use ``intersected_rect`` (this screen's
                # actual slice), NOT the full ``local_rect``.  When the
                # selection spans into a taller neighbour, ``local_rect``'s
                # bottom/right edge lies past this window and would be clipped
                # away — leaving the slice looking bottomless/rightless.
                # Drawing the slice's own edges keeps every screen's piece
                # properly framed; on a single screen the two rects are equal,
                # so behaviour is unchanged there.
                # 1. Draw Glow/Shadow Effect
                glow_pen = QtGui.QPen(QtGui.QColor(*BRAND_GREEN_RGB, 100), 4)
                painter.setPen(glow_pen)
                painter.drawRect(intersected_rect)

                # 2. Draw Main Vibrant Border
                main_pen = QtGui.QPen(QtGui.QColor(BRAND_GREEN), 1.5)
                painter.setPen(main_pen)
                painter.drawRect(intersected_rect)

                # 3. Draw Corner Handles (professional look)
                painter.setBrush(QtGui.QColor(BRAND_GREEN))
                painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.white, 1))

                corners = [
                    intersected_rect.topLeft(), intersected_rect.topRight(),
                    intersected_rect.bottomLeft(), intersected_rect.bottomRight()
                ]
                for pt in corners:
                    if self.rect().contains(pt):
                        painter.drawRect(QtCore.QRect(
                            pt.x() - _SELECTION_HANDLE_SIZE // 2,
                            pt.y() - _SELECTION_HANDLE_SIZE // 2,
                            _SELECTION_HANDLE_SIZE, _SELECTION_HANDLE_SIZE
                        ))

                # 4. Real-time Size Label
                if self.session and self.session.global_start_pos:
                    # global_rect is already in physical pixels — report those
                    # directly so the readout matches the captured image and
                    # Windows file properties (same behaviour as ShareX).
                    width_val = global_rect.width()
                    height_val = global_rect.height()
                else:
                    width_val = local_rect.width()
                    height_val = local_rect.height()
                size_text = f"{width_val} x {height_val}"
                font = painter.font()
                font.setPointSize(_DIMENSION_LABEL_FONT_SIZE)
                font.setBold(True)
                painter.setFont(font)

                # Calculate label background rect
                metrics = painter.fontMetrics()
                text_w = metrics.horizontalAdvance(size_text)
                text_h = metrics.height()

                bg_rect = QtCore.QRect(
                    intersected_rect.right() - text_w - _DIMENSION_LABEL_PAD_X * 2,
                    intersected_rect.bottom() + _DIMENSION_LABEL_OFFSET,
                    text_w + _DIMENSION_LABEL_PAD_X * 2,
                    text_h + _DIMENSION_LABEL_PAD_Y * 2
                )

                # Adjust if label goes off-screen
                if bg_rect.bottom() > self.height():
                    bg_rect.moveBottom(intersected_rect.bottom() - _DIMENSION_LABEL_OFFSET)
                if bg_rect.left() < 0:
                    bg_rect.moveLeft(_DIMENSION_LABEL_OFFSET)

                # Draw Label Background
                if show_label and self.rect().intersects(bg_rect):
                    painter.setBrush(QtGui.QColor(*_DIMENSION_LABEL_BG))
                    painter.setPen(QtCore.Qt.PenStyle.NoPen)
                    painter.drawRoundedRect(bg_rect, _DIMENSION_LABEL_RADIUS, _DIMENSION_LABEL_RADIUS)
                    
                    # Draw Text
                    painter.setPen(QtCore.Qt.GlobalColor.white)
                    painter.drawText(bg_rect, QtCore.Qt.AlignmentFlag.AlignCenter, size_text)
        elif show_label:
            self._draw_cursor_label(painter)

    def _draw_cursor_label(self, painter):
        """Draw the cursor's desktop position next to the pointer.

        Shown only while hovering (no active selection). On multi-screen
        sessions only the screen that contains the pointer draws it; siblings
        skip. Coordinates are physical pixels, matching the size label's readout.
        """
        if self.session is not None and getattr(self.session, "wins", None):
            cursor_phys = getattr(self.session, "global_cursor_phys", None)
            if cursor_phys is None or self.physical_rect_win is None:
                return
            if not self.physical_rect_win.contains(cursor_phys):
                return  # pointer is on a sibling screen — let it draw there
            ax = round((cursor_phys.x() - self.physical_top_left.x()) / self.dpr)
            ay = round((cursor_phys.y() - self.physical_top_left.y()) / self.dpr)
            anchor = QtCore.QPoint(ax, ay)
            pos_text = f"{cursor_phys.x()}, {cursor_phys.y()}"
        else:
            if self._cursor_local is None:
                return
            anchor = self._cursor_local
            phys_x = self.physical_top_left.x() + round(self._cursor_local.x() * self.dpr)
            phys_y = self.physical_top_left.y() + round(self._cursor_local.y() * self.dpr)
            pos_text = f"{phys_x}, {phys_y}"

        font = painter.font()
        font.setPointSize(_DIMENSION_LABEL_FONT_SIZE)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(pos_text)
        text_h = metrics.height()
        bg_rect = QtCore.QRect(
            anchor.x() + _DIMENSION_LABEL_OFFSET,
            anchor.y() + _DIMENSION_LABEL_OFFSET,
            text_w + _DIMENSION_LABEL_PAD_X * 2,
            text_h + _DIMENSION_LABEL_PAD_Y * 2,
        )
        # Flip inside the viewport if the label would overflow the screen.
        if bg_rect.right() > self.width():
            bg_rect.moveRight(anchor.x() - _DIMENSION_LABEL_OFFSET)
        if bg_rect.bottom() > self.height():
            bg_rect.moveBottom(anchor.y() - _DIMENSION_LABEL_OFFSET)
        if not self.rect().intersects(bg_rect):
            return
        painter.setBrush(QtGui.QColor(*_DIMENSION_LABEL_BG))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bg_rect, _DIMENSION_LABEL_RADIUS, _DIMENSION_LABEL_RADIUS)
        painter.setPen(QtCore.Qt.GlobalColor.white)
        painter.drawText(bg_rect, QtCore.Qt.AlignmentFlag.AlignCenter, pos_text)

    def _clamp_to_bounds(self, pos: QtCore.QPoint) -> QtCore.QPoint:
        """Clamp a position to the frozen screen's window bounds.

        A drag can stray past the window edge onto a neighbouring monitor
        (Qt keeps delivering events via the implicit mouse grab); those
        pixels were never captured, so we clamp the cursor here to keep the
        selection — and its size label — honest.
        """
        b = self._selection_bounds
        # QRect's right()/bottom() are the last valid pixel (inclusive), which
        # is exactly the edge we want to clamp to.
        return QtCore.QPoint(
            max(b.left(), min(pos.x(), b.right())),
            max(b.top(), min(pos.y(), b.bottom())),
        )

    def physical_rect(self) -> QtCore.QRect:
        """This screen's rect in contiguous physical virtual-desktop pixels.

        Prefers the real Win32 ``rcMonitor`` (which tiles edge-to-edge with
        every other monitor).  Falls back to ``physical_top_left`` +
        ``pixmap.size()`` (the native grab extent) for mock/test screens where
        no Win32 handle was resolved.
        """
        if self.physical_rect_win is not None:
            return self.physical_rect_win
        size = self.pixmap.size() if self.pixmap is not None else QtCore.QSize(0, 0)
        return QtCore.QRect(self.physical_top_left, size)

    def _global_physical_cursor(self):
        """Cursor position in contiguous physical virtual-desktop pixels.

        Delegates to ``dpi.cursor_physical_pos`` — see that helper for why the
        physical (Win32 ``GetCursorPos``) read is required: in *logical* space
        a shorter high-DPR neighbour leaves a "dead zone" that makes
        ``screenAt`` return ``None`` and a ``primaryScreen`` fallback snap the
        selection box back.  Physical space has no such gap.
        """
        pos = cursor_physical_pos()
        if pos is None:
            pos = self.physical_top_left
        return pos

    def _clamp_to_physical_desktop(self, phys_pos: QtCore.QPoint) -> QtCore.QPoint:
        """Clamp a physical virtual-desktop point to the combined physical bounds of all screens."""
        if not (self.session and self.session.wins):
            return phys_pos
        geom = QtCore.QRect()
        for win in self.session.wins:
            try:
                geom = geom.united(win.physical_rect())
            except Exception:
                logger.debug("capture: physical_rect union skipped a window", exc_info=True)
        if geom.isNull():
            return phys_pos
        return QtCore.QPoint(
            max(geom.left(), min(phys_pos.x(), geom.right())),
            max(geom.top(), min(phys_pos.y(), geom.bottom())),
        )

    def mousePressEvent(self, event):
        """Mouse press: record start point; right-click arms exit-on-release.

        Right-click does NOT close here. Closing on press would release the
        implicit mouse grab mid-click, and the subsequent ``WM_RBUTTONUP``
        would fall through to whatever window is below (typically the desktop),
        popping its context menu — the user would have to dismiss that too.
        Accepting the press holds the grab so the matching release still
        lands on the overlay; the actual close happens in
        ``mouseReleaseEvent`` once both the down and the up have been
        consumed by us.
        """
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            event.accept()
            return
        elif event.button() == QtCore.Qt.MouseButton.LeftButton:
            if hasattr(self, 'session') and self.session and self.session.wins:
                phys = self._global_physical_cursor()
                if phys is None:
                    phys = self.physical_top_left
                clamped = self._clamp_to_physical_desktop(phys)
                self.session.global_start_pos = clamped
                self.session.global_curr_pos = clamped
                self.session.update_all_windows()
            else:
                local_pos = event.position().toPoint()
                self.start_pos = self._clamp_to_bounds(local_pos)
                self.curr_pos = self.start_pos

    def mouseMoveEvent(self, event):
        """Mouse move: update selection and trigger repaint.

        Even while hovering (not dragging) we repaint so the cursor-position
        label can follow the pointer; the label is itself gated by
        ``show_dimension_label`` on the session.
        """
        if hasattr(self, 'session') and self.session and self.session.wins:
            phys = self._global_physical_cursor()
            if phys is None:
                phys = self.physical_top_left
            # Always share the live cursor so each overlay can render the
            # position label only on the screen that contains the pointer.
            self.session.global_cursor_phys = phys
            if self.session.global_start_pos:
                self.session.global_curr_pos = self._clamp_to_physical_desktop(phys)
            self.session.update_all_windows()
        else:
            local_pos = event.position().toPoint()
            self._cursor_local = local_pos
            if self.start_pos:
                self.curr_pos = self._clamp_to_bounds(local_pos)
            self.update()

    def mouseReleaseEvent(self, event):
        """Mouse release: choose region capture or fullscreen capture based on drag distance."""
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            # Close on release (not press) so the right-click's down+up are
            # both consumed by the overlay and never reach the desktop below.
            self.close()
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            if self.pixmap is None:
                if not (hasattr(self, 'session') and self.session):
                    self.start_pos = self.curr_pos = None
                return

            if hasattr(self, 'session') and self.session and self.session.wins:
                if not self.session.global_start_pos:
                    return

                global_start = self.session.global_start_pos
                phys = self._global_physical_cursor()
                if phys is None:
                    phys = self.physical_top_left
                global_curr = self._clamp_to_physical_desktop(phys)

                # Check drag threshold (click_threshold is logical px; the
                # selection is physical, so scale by this screen's DPR).
                threshold = self.click_threshold * (self.dpr or 1.0)
                if (global_curr - global_start).manhattanLength() <= threshold:
                    # Click (no drag): do nothing — aligned with Windows
                    # Snipping Tool.  No capture, no dismiss; the overlay
                    # stays.  Also avoids wasteful full-screen OCR when
                    # auto-OCR is enabled.
                    self.session.global_start_pos = None
                    self.session.global_curr_pos = None
                    self.session.update_all_windows()
                    return
                else:
                    global_rect = QtCore.QRect(global_start, global_curr).normalized()
                    captured, logical_size = self.session.crop_global_rect(global_rect)
                    if captured is not None:
                        self._set_clipboard_pixmap(captured, "region")

                if captured is not None:
                    self._notify_captured(captured, logical_size)

                self.session.global_start_pos = None
                self.session.global_curr_pos = None
                self.session._close_all_windows()
            else:
                if not self.start_pos:
                    return
                local_pos = event.position().toPoint()
                self.curr_pos = self._clamp_to_bounds(local_pos)
                rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
                captured = None
                logical_size = None

                if (self.curr_pos - self.start_pos).manhattanLength() <= self.click_threshold:
                    # Click (no drag): do nothing.
                    self.start_pos = self.curr_pos = None
                    self.update()
                    return
                else:
                    ratio = self.pixmap.devicePixelRatio()
                    physical = logical_to_physical_rect(rect, dpr=ratio)
                    final = self.pixmap.copy(physical)
                    final.setDevicePixelRatio(ratio)
                    self._set_clipboard_pixmap(final, "region")
                    captured = final
                    logical_size = rect.size()

                if captured is not None:
                    self._notify_captured(captured, logical_size)

                self.start_pos = self.curr_pos = None
                self.close()

    def keyPressEvent(self, event):
        """Keyboard handling: Esc exits capture mode."""
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()

    def _notify_captured(self, pixmap, logical_size):
        """Notify app layer with the captured image and its logical selection size."""
        if self.on_captured is None:
            return
        logger.debug(
            "[OCR_CHAIN] capture completed, size=%dx%d",
            pixmap.width(), pixmap.height(),
        )
        try:
            # signature: on_captured(pixmap, logical_size)
            self.on_captured(pixmap, logical_size)
        except Exception:
            logger.error(f"capture_notify_err | trace={traceback.format_exc().strip()}")

    def closeEvent(self, event):
        """Immediately release raw background screenshot reference on close."""
        self.pixmap = None
        if hasattr(self, 'on_closed') and self.on_closed:
            cb = self.on_closed
            self.on_closed = None
            try:
                cb()
            except Exception:
                logger.debug("capture: on_closed callback failed", exc_info=True)
        super().closeEvent(event)

