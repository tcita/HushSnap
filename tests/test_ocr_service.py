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

    service.recognize_async(ocr.OcrRequest(pixmap=sample_pixmap.toImage()), _done)

    assert done.wait(timeout=2), "OCR async callback timed out"
    assert result_holder["response"].text == "async"


def test_ocr_service_async_rejects_qpixmap(sample_pixmap):
    """A QPixmap must never cross the asynchronous worker boundary."""
    service = ocr.OcrService()

    with pytest.raises(TypeError, match="requires OcrRequest.pixmap to be a QImage"):
        service.recognize_async(ocr.OcrRequest(pixmap=sample_pixmap), lambda _response: None)


def test_ocr_service_shutdown_stops_worker_and_joins(monkeypatch, sample_pixmap):
    """shutdown() must set the flag, join the worker thread, and refuse new work."""
    started = threading.Event()
    release = threading.Event()

    def _recognize(*args, **kwargs):
        started.set()
        release.wait(timeout=2)
        return ocr.OcrRecognition(text="never delivered")

    register_engine(OCR_ENGINE_PPOCR, recognize=_recognize)

    service = ocr.OcrService()
    called = []

    service.recognize_async(
        ocr.OcrRequest(pixmap=sample_pixmap.toImage()),
        lambda response: called.append(response),
    )

    # Worker is blocked inside recognize(); shutdown must join it (with timeout)
    assert started.wait(timeout=2), "worker never started"
    service.shutdown(timeout=1)

    # Callback must NOT be delivered — shutdown drops the in-flight result
    release.set()
    import time
    time.sleep(0.2)
    assert called == []

    # After shutdown, no new work is accepted
    service.recognize_async(
        ocr.OcrRequest(pixmap=sample_pixmap.toImage()),
        lambda response: called.append(response),
    )
    time.sleep(0.2)
    assert called == []


def test_ocr_service_shutdown_idempotent(sample_pixmap):
    """Calling shutdown() more than once must not raise."""
    service = ocr.OcrService()
    service.shutdown()
    service.shutdown()


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
