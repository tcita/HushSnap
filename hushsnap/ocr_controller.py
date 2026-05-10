import logging
import os

from PyQt6 import QtWidgets

from .config import (
    get_ocr_engine,
    get_ocr_lang,
    get_ocr_preprocess_settings_from_config,
    update_ocr_engine,
    update_ocr_lang,
    get_config_path,
)
from .constants import (
    TRAY_MSG_MEDIUM_MS,
    TRAY_NOTIFICATIONS_ENABLED,
)
from .ocr import OcrPreprocessSettings, OcrRequest, OcrService
from .ocr.engine import identify_engine_error, release_engine
from .signal_bridge import SignalBridge
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
        self._warned_engine_unavailable: set[str] = set()
        self._force_ocr = False

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
        self.popup.language_changed.connect(self.on_ocr_lang_changed)
        self.popup.engine_changed.connect(self.on_ocr_engine_changed)
        self.popup.switch_language_requested.connect(self._handle_notice_switch_requested)
        self.popup.open_language_settings_requested.connect(self._open_windows_language_settings)

    def force_ocr_next_capture(self):
        """Flag the next capture to always run OCR (used by OCR hotkey)."""
        self._force_ocr = True

    def handle_capture_completed(self, captured_pixmap):
        """Start OCR after a screenshot if triggered via OCR hotkey."""
        logging.info(f"Capture completed. force_ocr: {self._force_ocr}")
        if not self._force_ocr:
            return
        self._start_request(
            captured_pixmap.copy(),
            self.popup.lang_combo.itemData(self.popup.lang_combo.currentIndex()),
            self.popup.engine_combo.itemData(self.popup.engine_combo.currentIndex()),
        )

    def on_ocr_finished(self, response):
        engine_name = response.recognition.engine_type if response.recognition else "unknown"
        error_part = f", Error: {response.error}" if response.error else ""
        logging.info(f"OCR finished (engine={engine_name}){error_part}, Text length: {len(response.text or '')}")
        if not self._force_ocr:
            return

        self._force_ocr = False

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

        self._start_request(
            pixmap,
            lang,
            self.popup.engine_combo.itemData(self.popup.engine_combo.currentIndex())
        )

    def on_ocr_engine_changed(self, engine):
        """Persist engine changes and re-run OCR for the most recent capture."""
        logging.info("Engine switched: %s -> %s", self._current_engine, engine)
        update_ocr_engine(engine)

        release_engine(self._current_engine)
        self._current_engine = engine

        pixmap = self.popup.last_pixmap
        if not pixmap or pixmap.isNull():
            logging.debug("on_ocr_engine_changed: no pixmap to re-OCR")
            return

        self._start_request(
            pixmap,
            self.popup.lang_combo.itemData(self.popup.lang_combo.currentIndex()),
            engine
        )

    def _start_request(self, pixmap, language_tag, engine):
        debug_dir = self.user_data_dir if self.save_debug_image else None
        
        # Load custom preprocess settings from config if available
        preprocess_dict = get_ocr_preprocess_settings_from_config(self.config_path)
        preprocess_settings = None
        if preprocess_dict:
            try:
                preprocess_settings = OcrPreprocessSettings(**preprocess_dict)
            except Exception as exc:
                logging.getLogger(__name__).warning(f"Failed to parse ocr_preprocess settings: {exc}")

        logging.info(f"Starting OCR request with engine: {engine}, language: {language_tag}")

        request = OcrRequest(
            pixmap=pixmap,
            language_tag=language_tag,
            engine=engine,
            debug_dir=debug_dir,
            preprocess_settings=preprocess_settings,
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
