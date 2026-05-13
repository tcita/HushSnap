from pathlib import Path

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap import ocr_controller
from hushsnap.ocr import OcrRecognition, OcrResponse


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def sample_pixmap(qapp):
    pixmap = QtGui.QPixmap(32, 32)
    pixmap.fill(QtCore.Qt.GlobalColor.white)
    return pixmap


def _translate(key, **kwargs):
    table = {
        "ocr_popup_title": "OCR Text",
        "ocr_copy_btn": "Copy",
        "ocr_failed_title": "Failed",
        "ocr_failed_body": "OCR failed",
        "ocr_engine_unavailable_title": "Engine unavailable",
        "ocr_engine_unavailable_body": "Windows OCR unavailable on this system",
        "ocr_lang_missing_title": "Missing language pack",
        "ocr_lang_missing_body": "{requested_lang} not installed; switch language or install it",
        "ocr_lang_missing_switch_btn": "Switch to {available_lang}",
        "ocr_lang_missing_open_settings_btn": "Open language settings",
        "ocr_lang_missing_cancel_btn": "Not now",
        "ocr_lang_installed_fallback": "Installed language",
        "ocr_open_settings_failed_title": "Cannot open settings",
        "ocr_open_settings_failed_body": "Cannot open settings",
        "ocr_lang_system_default": "system language",
        "ocr_lang_english": "Lang: EN",
        "ocr_lang_chinese_simplified": "Simplified Chinese",
        "ocr_lang_chinese_traditional": "Traditional Chinese",
        "ocr_lang_selector_tooltip": "Select OCR language",
        "ocr_engine_windows": "WindowsOCR",
        "ocr_engine_rapid": "RapidOCR",
        "ocr_empty_title": "Empty",
        "ocr_empty_body": "No text found",
        "ocr_empty_popup_hint": "No text recognized. Try selecting a larger area or making sure the screenshot contains clear text.",
        "ocr_toggle_title": "OCR",
        "ocr_enabled_body": "Enabled",
        "ocr_disabled_body": "Disabled",
    }
    return table[key].format(**kwargs)


class FakeService:
    def __init__(self):
        self.requests = []
        self.callbacks = []

    def recognize_async(self, request, callback):
        self.requests.append(request)
        self.callbacks.append(callback)


class FakeSignal:
    def __init__(self):
        self._handlers = []

    def connect(self, handler):
        self._handlers.append(handler)

    def emit(self, value):
        for handler in list(self._handlers):
            handler(value)


class FakeTrayIcon:
    def __init__(self):
        self.messages = []

    def showMessage(self, title, body, icon, timeout):
        self.messages.append((title, body, icon, timeout))


def _build_controller(monkeypatch, qapp, tmp_path, service=None):
    monkeypatch.setattr(ocr_controller, "get_ocr_lang", lambda state_path=None, config_path=None: "en-US")

    popup = ocr_controller.OcrPopup(_translate)
    popup.show = lambda: None
    popup.raise_ = lambda: None
    popup.activateWindow = lambda: None

    controller = ocr_controller.OcrController(
        app=qapp,
        translate=_translate,
        config_path=tmp_path / "fake-config.json",
        user_data_dir=Path("data"),
        save_debug_image=True,
        popup=popup,
        service=service or FakeService(),
    )
    tray_icon = FakeTrayIcon()
    controller.tray_icon = tray_icon
    return controller, tray_icon


def test_capture_completed_starts_ocr_request(monkeypatch, qapp, tmp_path, sample_pixmap):
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    controller.force_ocr_next_capture()

    controller.handle_capture_completed(sample_pixmap)

    assert len(service.requests) == 1
    assert service.requests[0].language_tag == "en-US"
    assert service.requests[0].debug_dir == Path("data")


def test_ocr_finished_copies_text_and_updates_popup(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller.force_ocr_next_capture()

    shown = {}

    def _show_text(text, pixmap=None, lang=None, engine=None):
        shown["text"] = text
        shown["pixmap"] = pixmap
        shown["lang"] = lang
        shown["engine"] = engine

    controller.popup.show_text = _show_text
    qapp.clipboard().clear()

    controller.on_ocr_finished(
        OcrResponse(text=" hello world ", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )

    assert qapp.clipboard().text() == "hello world"
    assert shown["text"] == "hello world"
    assert shown["pixmap"] is sample_pixmap
    assert shown["lang"] == "en-US"


def test_ocr_finished_shows_notice_when_selected_language_is_not_installed(
    monkeypatch, qapp, tmp_path, sample_pixmap
):
    controller, tray_icon = _build_controller(monkeypatch, qapp, tmp_path)
    controller.force_ocr_next_capture()
    tray_icon.messages.clear()
    shown = {}
    controller.popup.show_text = lambda *args, **kwargs: shown.update({"shown": True})

    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-CN",
        ),
    )

    controller.on_ocr_finished(response)
    controller.on_ocr_finished(response)

    assert tray_icon.messages == []
    assert shown["shown"] is True
    assert controller.popup.notice_frame.isHidden() is False
    assert "EN" in controller.popup.notice_label.text()
    assert controller.popup.notice_switch_btn.property("target_lang") == "zh-CN"


