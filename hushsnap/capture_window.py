"""
Screenshot interaction UI module.
Handles fullscreen overlay display, drag-to-select drawing, final crop, and clipboard write.
"""

import ctypes
import logging
import sys
import traceback
from ctypes import wintypes

from PyQt6 import QtCore, QtGui, QtWidgets

from .config import get_copy_image_to_clipboard
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

FLOAT_UP_PX = 20
FLOAT_DURATION_MS = 600


class CopiedToast(QtWidgets.QWidget):
    """A tiny, sleek floating notification that appears at the cursor position."""

    def __init__(self, text: str, global_pos: QtCore.QPoint):
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Main layout for the top-level CopiedToast widget (transparent wrapper)
        outer_layout = QtWidgets.QVBoxLayout(self)
        # Margin around container for shadow to draw inside window bounds (blur radius 10, offset 2)
        outer_layout.setContentsMargins(12, 12, 12, 14)
        outer_layout.setSpacing(0)

        # Container widget for actual content
        self.container = QtWidgets.QFrame()
        self.container.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        outer_layout.addWidget(self.container)

        # Horizontal layout for content inside container
        layout = QtWidgets.QHBoxLayout(self.container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Left accent bar (HushSnap Green)
        accent = QtWidgets.QFrame()
        accent.setFixedWidth(3)
        accent.setStyleSheet(
            f"background-color: {BRAND_GREEN};"
            "border-top-left-radius: 6px;"
            "border-bottom-left-radius: 6px;"
        )
        layout.addWidget(accent)

        # Label content
        label = QtWidgets.QLabel(text)
        label.setStyleSheet(
            "QLabel {"
            "  color: #FFFFFF;"
            "  background: rgba(26, 26, 26, 0.98);"
            "  padding: 6px 14px;"
            "  border-top-right-radius: 6px;"
            "  border-bottom-right-radius: 6px;"
            "  font-size: 12px;"
            "  font-weight: 500;"
            "  font-family: \"Microsoft YaHei\", \"Segoe UI\", sans-serif;"
            "}"
        )
        layout.addWidget(label)
        
        self.adjustSize()

        # Drop shadow on the container widget to render inside the top-level window
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.container)
        shadow.setBlurRadius(10)
        shadow.setColor(QtGui.QColor(0, 0, 0, 100))
        shadow.setOffset(0, 2)
        self.container.setGraphicsEffect(shadow)

        # Center on cursor (accounting for its own width)
        target_pos = global_pos + QtCore.QPoint(-self.width() // 2, -self.height() // 2)
        self.move(target_pos)

        # Float upward animation
        self._float_anim = QtCore.QPropertyAnimation(self, b"pos")
        self._float_anim.setDuration(FLOAT_DURATION_MS)
        self._float_anim.setStartValue(target_pos)
        self._float_anim.setEndValue(target_pos + QtCore.QPoint(0, -FLOAT_UP_PX))
        self._float_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        # Fade out animation
        self._fade_anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(FLOAT_DURATION_MS)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)

        # Run both in parallel, close when done
        self._group = QtCore.QParallelAnimationGroup(self)
        self._group.addAnimation(self._float_anim)
        self._group.addAnimation(self._fade_anim)
        self._group.finished.connect(self.close)
        self._group.start()

    def showEvent(self, event):
        super().showEvent(event)
        # Animation already started in __init__; ensure it runs.
        if self._group.state() == QtCore.QAbstractAnimation.State.Stopped:
            self._group.start()


