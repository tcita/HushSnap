import threading

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap import ocr
from hushsnap.constants import OCR_ENGINE_PPOCR
from hushsnap.ocr.engine import register_engine


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


def test_ocr_service_sync_success(monkeypatch, sample_pixmap):
    register_engine(
        OCR_ENGINE_PPOCR,
        recognize=lambda *args, **kwargs: ocr.OcrRecognition(text="hello world"),
    )

    service = ocr.OcrService()
    response = service.recognize(
        ocr.OcrRequest(pixmap=sample_pixmap, language_tag="en-US"),
    )

    assert response.error == ""
    assert response.text == "hello world"
    assert response.recognition is not None


def test_ocr_service_sync_error(monkeypatch, sample_pixmap):
    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    register_engine(OCR_ENGINE_PPOCR, recognize=_raise)

    service = ocr.OcrService()
    response = service.recognize(ocr.OcrRequest(pixmap=sample_pixmap))

    assert response.text == ""
    assert "boom" in response.error
    assert response.recognition is None


def test_ocr_service_async_callback(monkeypatch, sample_pixmap):
    register_engine(
        OCR_ENGINE_PPOCR,
        recognize=lambda *args, **kwargs: ocr.OcrRecognition(text="async"),
    )

    service = ocr.OcrService()
    done = threading.Event()
    result_holder = {}

    def _done(response):
        result_holder["response"] = response
        done.set()

    service.recognize_async(ocr.OcrRequest(pixmap=sample_pixmap), _done)

    assert done.wait(timeout=2), "OCR async callback timed out"
    assert result_holder["response"].text == "async"


def test_ocr_service_receives_preprocessed_image(monkeypatch, sample_pixmap):
    captured = {}

    def _recognize(image, language_tag=""):
        captured["image"] = image
        captured["language_tag"] = language_tag
        return ocr.OcrRecognition(text="preprocessed")

    register_engine(OCR_ENGINE_PPOCR, recognize=_recognize)

    service = ocr.OcrService()
    response = service.recognize(
        ocr.OcrRequest(
            pixmap=sample_pixmap,
            language_tag="en-US",
            engine=OCR_ENGINE_PPOCR,
            debug_dir=None,
        )
    )

    assert response.text == "preprocessed"
    assert captured["language_tag"] == "en-US"
    # OcrService preprocesses the pixmap — the engine should receive a QImage
    assert isinstance(captured["image"], QtGui.QImage)
    assert captured["image"].format() == QtGui.QImage.Format.Format_RGB32

