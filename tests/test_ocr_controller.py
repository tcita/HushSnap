from pathlib import Path

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap import ocr_controller


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
        "ocr_empty_title": "Empty",
        "ocr_empty_body": "No text found",
        "ocr_empty_popup_hint": "Switch language and try again",
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


class FakeAction:
    def __init__(self):
        self._checked = False
        self.toggled = FakeSignal()

    def setChecked(self, checked):
        self._checked = checked

    def isChecked(self):
        return self._checked

    def toggle_to(self, checked):
        self._checked = checked
        self.toggled.emit(checked)


class FakeTrayIcon:
    def __init__(self):
        self.messages = []

    def showMessage(self, title, body, icon, timeout):
        self.messages.append((title, body, icon, timeout))


def _build_controller(monkeypatch, qapp, tmp_path, service=None):
    monkeypatch.setattr(ocr_controller, "get_ocr_lang_from_config", lambda path: "en-US")
    monkeypatch.setattr(ocr_controller, "get_ocr_enabled_from_config", lambda path: True)

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
    action = FakeAction()
    controller.attach_tray(tray_icon, action)
    return controller, tray_icon, action


def test_capture_completed_starts_ocr_request(monkeypatch, qapp, tmp_path, sample_pixmap):
    service = FakeService()
    controller, _, action = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    action.toggle_to(True)

    controller.handle_capture_completed(sample_pixmap)

    assert len(service.requests) == 1
    assert service.requests[0].language_tag == "en-US"
    assert service.requests[0].debug_dir == Path("data")


def test_ocr_finished_copies_text_and_updates_popup(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _, action = _build_controller(monkeypatch, qapp, tmp_path)
    action.toggle_to(True)

    shown = {}

    def _show_text(text, pixmap=None, lang=None):
        shown["text"] = text
        shown["pixmap"] = pixmap
        shown["lang"] = lang

    controller.popup.show_text = _show_text
    qapp.clipboard().clear()

    controller.on_ocr_finished((" hello world ", "", sample_pixmap))

    assert qapp.clipboard().text() == "hello world"
    assert shown["text"] == "hello world"
    assert shown["pixmap"] is sample_pixmap
    assert shown["lang"] == "en-US"


def test_ocr_lang_changed_persists_and_reruns(monkeypatch, qapp, tmp_path, sample_pixmap):
    saved = {}
    service = FakeService()
    controller, _, action = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    action.toggle_to(True)
    monkeypatch.setattr(
        ocr_controller,
        "update_ocr_lang_in_config",
        lambda path, lang: saved.update({"path": path, "lang": lang}),
    )
    controller.popup._last_pixmap = sample_pixmap

    controller.on_ocr_lang_changed("zh-CN")

    assert saved["lang"] == "zh-CN"
    assert len(service.requests) == 1
    assert service.requests[0].language_tag == "zh-CN"
