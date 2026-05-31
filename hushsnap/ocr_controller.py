import logging
import os

from PyQt6 import QtCore, QtWidgets

from .config import (
    get_config_path,
    get_ocr_engine,
    get_ocr_lang,
    update_ocr_engine,
    update_ocr_lang,
)
from .constants import (
    OCR_ENGINE_RAPID,
    OCR_RAPID_IDLE_RELEASE_MS,
    TRAY_MSG_MEDIUM_MS,
    TRAY_NOTIFICATIONS_ENABLED,
)
from .ocr import OcrRequest, OcrService
from .ocr.engine import identify_engine_error, release_engine
from .signal_bridge import SignalBridge
from .system.memory_utils import get_working_set_mb, fmt_memory
from .ui.ocr_popup import OcrPopup


class OcrController:
    """Coordinate OCR requests, results, popup interactions, and persisted OCR settings."""

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
        self._warned_engine_unavailable: set[str] = set()
        self._should_ocr = False

        initial_lang = get_ocr_lang(config_path=config_path)
        lang_idx = self.popup.lang_combo.findData(initial_lang)
        if lang_idx >= 0:
            self.popup.lang_combo.setCurrentIndex(lang_idx)

        initial_engine = get_ocr_engine(config_path=config_path)
        self._current_engine = initial_engine
        engine_idx = self.popup.engine_combo.findData(initial_engine)
        if engine_idx >= 0:
            self.popup.engine_combo.setCurrentIndex(engine_idx)

        self.bridge.signal.connect(self.on_ocr_finished)
        self.bridge.warmup_finished.connect(self._schedule_post_warmup_trim)
        self.popup.language_changed.connect(self.on_ocr_lang_changed)
        self.popup.engine_changed.connect(self.on_ocr_engine_changed)
        self.popup.switch_language_requested.connect(self._handle_notice_switch_requested)
        self.popup.open_language_settings_requested.connect(self._open_windows_language_settings)
        self.popup.recapture_requested.connect(self.on_recapture_requested)

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
        """Set callback used by popup recapture button to open screenshot selection."""
        self.capture_requester = capture_requester

    def enable_ocr_next_capture(self):
        """Enable OCR for the next capture (used by OCR hotkey)."""
        self._should_ocr = True

    def _trim_current_engine(self):
        """Trim working set of the current OCR engine to minimize idle footprint."""
        # Guard: if OCR was requested between the timer being scheduled and
        # this callback firing, skip the trim. The engine is still in use
        # (or about to be), and on_ocr_finished will re-schedule a 30s trim.
        if self._should_ocr:
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

    def on_recapture_requested(self):
        """Start a fresh screenshot selection from the OCR popup."""
        if self.capture_requester is None:
            logging.debug("on_recapture_requested: no capture requester is configured")
            return

        self.enable_ocr_next_capture()
        self.popup.hide()
        QtCore.QTimer.singleShot(180, self._request_ocr_capture)

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
        logging.info(f"Capture completed. should_ocr: {self._should_ocr}")
        if not self._should_ocr:
            return
        self._start_request(
            captured_pixmap.copy(),
            self.popup.lang_combo.itemData(self.popup.lang_combo.currentIndex()),
            self.popup.engine_combo.itemData(self.popup.engine_combo.currentIndex()),
        )

    def on_ocr_finished(self, response):
        self._trim_timer.start(30000)
        engine_name = response.recognition.engine_type if response.recognition else "unknown"
        if engine_name == OCR_ENGINE_RAPID or self._current_engine == OCR_ENGINE_RAPID:
            self._rapid_release_timer.start(OCR_RAPID_IDLE_RELEASE_MS)
        error_part = f", Error: {response.error}" if response.error else ""
        logging.info(
            "[on_ocr_finished] engine=%s, text_len=%d%s. %s",
            engine_name, len(response.text or ""), error_part, fmt_memory(),
        )
        if not self._should_ocr:
            return

        self._should_ocr = False

        text = response.text
        error = response.error
        pixmap = response.pixmap

        self._update_missing_language_notice(response)

        if error:
            logging.error(f"OCR Error: {error}")
            if self._show_specific_ocr_error(error):
                return
            self._show_tray_message(
                self.translate("ocr_failed_title"),
                self.translate("ocr_failed_body"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
            )
            return

        recognized = (text or "").strip()
        current_lang = self.popup.lang_combo.itemData(self.popup.lang_combo.currentIndex())
        current_engine = self.popup.engine_combo.itemData(self.popup.engine_combo.currentIndex())

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
                lang=current_lang,
                engine=current_engine,
            )
            return

        clipboard = self.app.clipboard()
        if clipboard is not None:
            clipboard.setText(recognized)

        self.popup.show_text(
            recognized,
            pixmap=pixmap,
            lang=current_lang,
            engine=current_engine,
        )

    def on_ocr_lang_changed(self, lang):
        """Persist language changes and re-run OCR for the most recent capture."""
        update_ocr_lang(lang)

        pixmap = self.popup.last_pixmap
        if not pixmap or pixmap.isNull():
            logging.debug("on_ocr_lang_changed: no pixmap to re-OCR")
            return

        self._should_ocr = True
        self._start_request(
            pixmap,
            lang,
            self.popup.engine_combo.itemData(self.popup.engine_combo.currentIndex())
        )

    def on_ocr_engine_changed(self, engine):
        """Persist engine changes and re-run OCR for the most recent capture."""
        logging.info("Engine switched: %s -> %s", self._current_engine, engine)
        update_ocr_engine(engine)

        self._rapid_release_timer.stop()
        release_engine(self._current_engine)
        self._current_engine = engine

        pixmap = self.popup.last_pixmap
        if not pixmap or pixmap.isNull():
            logging.debug("on_ocr_engine_changed: no pixmap to re-OCR")
            return

        self._should_ocr = True
        self._start_request(
            pixmap,
            self.popup.lang_combo.itemData(self.popup.lang_combo.currentIndex()),
            engine
        )

    def _start_request(self, pixmap, language_tag, engine):
        self._trim_timer.stop()
        self._rapid_release_timer.stop()
        debug_dir = self.user_data_dir if self.save_debug_image else None

        logging.info("[_start_request] engine=%s. %s", engine, fmt_memory())

        # Convert QPixmap to QImage on the main GUI thread to prevent thread-safety issues
        from PyQt6 import QtGui
        image = pixmap.toImage() if isinstance(pixmap, QtGui.QPixmap) else pixmap

        request = OcrRequest(
            pixmap=image,
            language_tag=language_tag,
            engine=engine,
            debug_dir=debug_dir,
        )
        self.service.recognize_async(
            request,
            lambda response: self.bridge.signal.emit(response),
        )

    def _update_missing_language_notice(self, response):
        recognition = response.recognition
        if recognition is None:
            self.popup.hide_language_notice()
            return

        current_lang = self.popup.lang_combo.itemData(self.popup.lang_combo.currentIndex())
        if not current_lang:
            self.popup.hide_language_notice()
            return

        if recognition.requested_language_supported is not False:
            self.popup.hide_language_notice()
            return
        if not recognition.used_user_profile_fallback:
            self.popup.hide_language_notice()
            return
        if self._is_compatible_language_family(current_lang, recognition.engine_language_tag):
            self.popup.hide_language_notice()
            return

        available_lang = self._resolve_available_language_tag(recognition.engine_language_tag, current_lang)
        self.popup.show_language_notice(
            message=self.translate(
                "ocr_lang_missing_body",
                requested_lang=self._describe_language(current_lang),
            ),
            available_lang=available_lang,
        )

    def _resolve_available_language_tag(self, engine_language_tag, current_lang):
        normalized_engine_lang = self._normalize_combo_language_tag(engine_language_tag)
        normalized_current_lang = self._normalize_combo_language_tag(current_lang)

        if normalized_engine_lang:
            idx = self.popup.lang_combo.findData(normalized_engine_lang)
            if idx >= 0 and normalized_engine_lang != normalized_current_lang:
                return normalized_engine_lang
        for idx in range(self.popup.lang_combo.count()):
            candidate = self.popup.lang_combo.itemData(idx)
            if candidate and candidate != normalized_current_lang:
                return candidate
        return ""

    def _switch_ocr_language(self, language_tag, pixmap):
        language_tag = self._normalize_combo_language_tag(language_tag)
        update_ocr_lang(language_tag)
        idx = self.popup.lang_combo.findData(language_tag)
        if idx >= 0:
            self.popup._is_refreshing = True
            self.popup.lang_combo.setCurrentIndex(idx)
            self.popup._is_refreshing = False
        self.popup._last_pixmap = pixmap
        self.popup.hide_language_notice()
        self._start_request(
            pixmap,
            language_tag,
            self.popup.engine_combo.itemData(self.popup.engine_combo.currentIndex()),
        )

    def _handle_notice_switch_requested(self, language_tag):
        pixmap = self.popup.last_pixmap
        if not pixmap or pixmap.isNull():
            logging.debug("_handle_notice_switch_requested: no pixmap to re-OCR")
            return
        self._switch_ocr_language(language_tag, pixmap)

    def _open_windows_language_settings(self):
        try:
            os.startfile("ms-settings:regionlanguage")
        except Exception as exc:
            logging.getLogger(__name__).exception(f"Failed to open Windows language settings: {exc}")
            QtWidgets.QMessageBox.warning(
                self.popup,
                self.translate("ocr_open_settings_failed_title"),
                self.translate("ocr_open_settings_failed_body"),
            )

    def _describe_language(self, language_tag):
        if not language_tag:
            return self.translate("ocr_lang_system_default")

        normalized_language_tag = self._normalize_combo_language_tag(language_tag)
        idx = self.popup.lang_combo.findData(normalized_language_tag)
        if idx >= 0:
            return self.popup.lang_combo.itemText(idx).replace("Lang: ", "")
        return normalized_language_tag or language_tag

    def _normalize_combo_language_tag(self, language_tag):
        lowered = str(language_tag or "").strip().lower()
        if not lowered:
            return ""
        if lowered.startswith("en"):
            return "en-US"
        if lowered.startswith(("zh-tw", "zh-hk", "zh-mo", "zh-hant")):
            return "zh-TW"
        if lowered.startswith(("zh-cn", "zh-sg", "zh-hans", "zh")):
            return "zh-CN"
        return language_tag

    def _is_compatible_language_family(self, requested_lang, engine_language_tag):
        normalized_requested = self._normalize_combo_language_tag(requested_lang)
        normalized_engine = self._normalize_combo_language_tag(engine_language_tag)
        return bool(normalized_requested and normalized_requested == normalized_engine)

    def _show_specific_ocr_error(self, error):
        engine_id = identify_engine_error(error)
        if engine_id is None:
            return False

        if engine_id in self._warned_engine_unavailable:
            return True

        self._warned_engine_unavailable.add(engine_id)
        self._show_tray_message(
            self.translate("ocr_engine_unavailable_title"),
            self.translate("ocr_engine_unavailable_body"),
            QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
        )
        return True

    def _show_tray_message(self, title, body, icon):
        if self.tray_icon is None or not TRAY_NOTIFICATIONS_ENABLED:
            return
        self.tray_icon.showMessage(title, body, icon, TRAY_MSG_MEDIUM_MS)

    def _background_warmup(self):
        """Pre-initialize OCR engine in a background thread to eliminate first-call latency."""
        import threading
        from .ocr.engine import warmup_engine

        # If the user already triggered OCR (e.g. pressed the OCR hotkey
        # before the event loop started processing this timer callback),
        # skip warmup — the OCR request will initialize the engine via its
        # own call to _get_engine(), making warmup redundant.
        if self._should_ocr:
            logging.info(
                "[_background_warmup] Skipping warmup: OCR already requested "
                "(engine will be initialized by the OCR path)"
            )
            return

        def run_warmup():
            ws_before = get_working_set_mb()
            logging.debug(
                "[_background_warmup] Thread started for engine=%s. %s",
                self._current_engine, fmt_memory(),
            )
            try:
                warmup_engine(self._current_engine)
                ws_after = get_working_set_mb()
                logging.debug(
                    "[_background_warmup] Warmup complete. %s (delta=%.1f MB)",
                    fmt_memory(), ws_after - ws_before,
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
            "[_schedule_post_warmup_trim] Signal received. should_ocr=%s. %s",
            self._should_ocr, fmt_memory(),
        )
        if self._should_ocr:
            logging.info("[_schedule_post_warmup_trim] Post-warmup trim skipped: OCR request in progress")
            return

        # Defer to the next event-loop iteration so any pending UI updates
        # render first, then trim immediately — no reason to wait longer.
        # If the user triggers OCR before this fires, _start_request calls
        # _trim_timer.stop() and cancels the trim.
        logging.debug("[_schedule_post_warmup_trim] Deferring trim to next event-loop cycle...")
        self._trim_timer.start(0)
