from PyQt6 import QtWidgets

from .config import (
    get_ocr_enabled_from_config,
    get_ocr_lang_from_config,
    update_ocr_enabled_in_config,
    update_ocr_lang_in_config,
)
from .constants import TRAY_MSG_MEDIUM_MS
from .ocr_service import OcrRequest, OcrService
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
        self.ocr_action = None

        initial_lang = get_ocr_lang_from_config(config_path)
        lang_idx = self.popup.lang_combo.findData(initial_lang)
        if lang_idx >= 0:
            self.popup.lang_combo.setCurrentIndex(lang_idx)

        self.bridge.signal.connect(self.on_ocr_finished)
        self.popup.language_changed.connect(self.on_ocr_lang_changed)

    def attach_tray(self, tray_icon, ocr_action):
        self.tray_icon = tray_icon
        self.ocr_action = ocr_action
        self.ocr_action.setChecked(get_ocr_enabled_from_config(self.config_path))
        self.ocr_action.toggled.connect(self.on_ocr_toggled)

    def handle_capture_completed(self, captured_pixmap):
        """Start OCR after a screenshot has already been copied to the clipboard."""
        if not self._ocr_enabled():
            return
        self._start_request(
            captured_pixmap.copy(),
            self.popup.lang_combo.itemData(self.popup.lang_combo.currentIndex()),
        )

    def on_ocr_finished(self, payload):
        text, error, pixmap = payload
        if not self._ocr_enabled():
            return

        if error:
            self._show_tray_message(
                self.translate("ocr_failed_title"),
                self.translate("ocr_failed_body"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
            )
            return

        recognized = (text or "").strip()
        current_lang = self.popup.lang_combo.itemData(self.popup.lang_combo.currentIndex())

        if not recognized:
            self._show_tray_message(
                self.translate("ocr_empty_title"),
                self.translate("ocr_empty_body"),
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
            )
            self.popup.show_text(
                self.translate("ocr_empty_popup_hint"),
                pixmap=pixmap,
                lang=current_lang,
            )
            return

        clipboard = self.app.clipboard()
        if clipboard is not None:
            clipboard.setText(recognized)

        self.popup.show_text(
            recognized,
            pixmap=pixmap,
            lang=current_lang,
        )

    def on_ocr_lang_changed(self, lang):
        """Persist language changes and re-run OCR for the most recent capture."""
        update_ocr_lang_in_config(self.config_path, lang)

        pixmap = self.popup.last_pixmap
        if not pixmap or pixmap.isNull():
            return

        self._start_request(pixmap, lang)

    def on_ocr_toggled(self, enabled):
        update_ocr_enabled_in_config(self.config_path, enabled)
        self._show_tray_message(
            self.translate("ocr_toggle_title"),
            self.translate("ocr_enabled_body") if enabled else self.translate("ocr_disabled_body"),
            QtWidgets.QSystemTrayIcon.MessageIcon.Information,
        )

    def _ocr_enabled(self):
        return self.ocr_action is not None and self.ocr_action.isChecked()

    def _start_request(self, pixmap, language_tag):
        debug_dir = self.user_data_dir if self.save_debug_image else None
        request = OcrRequest(
            pixmap=pixmap,
            language_tag=language_tag,
            debug_dir=debug_dir,
        )
        self.service.recognize_async(
            request,
            lambda response: self.bridge.signal.emit((response.text, response.error, response.pixmap)),
        )

    def _show_tray_message(self, title, body, icon):
        if self.tray_icon is None:
            return
        self.tray_icon.showMessage(title, body, icon, TRAY_MSG_MEDIUM_MS)
