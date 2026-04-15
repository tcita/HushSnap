import threading

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap import ocr_service


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
    monkeypatch.setattr(
        ocr_service,
        "recognize_result_from_pixmap",
        lambda *args, **kwargs: ocr_service.OcrRecognition(text=" hello world "),
    )

    service = ocr_service.OcrService()
    response = service.recognize(
        ocr_service.OcrRequest(pixmap=sample_pixmap, language_tag="en-US"),
    )

    assert response.error == ""
    assert response.text == "hello world"
    assert response.recognition is not None


def test_ocr_service_sync_error(monkeypatch, sample_pixmap):
    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ocr_service, "recognize_result_from_pixmap", _raise)

    service = ocr_service.OcrService()
    response = service.recognize(ocr_service.OcrRequest(pixmap=sample_pixmap))

    assert response.text == ""
    assert "boom" in response.error
    assert response.recognition is None


def test_ocr_service_async_callback(monkeypatch, sample_pixmap):
    monkeypatch.setattr(
        ocr_service,
        "recognize_result_from_pixmap",
        lambda *args, **kwargs: ocr_service.OcrRecognition(text="async"),
    )

    service = ocr_service.OcrService()
    done = threading.Event()
    result_holder = {}

    def _done(response):
        result_holder["response"] = response
        done.set()

    service.recognize_async(ocr_service.OcrRequest(pixmap=sample_pixmap), _done)

    assert done.wait(timeout=2), "OCR async callback timed out"
    assert result_holder["response"].text == "async"


def test_compose_text_from_result_keeps_chinese_tokens_intact():
    result = ocr_service.OcrRecognition(
        lines=[
            ocr_service.OcrLine(
                words=[
                    ocr_service.OcrWord(text="沪"),
                    ocr_service.OcrWord(text="A"),
                    ocr_service.OcrWord(text="测试"),
                ]
            )
        ]
    )

    text = ocr_service._compose_text_from_result(result, language_tag="zh-CN")

    assert text == "沪 A 测试"


def test_compose_text_from_result_applies_english_letter_number_fix():
    result = ocr_service.OcrRecognition(
        lines=[
            ocr_service.OcrLine(text="he11o wor1d"),
        ]
    )

    text = ocr_service._compose_text_from_result(result, language_tag="en-US")

    assert text == "hello world"


def test_select_text_adapter_falls_back_to_default():
    adapter = ocr_service._select_text_adapter("fr-FR")

    assert adapter.name == "default"

