import logging
import os

from PyQt6 import QtWidgets

from .config import (
    get_ocr_enabled_from_config,
    get_ocr_lang_from_config,
    update_ocr_enabled_in_config,
    update_ocr_lang_in_config,
)
from .constants import TRAY_MSG_MEDIUM_MS, TRAY_NOTIFICATIONS_ENABLED
from .ocr import OcrRequest, OcrService
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
        self._warned_engine_unavailable = False

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

    def on_ocr_finished(self, response):
        if not self._ocr_enabled():
            return

        text = response.text
        error = response.error
        pixmap = response.pixmap

        if self._handle_missing_language_pack(response):
            return

        if error:
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
            lambda response: self.bridge.signal.emit(response),
        )

    def _handle_missing_language_pack(self, response):
        recognition = response.recognition
        if recognition is None:
            return False

        current_lang = self.popup.lang_combo.itemData(self.popup.lang_combo.currentIndex())
        if not current_lang:
            return False

        if recognition.requested_language_supported is not False:
            return False
        if not recognition.used_user_profile_fallback:
            return False

        available_lang = self._resolve_available_language_tag(recognition.engine_language_tag, current_lang)
        action = self._show_missing_language_dialog(
            requested_lang=self._describe_language(current_lang),
            available_lang=self._describe_language(available_lang) if available_lang else "",
        )
        if action == "switch" and available_lang and response.pixmap is not None:
            self._switch_ocr_language(available_lang, response.pixmap)
        elif action == "settings":
            self._open_windows_language_settings()
        return True

    def _resolve_available_language_tag(self, engine_language_tag, current_lang):
        if engine_language_tag:
            idx = self.popup.lang_combo.findData(engine_language_tag)
            if idx >= 0 and engine_language_tag != current_lang:
                return engine_language_tag
        for idx in range(self.popup.lang_combo.count()):
            candidate = self.popup.lang_combo.itemData(idx)
            if candidate and candidate != current_lang:
                return candidate
        return ""

    def _show_missing_language_dialog(self, requested_lang, available_lang):
        dialog = QtWidgets.QMessageBox(self.popup)
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        dialog.setWindowTitle(self.translate("ocr_lang_missing_title"))
        dialog.setText(
            self.translate(
                "ocr_lang_missing_body",
                requested_lang=requested_lang,
            )
        )

        switch_label = self.translate(
            "ocr_lang_missing_switch_btn",
            available_lang=available_lang or self.translate("ocr_lang_installed_fallback"),
        )
        switch_button = dialog.addButton(
            switch_label,
            QtWidgets.QMessageBox.ButtonRole.AcceptRole,
        )
        settings_button = dialog.addButton(
            self.translate("ocr_lang_missing_open_settings_btn"),
            QtWidgets.QMessageBox.ButtonRole.ActionRole,
        )
        dialog.addButton(
            self.translate("ocr_lang_missing_cancel_btn"),
            QtWidgets.QMessageBox.ButtonRole.RejectRole,
        )
        if not available_lang:
            switch_button.setEnabled(False)

        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is switch_button:
            return "switch"
        if clicked is settings_button:
            return "settings"
        return "cancel"

    def _switch_ocr_language(self, language_tag, pixmap):
        update_ocr_lang_in_config(self.config_path, language_tag)
        idx = self.popup.lang_combo.findData(language_tag)
        if idx >= 0:
            self.popup._is_refreshing = True
            self.popup.lang_combo.setCurrentIndex(idx)
            self.popup._is_refreshing = False
        self.popup._last_pixmap = pixmap
        self._start_request(pixmap, language_tag)

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

        idx = self.popup.lang_combo.findData(language_tag)
        if idx >= 0:
            return self.popup.lang_combo.itemText(idx).replace("Lang: ", "")
        return language_tag

    def _show_specific_ocr_error(self, error):
        lowered = (error or "").lower()
        if "windows ocr engine unavailable" not in lowered:
            return False

        if self._warned_engine_unavailable:
            return True

        self._warned_engine_unavailable = True
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
