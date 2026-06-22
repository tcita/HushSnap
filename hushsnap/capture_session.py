from PyQt6 import QtCore

from .capture_window import CaptureWindow
from .signal_bridge import SignalBridge
from .logging_config import get_logger

logger = get_logger(__name__)


class CaptureSession(QtCore.QObject):
    """Own the capture request bridge and the active CaptureWindow lifecycles."""

    def __init__(self, on_capture_completed, window_factory=None, bridge=None):
        super().__init__()
        self.on_capture_completed = on_capture_completed
        self.window_factory = window_factory or CaptureWindow
        self.bridge = bridge or SignalBridge()
        self.wins = []
        self.global_start_pos = None
        self.global_curr_pos = None

        self.bridge.signal.connect(self.launch_capture_window)

    @property
    def win(self):
        """Maintain backward compatibility for tests checking self.win."""
        return self.wins[0] if self.wins else None

    @win.setter
    def win(self, value):
        """Maintain backward compatibility for clearing/setting self.win in tests."""
        if value is None:
            self.wins = []
        else:
            self.wins = [value]

    def request_capture(self, screens_and_pixmaps):
        """Queue a capture request onto the Qt bridge."""
        self.bridge.signal.emit(screens_and_pixmaps)

    def launch_capture_window(self, screens_and_pixmaps):
        """Show capture windows across all screens unless active."""
        if self.wins:
            return

        from PyQt6 import QtGui
        is_legacy = False
        if isinstance(screens_and_pixmaps, QtGui.QPixmap):
            is_legacy = True
        elif not isinstance(screens_and_pixmaps, list):
            # Fallback for mock objects in testing
            is_legacy = True

        if is_legacy:
            win = self.window_factory(
                screens_and_pixmaps,
                on_captured=self.on_capture_completed,
            )
            win.session = self
            self.wins = [win]
            try:
                win.destroyed.connect(self._clear_window)
            except Exception:
                logger.debug("launch_capture_window: destroyed.connect failed", exc_info=True)
            win.show()
            return

        self.wins = []

        def handle_captured(pixmap, logical_size):
            if self.on_capture_completed:
                self.on_capture_completed(pixmap, logical_size)
            self._close_all_windows()

        def handle_closed():
            self._close_all_windows()

        for screen, pixmap in screens_and_pixmaps:
            try:
                win = self.window_factory(
                    pixmap=pixmap,
                    screen=screen,
                    on_captured=handle_captured,
                    on_closed=handle_closed,
                )
                win.session = self
                win.destroyed.connect(lambda _obj=None, w=win: self._remove_window(w))
                self.wins.append(win)
            except Exception:
                import traceback
                QtCore.qWarning(f"Failed to create CaptureWindow for screen: {traceback.format_exc()}")

        for win in self.wins:
            win.show()

    def update_all_windows(self):
        """Repaint all overlay windows to keep cross-screen selections in sync."""
        for win in self.wins:
            try:
                win.update()
            except Exception:
                logger.debug("update_all_windows: repaint failed", exc_info=True)

    def max_dpr(self) -> float:
        """Highest devicePixelRatio among the active screens (for logical-size readouts)."""
        try:
            return max((w.dpr for w in self.wins), default=1.0)
        except Exception:
            logger.debug("max_dpr: failed to read DPRs", exc_info=True)
            return 1.0

    def crop_global_rect(self, global_rect):
        """Crop & stitch the selection from all intersecting screens.

        ``global_rect`` is in contiguous physical virtual-desktop pixels.
        Each screen's native grab is copied 1:1 into a canvas the size of
        the selection (physical), so every monitor contributes its native
        resolution with no resampling and no inter-screen gap.  The canvas
        is tagged with the max DPR so consumers see a logical size.
        """
        from PyQt6 import QtGui

        parts = []
        max_dpr = 1.0
        for win in self.wins:
            try:
                wphys = win.physical_rect()
                inter = wphys.intersected(global_rect)
                if inter.isEmpty():
                    continue
                parts.append((win, wphys, inter))
                max_dpr = max(max_dpr, win.pixmap.devicePixelRatio())
            except Exception:
                logger.debug("crop_global_rect: screen skipped", exc_info=True)
                continue

        if not parts:
            return None, QtCore.QSize(0, 0)

        phys_size = global_rect.size()
        # Canvas is allocated at native size with DPR 1 during painting so we
        # can place each screen's native grab 1:1 via integer physical-pixel
        # coords (QRect targets — the only 2-arg drawPixmap overload PyQt6
        # exposes).  The canvas DPR is tagged afterwards for consumers.
        stitched = QtGui.QPixmap(phys_size.width(), phys_size.height())
        stitched.fill(QtCore.Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(stitched)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)

        gtl = global_rect.topLeft()
        for win, wphys, inter in parts:
            try:
                # Source: native pixels within this screen's grab.
                src = inter.translated(-wphys.topLeft())
                part = win.pixmap.copy(src)
                # Treat the part as 1:1 native (no per-part DPR scaling) so it
                # lands in the DPR-1 canvas pixel-for-pixel — no resampling,
                # no seams across the screen boundary.
                part.setDevicePixelRatio(1.0)
                dst = QtCore.QRect(
                    inter.x() - gtl.x(),
                    inter.y() - gtl.y(),
                    inter.width(),
                    inter.height(),
                )
                painter.drawPixmap(dst, part)
            except Exception:
                logger.debug("crop_global_rect: stitch part failed", exc_info=True)
                continue

        painter.end()
        stitched.setDevicePixelRatio(max_dpr)
        logical_size = QtCore.QSize(
            max(1, round(phys_size.width() / max_dpr)),
            max(1, round(phys_size.height() / max_dpr)),
        )
        return stitched, logical_size

    def _remove_window(self, win):
        if win in self.wins:
            self.wins.remove(win)

    def _close_all_windows(self):
        if not self.wins:
            return
        wins_to_close = list(self.wins)
        self.wins = []
        for win in wins_to_close:
            try:
                # Disconnect callback references to prevent recursion
                win.on_captured = None
                win.on_closed = None
                win.close()
            except Exception:
                logger.debug("_close_all_windows: close failed", exc_info=True)

    def _clear_window(self, *_args):
        """Helper for legacy destroyed signal connection."""
        self.wins = []