def test_ocr_lang_changed_persists_and_reruns(monkeypatch, qapp, tmp_path, sample_pixmap):
    saved = {}
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    controller.force_ocr_next_capture()
    monkeypatch.setattr(
        ocr_controller,
        "update_ocr_lang",
        lambda lang, state_path=None: saved.update({"lang": lang}),
    )
    controller.popup._last_pixmap = sample_pixmap

    controller.on_ocr_lang_changed("zh-CN")

    assert saved["lang"] == "zh-CN"
    assert len(service.requests) == 1
    assert service.requests[0].language_tag == "zh-CN"


def test_ocr_finished_warns_once_when_engine_is_unavailable(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, tray_icon = _build_controller(monkeypatch, qapp, tmp_path)
    controller.force_ocr_next_capture()

    response = OcrResponse(
        text="",
        error="Windows OCR engine unavailable.",
        pixmap=sample_pixmap,
        recognition=None,
    )

    controller.on_ocr_finished(response)
    controller.on_ocr_finished(response)

    assert tray_icon.messages == []
    assert "windows" in controller._warned_engine_unavailable


def test_ocr_missing_language_switches_and_reruns(monkeypatch, qapp, tmp_path, sample_pixmap):
    saved = {}
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    controller.force_ocr_next_capture()
    controller.popup.lang_combo.setCurrentIndex(controller.popup.lang_combo.findData("zh-CN"))
    monkeypatch.setattr(
        ocr_controller,
        "update_ocr_lang",
        lambda lang, state_path=None: saved.update({"lang": lang}),
    )
    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-TW",
        ),
    )

    controller.on_ocr_finished(response)
    controller.popup.notice_switch_btn.click()

    assert saved["lang"] == "zh-TW"
    assert len(service.requests) == 1
    assert service.requests[0].language_tag == "zh-TW"
    assert controller.popup.last_pixmap is sample_pixmap


def test_ocr_missing_language_can_open_settings(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller.force_ocr_next_capture()
    controller.popup.lang_combo.setCurrentIndex(controller.popup.lang_combo.findData("zh-CN"))
    opened = {}
    monkeypatch.setattr(ocr_controller.os, "startfile", lambda uri: opened.update({"called": uri}))

    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-TW",
        ),
    )

    controller.on_ocr_finished(response)
    controller.popup.notice_settings_btn.click()

    assert opened["called"] == "ms-settings:regionlanguage"


def test_ocr_missing_language_switch_falls_back_to_other_combo_language(
    monkeypatch, qapp, tmp_path, sample_pixmap
):
    saved = {}
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    controller.force_ocr_next_capture()
    monkeypatch.setattr(
        ocr_controller,
        "update_ocr_lang",
        lambda lang, state_path=None: saved.update({"lang": lang}),
    )
    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="",
        ),
    )

    controller.on_ocr_finished(response)
    controller.popup.notice_switch_btn.click()

    assert saved["lang"] == "zh-CN"
    assert len(service.requests) == 1
    assert service.requests[0].language_tag == "zh-CN"


def test_chinese_family_fallback_does_not_prompt_when_variant_is_available(
    monkeypatch, qapp, tmp_path, sample_pixmap
):
    controller, tray_icon = _build_controller(monkeypatch, qapp, tmp_path)
    controller.force_ocr_next_capture()
    controller.popup.lang_combo.setCurrentIndex(controller.popup.lang_combo.findData("zh-TW"))

    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-TW",
        ),
    )

    controller.on_ocr_finished(response)

    assert controller.popup.notice_frame.isHidden() is True
    assert tray_icon.messages == []


def test_simplified_chinese_family_fallback_does_not_prompt_when_variant_is_available(
    monkeypatch, qapp, tmp_path, sample_pixmap
):
    controller, tray_icon = _build_controller(monkeypatch, qapp, tmp_path)
    controller.force_ocr_next_capture()
    controller.popup.lang_combo.setCurrentIndex(controller.popup.lang_combo.findData("zh-CN"))

    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-SG",
        ),
    )

    controller.on_ocr_finished(response)

    assert controller.popup.notice_frame.isHidden() is True
    assert tray_icon.messages == []


def test_notice_hides_after_compatible_response(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller.popup.lang_combo.setCurrentIndex(controller.popup.lang_combo.findData("zh-CN"))

    incompatible_response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-TW",
        ),
    )
    compatible_response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=True,
            used_user_profile_fallback=False,
            engine_language_tag="zh-CN",
        ),
    )

    controller._force_ocr = True
    controller.on_ocr_finished(incompatible_response)
    assert controller.popup.notice_frame.isHidden() is False

    controller._force_ocr = True
    controller.on_ocr_finished(compatible_response)
    assert controller.popup.notice_frame.isHidden() is True


def test_force_ocr_next_capture_sets_flag(monkeypatch, qapp, tmp_path):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    assert controller._force_ocr is False

    controller.force_ocr_next_capture()
    assert controller._force_ocr is True


def test_handle_capture_completed_skips_when_not_forced(monkeypatch, qapp, tmp_path, sample_pixmap):
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)

    controller.handle_capture_completed(sample_pixmap)

    assert len(service.requests) == 0


def test_on_ocr_finished_clears_force_flag(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    qapp.clipboard().clear()

    controller._force_ocr = True
    controller.on_ocr_finished(
        OcrResponse(text="test", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )

    assert controller._force_ocr is False


def test_on_ocr_finished_skips_when_not_forced(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    qapp.clipboard().clear()

    controller._force_ocr = False
    controller.on_ocr_finished(
        OcrResponse(text="should not appear", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )

    assert qapp.clipboard().text() == ""
