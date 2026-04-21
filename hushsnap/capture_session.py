from PyQt6 import QtCore

from .capture_window import CaptureWindow
from .signal_bridge import SignalBridge


class CaptureSession(QtCore.QObject):
    """Own the capture request bridge and the active CaptureWindow lifecycle."""

    def __init__(self, on_capture_completed, window_factory=None, bridge=None):
        super().__init__()
        self.on_capture_completed = on_capture_completed
        self.window_factory = window_factory or CaptureWindow
        self.bridge = bridge or SignalBridge()
        self.win = None

        self.bridge.signal.connect(self.launch_capture_window)

    def request_capture(self, screen_pixmap):
        """Queue a capture request onto the Qt bridge."""
        self.bridge.signal.emit(screen_pixmap)

    def launch_capture_window(self, screen_pixmap):
        """Show a new capture window unless one is already active."""
        if self.win is not None:
            return

        self.win = self.window_factory(
            screen_pixmap,
            on_captured=self.on_capture_completed,
        )
        self.win.destroyed.connect(self._clear_window)
        self.win.show()

    def _clear_window(self, *_args):
        self.win = None
