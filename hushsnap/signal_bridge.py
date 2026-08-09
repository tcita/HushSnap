from PyQt6 import QtCore


class SignalBridge(QtCore.QObject):
    """Generic thread-safe signal bridge for passing arbitrary payloads."""

    # Generic 1-arg signal (used by CaptureSession)
    signal = QtCore.pyqtSignal(object)
    
    # OCR result signal (response only — no QObject args across threads).
    # The target popup is stored as a field on OcrController and read by
    # on_ocr_finished on the main thread.  Passing a QObject through a
    # cross-thread queued signal is unsafe because the C++ side may be
    # deleted between queue and delivery.
    ocr_result = QtCore.pyqtSignal(object)
    
    load_finished = QtCore.pyqtSignal()
