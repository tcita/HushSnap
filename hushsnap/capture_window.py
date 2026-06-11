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

        # Layout for content
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Left accent bar (HushSnap Green)
        accent = QtWidgets.QFrame()
        accent.setFixedWidth(3)
        accent.setStyleSheet(
            "background-color: #5FC98A;"
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
        
        # Adjust size and add shadow
        self.adjustSize()
        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QtGui.QColor(0, 0, 0, 100))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

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

                # --- New Advanced Selection UI ---
                # 1. Draw Glow/Shadow Effect
                glow_pen = QtGui.QPen(QtGui.QColor(95, 201, 138, 100), 4)
                painter.setPen(glow_pen)
                painter.drawRect(rect)

                # 2. Draw Main Vibrant Border
                main_pen = QtGui.QPen(QtGui.QColor("#5FC98A"), 1.5)
                painter.setPen(main_pen)
                painter.drawRect(rect)

                # 3. Draw Corner Handles (professional look)
                handle_size = 10
                painter.setBrush(QtGui.QColor("#5FC98A"))
                painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.white, 1))
                
                # Corners: Top-Left, Top-Right, Bottom-Left, Bottom-Right
                corners = [
                    rect.topLeft(), rect.topRight(), 
                    rect.bottomLeft(), rect.bottomRight()
                ]
                for pt in corners:
                    painter.drawRect(QtCore.QRect(
                        pt.x() - handle_size // 2, 
                        pt.y() - handle_size // 2, 
                        handle_size, handle_size
                    ))

                # 4. Real-time Size Label
                size_text = f"{rect.width()} x {rect.height()}"
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
                    rect.right() - text_w - padding_x * 2,
                    rect.bottom() + 5, # Positioned slightly below selection
                    text_w + padding_x * 2,
                    text_h + padding_y * 2
                )
                
                # Adjust if label goes off-screen
                if bg_rect.bottom() > self.height():
                    bg_rect.moveBottom(rect.bottom() - 5)
                if bg_rect.left() < 0:
                    bg_rect.moveLeft(5)

                # Draw Label Background
                painter.setBrush(QtGui.QColor(0, 0, 0, 180))
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.drawRoundedRect(bg_rect, 4, 4)
                
                # Draw Text
                painter.setPen(QtCore.Qt.GlobalColor.white)
                painter.drawText(bg_rect, QtCore.Qt.AlignmentFlag.AlignCenter, size_text)

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
            # Guard: pixmap may have been cleared if the window is already closing.
            if self.pixmap is None:
                self.start_pos = self.curr_pos = None
                return

            self.curr_pos = event.position().toPoint()
            rect = QtCore.QRect(self.start_pos, self.curr_pos).normalized()
            captured = None
            logical_size = None

            # If movement is too small, treat it as click -> fullscreen capture.
            copy_to_clipboard = get_copy_image_to_clipboard()
            if (self.curr_pos - self.start_pos).manhattanLength() <= self.click_threshold:
                full = self.pixmap.copy()
                full.setDevicePixelRatio(self.pixmap.devicePixelRatio())
                if copy_to_clipboard:
                    self._set_clipboard_pixmap(full, "fullscreen")
                captured = full
                logical_size = self.rect().size()
            else:
                # Region capture: convert logical coordinates to physical pixels by screen scale.
                ratio = self.pixmap.devicePixelRatio()
                physical = QtCore.QRect(
                    int(rect.x() * ratio), int(rect.y() * ratio),
                    int(rect.width() * ratio), int(rect.height() * ratio)
                )
                final = self.pixmap.copy(physical)
                # Ensure the pixmap knows its scaling ratio for correct clipboard/UI rendering
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
        super().closeEvent(event)

