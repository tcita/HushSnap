import logging

from PyQt6 import QtCore, QtWidgets

from .config import (
    get_auto_copy_ocr_result,
    get_config_path,
    get_ocr_pinned,
    update_ocr_pinned,
)
from .constants import (
    OCR_ENGINE_PPOCR,
    TRAY_MSG_MEDIUM_MS,
    TRAY_NOTIFICATIONS_ENABLED,
)
from .ocr import OcrRequest, OcrService

from .signal_bridge import SignalBridge
from .system.memory_utils import get_working_set_mb, fmt_memory
from .ui.ocr_popup import OcrPopup
from .ui.thumbnail import thumbnail_manager
from .ui.toast import show_toast


class _ToastBridge(QtCore.QObject):
    """Temporary signal bridge for pinned image OCR results."""
    done = QtCore.pyqtSignal(object)


class OcrController:
    """Coordinate OCR requests, results, popup interactions, and persisted settings."""

    def __init__(
        self,
        app,
        translate,
        config_path,
        user_data_dir,
        save_debug_image=False,
        popup=None,
        service=None,
    ):
        self.app = app
        self.translate = translate
        self.config_path = config_path
        self.user_data_dir = user_data_dir
        self.save_debug_image = save_debug_image
        self.popup = popup or OcrPopup(translate)
        self.service = service or OcrService()
        self.bridge = SignalBridge()
        self.tray_icon = None
        self.capture_requester = None
        self.next_capture_should_ocr = False
        self._expecting_ocr_result = False
        self._toast_bridge = None

        self._current_engine = OCR_ENGINE_PPOCR

        self.bridge.signal.connect(self.on_ocr_finished)
        self.bridge.warmup_finished.connect(self._schedule_post_warmup_trim)
        
        # Load and apply persisted pin state
        initial_pinned = get_ocr_pinned()
        if initial_pinned:
            self.popup.set_pinned(True)
        self.popup.pin_toggled.connect(self._handle_pin_toggled)

        self._trim_timer = QtCore.QTimer()
        self._trim_timer.setSingleShot(True)
        self._trim_timer.timeout.connect(self._trim_current_engine)

        # Warm up background engine
        logging.debug("[OcrController] Scheduling background warmup on event loop start...")
        QtCore.QTimer.singleShot(0, self._background_warmup)

    def set_capture_requester(self, capture_requester):
        """Set callback used to request screenshot captures on demand."""
        self.capture_requester = capture_requester

    def is_busy(self):
        """Return True if an OCR request is currently in progress."""
        return self._expecting_ocr_result or self._toast_bridge is not None

    def set_popup_anchor(self, x, y, width=None, height=None):
        """Set preferred screen position for the next OCR popup appearance."""
        self.popup.set_anchor_pos(x, y, width, height)

    def copy_text_from_image(self, pixmap, toast_window):
        """Run OCR on *pixmap* and copy recognized text to clipboard."""
        from PyQt6 import QtGui
        image = pixmap.toImage() if isinstance(pixmap, QtGui.QPixmap) else pixmap
        request = OcrRequest(pixmap=image, debug_dir=None)

        bridge = _ToastBridge()
        bridge.done.connect(
            lambda resp: self._on_toast_ocr_done(resp, toast_window),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self.service.recognize_async(request, bridge.done.emit)
        self._toast_bridge = bridge

    def _on_toast_ocr_done(self, response, toast_window):
        """Main-thread handler: copy OCR text and show global toast."""
        self._toast_bridge = None
        self._trim_timer.start(5000)
        text = response.text.strip() if response.text else ""
        
        if len(text) <= 1 and not text.isalnum():
            text = ""
            
        if text:
            clipboard = self.app.clipboard()
            if clipboard:
                clipboard.setText(text)
        
        try:
            if not toast_window.isVisible():
                return
        except RuntimeError:
            return

        if text:
            show_toast(self.translate("pin_ocr_copied"))
        else:
            show_toast(self.translate("pin_ocr_empty"), is_error=True)

    def enable_ocr_next_capture(self):
        self.next_capture_should_ocr = True

    def _trim_current_engine(self):
        if self.next_capture_should_ocr or self._expecting_ocr_result:
            return
        from .ocr.engine import trim_engine
        try:
            trim_engine(self._current_engine)
        except Exception:
            logging.getLogger(__name__).exception("Trim failed")

    def _handle_pin_toggled(self, pinned):
        update_ocr_pinned(pinned)

    def handle_capture_completed(self, captured_pixmap):
        if not self.next_capture_should_ocr:
            return

        self.next_capture_should_ocr = False
        self.popup.clear_anchor()
        self.popup.show_loading(pixmap=captured_pixmap)
        self.start_request(captured_pixmap.copy())

    def on_ocr_finished(self, response):
        self._trim_timer.start(5000)
        logging.debug("[on_ocr_finished] engine=%s, text_len=%d", 
                      response.recognition.engine_type if response.recognition else "unknown",
                      len(response.text or ""))

        if not self._expecting_ocr_result:
            return

        self._expecting_ocr_result = False
        thumbnail_manager.dismiss_current()

        text = response.text
        error = response.error
        pixmap = response.pixmap

        if error:
            logging.error(f"OCR Error: {error}")
            self.popup.show_text(
                f"{self.translate('ocr_failed_title')}\n{self.translate('ocr_failed_body')}",
                pixmap=pixmap,
            )
            return

        recognized = (text or "").rstrip()
        if not recognized:
            logging.debug("OCR result is empty.")
            self.popup.show_text(self.translate("ocr_empty_popup_hint"), pixmap=pixmap)
            return

        if get_auto_copy_ocr_result(self.config_path):
            clipboard = self.app.clipboard()
            if clipboard:
                clipboard.setText(recognized)

        self.popup.show_text(
            recognized,
            pixmap=pixmap,
            lines=response.recognition.lines if response.recognition else None,
        )

    def start_request(self, pixmap):
        self._trim_timer.stop()
        self._expecting_ocr_result = True
        debug_dir = self.user_data_dir if self.save_debug_image else None

        from PyQt6 import QtGui
        image = pixmap.toImage() if isinstance(pixmap, QtGui.QPixmap) else pixmap
        request = OcrRequest(pixmap=image, engine=OCR_ENGINE_PPOCR, debug_dir=debug_dir)
        self.service.recognize_async(request, lambda response: self.bridge.signal.emit(response))

    def _background_warmup(self):
        import threading
        from .ocr.engine import warmup_engine
        if self.next_capture_should_ocr or self._expecting_ocr_result:
            self.bridge.warmup_finished.emit()
            return

        def run_warmup():
            try:
                warmup_engine(self._current_engine)
            except Exception:
                logging.error("Warmup failed", exc_info=True)
            finally:
                self.bridge.warmup_finished.emit()

        threading.Thread(target=run_warmup, daemon=True).start()

    def _schedule_post_warmup_trim(self):
        if self.next_capture_should_ocr or self._expecting_ocr_result:
            return
        self._trim_timer.start(0)
