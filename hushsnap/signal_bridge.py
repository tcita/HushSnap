from PyQt6 import QtCore


class _OcrResultEvent(QtCore.QEvent):
    """Custom event carrying an OCR response from the worker thread to the
    main thread via QCoreApplication.postEvent(), which is explicitly
    thread-safe per Qt documentation.

    ``kind`` is 'popup' for thumbnail-click OCR or 'toast' for auto-OCR.
    On the main thread, SignalBridge.event() routes the response to the
    correct handler by emitting ocr_result or auto_ocr_done.
    """

    _EVENT_TYPE = QtCore.QEvent.Type(QtCore.QEvent.registerEventType())

    __slots__ = ("response", "kind")

    def __init__(self, response, kind):
        super().__init__(self._EVENT_TYPE)
        self.response = response
        self.kind = kind


class _LoadFinishedEvent(QtCore.QEvent):
    """Lightweight event posted from the background load thread to the main
    thread via QCoreApplication.postEvent().  Carries no payload — the
    receiver just emits load_finished on the main thread."""

    _EVENT_TYPE = QtCore.QEvent.Type(QtCore.QEvent.registerEventType())

    def __init__(self):
        super().__init__(self._EVENT_TYPE)


class SignalBridge(QtCore.QObject):
    """Thread-safe bridge that receives OCR results from worker threads
    via postEvent and emits them as Qt signals on the main thread."""

    # Generic 1-arg signal (used by CaptureSession)
    signal = QtCore.pyqtSignal(object)

    # OCR popup path (thumbnail click → start_request)
    ocr_result = QtCore.pyqtSignal(object)

    # Auto-OCR-to-clipboard path (toast, no popup)
    auto_ocr_done = QtCore.pyqtSignal(object)

    load_finished = QtCore.pyqtSignal()

    def event(self, event):
        if isinstance(event, _OcrResultEvent):
            if event.kind == "popup":
                self.ocr_result.emit(event.response)
            elif event.kind == "toast":
                self.auto_ocr_done.emit(event.response)
            return True
        if isinstance(event, _LoadFinishedEvent):
            self.load_finished.emit()
            return True
        return super().event(event)
