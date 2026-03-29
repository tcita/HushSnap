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

logger = get_logger(__name__)


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
    def __init__(self, pixmap, on_captured=None):
        super().__init__()
        self.pixmap = pixmap
        self.on_captured = on_captured

        # Configure window attributes: tool style, frameless, initially topmost.
        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

        # Fill primary screen.
        self.setWindowState(QtCore.Qt.WindowState.WindowFullScreen)
        screen = QtWidgets.QApplication.primaryScreen()
        self.setGeometry(screen.geometry())

        # Initialize interaction state.
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.start_pos = None
        self.curr_pos = None
        self.click_threshold = CAPTURE_CLICK_THRESHOLD_PX

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
        if self.start_pos and self.curr_pos:
            rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
            if rect.width() >= CAPTURE_SELECTION_MIN_PX and rect.height() >= CAPTURE_SELECTION_MIN_PX:
                painter.save()
                painter.setClipRect(rect)
                painter.drawPixmap(self.rect(), self.pixmap)
                painter.restore()
                # Draw dark orange selection border.
                painter.setPen(QtGui.QPen(QtGui.QColor("#FF8C00"), 2))
                painter.drawRect(rect)

    def mousePressEvent(self, event):
        """Mouse press: record start point or close window."""
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.close()
        elif event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.start_pos = event.position().toPoint()
            self.curr_pos = self.start_pos

    def mouseMoveEvent(self, event):
        """Mouse move: update selection and trigger repaint."""
        if self.start_pos:
            self.curr_pos = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        """Mouse release: choose region capture or fullscreen capture based on drag distance."""
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.start_pos:
            self.curr_pos = event.position().toPoint()
            rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
            captured = None
            
            # If movement is too small, treat it as click -> fullscreen capture.
            if (self.curr_pos - self.start_pos).manhattanLength() <= self.click_threshold:
                full = self.pixmap.copy()
                full.setDevicePixelRatio(self.pixmap.devicePixelRatio())
                self._set_clipboard_pixmap(full, "fullscreen")
                captured = full
            else:
                # Region capture: convert logical coordinates to physical pixels by screen scale.
                ratio = self.pixmap.devicePixelRatio()
                physical = QtCore.QRect(
                    int(rect.x() * ratio), int(rect.y() * ratio),
                    int(rect.width() * ratio), int(rect.height() * ratio)
                )
                final = self.pixmap.copy(physical)
                final.setDevicePixelRatio(ratio)
                self._set_clipboard_pixmap(final, "region")
                captured = final

            if captured is not None:
                self._notify_captured(captured)

            self.start_pos = self.curr_pos = None
            self.close()

    def keyPressEvent(self, event):
        """Keyboard handling: Esc exits capture mode."""
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()

    def _notify_captured(self, pixmap):
        """Notify app layer with the captured image for optional OCR flow."""
        if self.on_captured is None:
            return
        try:
            self.on_captured(pixmap)
        except Exception:
            logger.error(f"capture_notify_err | trace={traceback.format_exc().strip()}")
