import threading

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap import text_grab


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


def test_text_grab_service_sync_success(monkeypatch, sample_pixmap):
    monkeypatch.setattr(
        text_grab,
        "recognize_result_from_pixmap",
        lambda *args, **kwargs: text_grab.OcrRecognition(text=" hello world "),
    )

    service = text_grab.TextGrabOcrService()
    response = service.recognize(
        text_grab.TextGrabRequest(pixmap=sample_pixmap, language_tag="en-US"),
    )

    assert response.error == ""
    assert response.text == "hello world"
    assert response.recognition is not None


def test_text_grab_service_sync_error(monkeypatch, sample_pixmap):
    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(text_grab, "recognize_result_from_pixmap", _raise)

    service = text_grab.TextGrabOcrService()
    response = service.recognize(text_grab.TextGrabRequest(pixmap=sample_pixmap))

    assert response.text == ""
    assert "boom" in response.error
    assert response.recognition is None


def test_text_grab_service_async_callback(monkeypatch, sample_pixmap):
    monkeypatch.setattr(
        text_grab,
        "recognize_result_from_pixmap",
        lambda *args, **kwargs: text_grab.OcrRecognition(text="async"),
    )

    service = text_grab.TextGrabOcrService()
    done = threading.Event()
    result_holder = {}

    def _done(response):
        result_holder["response"] = response
        done.set()

    service.recognize_async(text_grab.TextGrabRequest(pixmap=sample_pixmap), _done)

    assert done.wait(timeout=2), "OCR async callback timed out"
    assert result_holder["response"].text == "async"
