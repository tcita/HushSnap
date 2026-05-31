from PyQt6 import QtCore


class SignalBridge(QtCore.QObject):
    """Generic thread-safe signal bridge for passing arbitrary payloads."""

    signal = QtCore.pyqtSignal(object)
    warmup_finished = QtCore.pyqtSignal()