class CaptureWindow(QtWidgets.QWidget):
    """
    Fullscreen screenshot window.
    Workflow:
    1. Receives fullscreen bitmap as background.
    2. Configures frameless fullscreen topmost window.
    3. Handles mouse interaction:
       - Left drag: create selection.
       - Left click: capture full screen.
       - Right click / Esc: exit.
    """
    def __init__(self, pixmap, screen=None, on_captured=None, on_closed=None):
        super().__init__()
        self.pixmap = pixmap
        self.on_captured = on_captured
        self.on_closed = on_closed
        self.session = None

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
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.start_pos = None
        self.curr_pos = None
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
        """Window show event: asynchronously apply Win32 focus/topmost logic and arm safety auto-close."""
        super().showEvent(event)
        # Defer topmost/focus logic to async queue so window handle is fully ready.
        QtCore.QTimer.singleShot(0, self._force_win_topmost)

        # Auto-close after 25s so overlay is removed even if app gets stuck.
        QtCore.QTimer.singleShot(25000, self.close)
        
        # In debug logging, run a delayed single-threaded audit.
        if logger.isEnabledFor(logging.DEBUG):
            QtCore.QTimer.singleShot(
                DEBUG_TOPMOST_DELAY_MS,
                lambda: self._debug_topmost_state(f"post_show_{DEBUG_TOPMOST_DELAY_MS}ms"),
            )

    def _force_win_topmost(self):
        """
        Progressive topmost escalation:
        1. Soft topmost (HWND_TOPMOST) + focus transfer attempt.
        2. Verify focus state.
        3. Responsiveness check: only attach threads (AttachThreadInput) if target isn't hung.
        """
        if sys.platform != "win32": return
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            
            # --- Declare strict API signatures to avoid handle truncation on 64-bit systems ---
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.IsHungAppWindow.argtypes = [wintypes.HWND]
            user32.IsHungAppWindow.restype = wintypes.BOOL
            
            # SendMessageTimeoutW: send WM_CANCELMODE to foreground window with 200ms timeout
            # to avoid deadlock (WM_CANCELMODE=0x1F, SMTO_ABORTIFHUNG=0x2).
            user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(wintypes.DWORD)]
            user32.SendMessageTimeoutW.restype = wintypes.LPARAM
            
            user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
            user32.AttachThreadInput.restype = wintypes.BOOL
            
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetActiveWindow.argtypes = [wintypes.HWND]
            user32.SetFocus.argtypes = [wintypes.HWND]
            
            hwnd = get_hwnd_value(self.winId())
            if not hwnd: return
            
            # --- Stage 1: soft preemption (non-intrusive) ---
            fg_hwnd = user32.GetForegroundWindow()
            # Note: compare HWND values directly when using ctypes.
            if fg_hwnd and fg_hwnd != hwnd:
                unused_res = wintypes.DWORD()
                user32.SendMessageTimeoutW(fg_hwnd, 0x001F, 0, 0, 0x0002, 200, ctypes.byref(unused_res))
            
            # Base visual topmost (HWND_TOPMOST = -1, SW_SHOW = 5)
            # SWP_SHOWWINDOW=0x40, SWP_NOMOVE=0x2, SWP_NOSIZE=0x1
            user32.ShowWindow(hwnd, 5)
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0043)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetFocus(hwnd)

            # --- Stage 2: foreground verification and health check ---
            if user32.GetForegroundWindow() == hwnd:
                if logger.isEnabledFor(logging.DEBUG):
                    self._debug_topmost_state("force_complete_soft")
                return

            if not fg_hwnd: return
            
            # Check whether previous foreground window is hung to avoid blocking after attach.
            if user32.IsHungAppWindow(fg_hwnd):
                logger.warning(f"topmost_warn | Target window {fg_hwnd} is HUNG. Skipping AttachThreadInput.")
                return

            # --- Stage 3: intrusive forced attach (fallback) ---
            curr_tid = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
            
            attached = False
            if fg_tid and fg_tid != curr_tid:
                try:
                    attached = bool(user32.AttachThreadInput(curr_tid, fg_tid, True))
                except Exception as e:
                    logger.debug(f"topmost_warn | AttachThreadInput failed: {e}")

            try:
                if attached:
                    user32.SetForegroundWindow(hwnd)
                    user32.SetActiveWindow(hwnd)
                    user32.SetFocus(hwnd)
            finally:
                if attached:
                    # Detach threads.
                    user32.AttachThreadInput(curr_tid, fg_tid, False)

            if logger.isEnabledFor(logging.DEBUG):
                self._debug_topmost_state("force_complete_hard")
        except Exception:
            logger.error(f"topmost_err | {traceback.format_exc().strip()}")

    def _set_clipboard_pixmap(self, pixmap, scene):
        """Write generated image into system clipboard."""
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
                handle_size = 10
                painter.setBrush(QtGui.QColor(BRAND_GREEN))
                painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.white, 1))

                corners = [
                    intersected_rect.topLeft(), intersected_rect.topRight(),
                    intersected_rect.bottomLeft(), intersected_rect.bottomRight()
                ]
                for pt in corners:
                    if self.rect().contains(pt):
                        painter.drawRect(QtCore.QRect(
                            pt.x() - handle_size // 2,
                            pt.y() - handle_size // 2,
                            handle_size, handle_size
                        ))

                # 4. Real-time Size Label
                if self.session and self.session.global_start_pos:
                    # global_rect is physical; report logical (DIP) size via the
                    # session's max DPR so the label stays consistent across
                    # screens and matches the single-screen logical readout.
                    mdpr = self.session.max_dpr() or 1.0
                    width_val = round(global_rect.width() / mdpr)
                    height_val = round(global_rect.height() / mdpr)
                else:
                    width_val = local_rect.width()
                    height_val = local_rect.height()
                size_text = f"{width_val} x {height_val}"
                font = painter.font()
                font.setPointSize(9)
                font.setBold(True)
                painter.setFont(font)
                
                # Calculate label background rect
                metrics = painter.fontMetrics()
                text_w = metrics.horizontalAdvance(size_text)
                text_h = metrics.height()
                padding_x, padding_y = 6, 2
                
                bg_rect = QtCore.QRect(
                    intersected_rect.right() - text_w - padding_x * 2,
                    intersected_rect.bottom() + 5, # Positioned slightly below selection
                    text_w + padding_x * 2,
                    text_h + padding_y * 2
                )

                # Adjust if label goes off-screen
                if bg_rect.bottom() > self.height():
                    bg_rect.moveBottom(intersected_rect.bottom() - 5)
                if bg_rect.left() < 0:
                    bg_rect.moveLeft(5)

                # Draw Label Background
                if self.rect().intersects(bg_rect):
                    painter.setBrush(QtGui.QColor(0, 0, 0, 180))
                    painter.setPen(QtCore.Qt.PenStyle.NoPen)
                    painter.drawRoundedRect(bg_rect, 4, 4)
                    
                    # Draw Text
                    painter.setPen(QtCore.Qt.GlobalColor.white)
                    painter.drawText(bg_rect, QtCore.Qt.AlignmentFlag.AlignCenter, size_text)

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
                pass
        if geom.isNull():
            return phys_pos
        return QtCore.QPoint(
            max(geom.left(), min(phys_pos.x(), geom.right())),
            max(geom.top(), min(phys_pos.y(), geom.bottom())),
        )

    def mousePressEvent(self, event):
        """Mouse press: record start point or close window."""
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.close()
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
        """Mouse move: update selection and trigger repaint."""
        if hasattr(self, 'session') and self.session and self.session.wins:
            if self.session.global_start_pos:
                phys = self._global_physical_cursor()
                if phys is None:
                    phys = self.physical_top_left
                self.session.global_curr_pos = self._clamp_to_physical_desktop(phys)
                self.session.update_all_windows()
        else:
            if self.start_pos:
                local_pos = event.position().toPoint()
                self.curr_pos = self._clamp_to_bounds(local_pos)
                self.update()

    def mouseReleaseEvent(self, event):
        """Mouse release: choose region capture or fullscreen capture based on drag distance."""
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
                copy_to_clipboard = get_copy_image_to_clipboard()
                threshold = self.click_threshold * (self.dpr or 1.0)
                if (global_curr - global_start).manhattanLength() <= threshold:
                    # Capture full screen under cursor
                    full = self.pixmap.copy()
                    full.setDevicePixelRatio(self.pixmap.devicePixelRatio())
                    if copy_to_clipboard:
                        self._set_clipboard_pixmap(full, "fullscreen")
                    captured = full
                    logical_size = self.rect().size()
                else:
                    global_rect = QtCore.QRect(global_start, global_curr).normalized()
                    captured, logical_size = self.session.crop_global_rect(global_rect)
                    if captured is not None and copy_to_clipboard:
                        self._set_clipboard_pixmap(captured, "region")

                if captured is not None:
                    self._notify_captured(captured, logical_size)
                    if copy_to_clipboard:
                        self._show_copied_toast(event.globalPosition().toPoint())

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

                copy_to_clipboard = get_copy_image_to_clipboard()
                if (self.curr_pos - self.start_pos).manhattanLength() <= self.click_threshold:
                    full = self.pixmap.copy()
                    full.setDevicePixelRatio(self.pixmap.devicePixelRatio())
                    if copy_to_clipboard:
                        self._set_clipboard_pixmap(full, "fullscreen")
                    captured = full
                    logical_size = self.rect().size()
                else:
                    ratio = self.pixmap.devicePixelRatio()
                    physical = logical_to_physical_rect(rect, dpr=ratio)
                    final = self.pixmap.copy(physical)
                    final.setDevicePixelRatio(ratio)
                    if copy_to_clipboard:
                        self._set_clipboard_pixmap(final, "region")
                    captured = final
                    logical_size = rect.size()

                if captured is not None:
                    self._notify_captured(captured, logical_size)
                    if copy_to_clipboard:
                        self._show_copied_toast(event.globalPosition().toPoint())

                self.start_pos = self.curr_pos = None
                self.close()

    def keyPressEvent(self, event):
        """Keyboard handling: Esc exits capture mode."""
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()

    def _show_copied_toast(self, global_pos: QtCore.QPoint):
        """Show a floating 'Copied' toast at the cursor position."""
        try:
            from .config import resolve_ui_lang, ui_text, get_config_path
            lang = resolve_ui_lang(get_config_path())
            text = ui_text(lang, "capture_copied")
            toast = CopiedToast(text, global_pos)
            toast.show()
        except Exception:
            logger.error(f"copied_toast_err | trace={traceback.format_exc().strip()}")

    def _notify_captured(self, pixmap, logical_size):
        """Notify app layer with the captured image and its logical selection size."""
        if self.on_captured is None:
            return
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
                pass
        super().closeEvent(event)

