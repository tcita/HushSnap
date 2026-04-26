import threading

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap import ocr
from hushsnap.ocr import ocr_service as ocr_service_module


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
        ocr_service_module,
        "recognize_result_from_pixmap",
        lambda *args, **kwargs: ocr.OcrRecognition(text=" hello world "),
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

    monkeypatch.setattr(ocr_service_module, "recognize_result_from_pixmap", _raise)

    service = ocr.OcrService()
    response = service.recognize(ocr.OcrRequest(pixmap=sample_pixmap))

    assert response.text == ""
    assert "boom" in response.error
    assert response.recognition is None


def test_ocr_service_async_callback(monkeypatch, sample_pixmap):
    monkeypatch.setattr(
        ocr_service_module,
        "recognize_result_from_pixmap",
        lambda *args, **kwargs: ocr.OcrRecognition(text="async"),
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


def test_ocr_service_forwards_preprocess_settings(monkeypatch, sample_pixmap):
    captured = {}

    def _recognize(pixmap, language_tag="", debug_dir=None, preprocess_settings=None):
        captured["pixmap"] = pixmap
        captured["language_tag"] = language_tag
        captured["debug_dir"] = debug_dir
        captured["preprocess_settings"] = preprocess_settings
        return ocr.OcrRecognition(text="configured")

    monkeypatch.setattr(ocr_service_module, "recognize_result_from_pixmap", _recognize)

    settings = ocr.OcrPreprocessSettings(auto_scale=True, auto_invert=False)
    service = ocr.OcrService()
    response = service.recognize(
        ocr.OcrRequest(
            pixmap=sample_pixmap,
            language_tag="en-US",
            debug_dir="debug",
            preprocess_settings=settings,
        )
    )

    assert response.text == "configured"
    assert captured["pixmap"] is sample_pixmap
    assert captured["language_tag"] == "en-US"
    assert captured["debug_dir"] == "debug"
    assert captured["preprocess_settings"] == settings


def test_compose_text_from_result_keeps_chinese_tokens_intact():
    result = ocr.OcrRecognition(
        lines=[
            ocr.OcrLine(
                words=[
                    ocr.OcrWord(text="沪"),
                    ocr.OcrWord(text="A"),
                    ocr.OcrWord(text="测试"),
                ]
            )
        ]
    )

    text = ocr.compose_text_from_result(result, language_tag="zh-CN")

    assert text == "沪 A 测试"


def test_compose_text_from_result_no_longer_applies_letter_number_fix():
    # Heuristic maps are now empty, so text should remain as is.
    result = ocr.OcrRecognition(
        lines=[
            ocr.OcrLine(text="he11o wor1d"),
        ]
    )

    text = ocr.compose_text_from_result(result, language_tag="en-US")

    assert text == "he11o wor1d"


def test_select_text_adapter_falls_back_to_default():
    adapter = ocr.select_text_adapter("fr-FR")

    assert adapter.name == "default"

