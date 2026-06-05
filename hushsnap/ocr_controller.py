import logging

from PyQt6 import QtCore, QtWidgets

from .config import (
    get_auto_copy_ocr_result,
    get_config_path,
    get_ocr_pinned,
    update_ocr_pinned,
)
from .constants import (
    OCR_ENGINE_RAPID,
    OCR_RAPID_IDLE_RELEASE_MS,
    TRAY_MSG_MEDIUM_MS,
    TRAY_NOTIFICATIONS_ENABLED,
)
from .ocr import OcrRequest, OcrService
from .ocr.engine import release_engine
from .signal_bridge import SignalBridge
from .system.memory_utils import get_working_set_mb, fmt_memory
from .ui.ocr_popup import OcrPopup


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
        self._next_capture_should_ocr = False
        self._expecting_ocr_result = False

        self._current_engine = OCR_ENGINE_RAPID

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

        self._rapid_release_timer = QtCore.QTimer()
        self._rapid_release_timer.setSingleShot(True)
        self._rapid_release_timer.timeout.connect(self._release_idle_rapidocr)

        # Warm up as soon as the event loop starts so the engine is ready
        # before the user's first OCR call. Warmup runs on a background
        # thread; if the user beats us and triggers OCR first, we skip
        # warmup — the OCR path will initialize the engine on its own.
        logging.info("[OcrController] Scheduling background warmup on event loop start...")
        QtCore.QTimer.singleShot(0, self._background_warmup)

    def set_capture_requester(self, capture_requester):
        """Set callback used to request screenshot captures on demand."""
        self.capture_requester = capture_requester

    def enable_ocr_next_capture(self):
        """Enable OCR for the next capture (used by OCR hotkey)."""
        self._next_capture_should_ocr = True

    def _trim_current_engine(self):
        """Trim working set of the current OCR engine to minimize idle footprint."""
        if self._next_capture_should_ocr or self._expecting_ocr_result:
            logging.debug("[_trim_current_engine] Skipping trim: OCR request is active")
            return

        ws_before = get_working_set_mb()
        logging.info(
            "[_trim_current_engine] Trimming engine=%s (idle). %s",
            self._current_engine, fmt_memory(),
        )
        from .ocr.engine import trim_engine
        try:
            trim_engine(self._current_engine)
            ws_after = get_working_set_mb()
            logging.debug(
                "[_trim_current_engine] Trim done. %s (delta=%.1f MB)",
                fmt_memory(), ws_after - ws_before,
            )
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "[_trim_current_engine] Trim failed: %s. %s", exc, fmt_memory(),
            )

    def _release_idle_rapidocr(self):
        """Release RapidOCR after a longer idle period for low-footprint tray usage."""
        ws_before = get_working_set_mb()
        logging.info(
            "[_release_idle_rapidocr] RapidOCR idle (5m). Releasing engine. %s",
            fmt_memory(),
        )
        try:
            release_engine(OCR_ENGINE_RAPID)
            ws_after = get_working_set_mb()
            logging.debug(
                "[_release_idle_rapidocr] Release done. %s (delta=%.1f MB)",
                fmt_memory(), ws_after - ws_before,
            )
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "[_release_idle_rapidocr] Release failed: %s. %s", exc, fmt_memory(),
            )

    def _handle_pin_toggled(self, pinned):
        """Persist the popup pin state to the state file."""
        update_ocr_pinned(pinned)

    def _request_ocr_capture(self):
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is None or self.capture_requester is None:
            logging.debug("_request_ocr_capture: no screen or capture requester")
            return

        dpr = screen.devicePixelRatio()
        pixmap = screen.grabWindow(0)
        pixmap.setDevicePixelRatio(dpr)
        self.capture_requester(pixmap)

    def handle_capture_completed(self, captured_pixmap):
        """Start OCR after a screenshot if OCR is enabled for this capture."""
        logging.info(f"Capture completed. next_capture_should_ocr: {self._next_capture_should_ocr}")
        if not self._next_capture_should_ocr:
            return

        # Consume the intent flag once capture is done
        self._next_capture_should_ocr = False
        self._start_request(captured_pixmap.copy())

    def on_ocr_finished(self, response):
        self._trim_timer.start(30000)
        engine_name = response.recognition.engine_type if response.recognition else "unknown"
        self._rapid_release_timer.start(OCR_RAPID_IDLE_RELEASE_MS)
        error_part = f", Error: {response.error}" if response.error else ""
        logging.info(
            "[on_ocr_finished] engine=%s, text_len=%d%s. %s",
            engine_name, len(response.text or ""), error_part, fmt_memory(),
        )

        if not self._expecting_ocr_result:
            return

        self._expecting_ocr_result = False

        text = response.text
        error = response.error
        pixmap = response.pixmap

        if error:
            logging.error(f"OCR Error: {error}")
            self._show_tray_message(
                self.translate("ocr_failed_title"),
                self.translate("ocr_failed_body"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
            )
            return

        recognized = (text or "").strip()

        if not recognized:
            logging.info("OCR result is empty.")
            self._show_tray_message(
                self.translate("ocr_empty_title"),
                self.translate("ocr_empty_body"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
            )
            self.popup.show_text(
                self.translate("ocr_empty_popup_hint"),
                pixmap=pixmap,
            )
            return

        if get_auto_copy_ocr_result(self.config_path):
            clipboard = self.app.clipboard()
            if clipboard is not None:
                clipboard.setText(recognized)

        self.popup.show_text(
            recognized,
            pixmap=pixmap,
        )

    def _start_request(self, pixmap):
        self._trim_timer.stop()
        self._rapid_release_timer.stop()
        self._expecting_ocr_result = True
        debug_dir = self.user_data_dir if self.save_debug_image else None

        logging.info("[_start_request] engine=%s. %s", OCR_ENGINE_RAPID, fmt_memory())

        # Show "Recognizing…" immediately so the user knows a new OCR pass
        # is in progress — replaces any stale text from a previous result.
        self.popup.show_text(
            self.translate("ocr_recognizing"),
            pixmap=pixmap,
        )

        # Convert QPixmap to QImage on the main GUI thread to prevent thread-safety issues
        from PyQt6 import QtGui
        image = pixmap.toImage() if isinstance(pixmap, QtGui.QPixmap) else pixmap

        request = OcrRequest(
            pixmap=image,
            language_tag="",
            engine=OCR_ENGINE_RAPID,
            debug_dir=debug_dir,
        )
        self.service.recognize_async(
            request,
            lambda response: self.bridge.signal.emit(response),
        )

    def _show_tray_message(self, title, body, icon):
        if self.tray_icon is None or not TRAY_NOTIFICATIONS_ENABLED:
            return
        self.tray_icon.showMessage(title, body, icon, TRAY_MSG_MEDIUM_MS)

    def _background_warmup(self):
        """Pre-initialize OCR engine in a background thread to eliminate first-call latency."""
        import threading
        import time
        from .ocr.engine import warmup_engine

        # If the user already triggered OCR (e.g. pressed the OCR hotkey
        # before the event loop started processing this timer callback),
        # skip warmup — the OCR request will initialize the engine via its
        # own call to _get_engine(), making warmup redundant.
        if self._next_capture_should_ocr or self._expecting_ocr_result:
            logging.info(
                "[_background_warmup] Skipping warmup: OCR already requested "
                "(engine will be initialized by the OCR path)"
            )
            # Still signal completion so that downstream listeners (e.g.
            # tray-icon show) are not left waiting forever.
            self.bridge.warmup_finished.emit()
            return

        def run_warmup():
            t0 = time.perf_counter()
            ws_before = get_working_set_mb()
            logging.debug(
                "[_background_warmup] Thread started for engine=%s. %s",
                self._current_engine, fmt_memory(),
            )
            try:
                warmup_engine(self._current_engine)
                elapsed = (time.perf_counter() - t0) * 1000
                ws_after = get_working_set_mb()
                logging.debug(
                    "[_background_warmup] Warmup complete. %s "
                    "(delta=%.1f MB, took %.1fms)",
                    fmt_memory(), ws_after - ws_before, elapsed,
                )
            except Exception as exc:
                logging.error(
                    "[_background_warmup] Warmup failed: %s. %s",
                    exc, fmt_memory(), exc_info=True,
                )
            finally:
                logging.debug("[_background_warmup] Emitting warmup_finished signal...")
                self.bridge.warmup_finished.emit()

        thread = threading.Thread(target=run_warmup, daemon=True)
        thread.start()

    def _schedule_post_warmup_trim(self):
        """Trim memory after successful warmup, unless an OCR request is active."""
        logging.debug(
            "[_schedule_post_warmup_trim] Signal received. next_capture_should_ocr=%s, expecting_ocr_result=%s. %s",
            self._next_capture_should_ocr, self._expecting_ocr_result, fmt_memory(),
        )
        if self._next_capture_should_ocr or self._expecting_ocr_result:
            logging.info("[_schedule_post_warmup_trim] Post-warmup trim skipped: OCR request in progress")
            return

        logging.debug("[_schedule_post_warmup_trim] Deferring trim to next event-loop cycle...")
        self._trim_timer.start(0)
