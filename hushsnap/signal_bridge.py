from PyQt6 import QtCore


class SignalBridge(QtCore.QObject):
    """Generic thread-safe signal bridge for passing arbitrary payloads."""

    # Generic 1-arg signal (used by CaptureSession)
    signal = QtCore.pyqtSignal(object)
    
    # Specific 2-arg signal (used by OcrController)
    ocr_result = QtCore.pyqtSignal(object, object)
    
    warmup_finished = QtCore.pyqtSignal()
