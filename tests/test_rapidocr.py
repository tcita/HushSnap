import numpy as np
import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap.constants import OCR_ENGINE_RAPID
from hushsnap.ocr.rapidocr import (
    _get_engine,
    compose_rapidocr_text,
    is_cjk_or_fullwidth,
    rapidocr_box_to_bbox,
    recognize_rapidocr_qimage,
    recognize_rapidocr_result_from_pixmap,
    word_separator,
)


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


# ── rapidocr_box_to_bbox ──────────────────────────────────────────────

def test_rapidocr_box_to_bbox_four_points():
    box = [[10, 20], [100, 20], [100, 60], [10, 60]]
    assert rapidocr_box_to_bbox(box) == (10.0, 20.0, 100.0, 60.0)


def test_rapidocr_box_to_bbox_empty_list():
    assert rapidocr_box_to_bbox([]) == (0.0, 0.0, 0.0, 0.0)


def test_rapidocr_box_to_bbox_not_a_list():
    assert rapidocr_box_to_bbox(None) == (0.0, 0.0, 0.0, 0.0)
    assert rapidocr_box_to_bbox("string") == (0.0, 0.0, 0.0, 0.0)
    assert rapidocr_box_to_bbox({}) == (0.0, 0.0, 0.0, 0.0)


def test_rapidocr_box_to_bbox_malformed_points():
    assert rapidocr_box_to_bbox([[]]) == (0.0, 0.0, 0.0, 0.0)
    assert rapidocr_box_to_bbox([["x"]]) == (0.0, 0.0, 0.0, 0.0)
    assert rapidocr_box_to_bbox([[1]]) == (0.0, 0.0, 0.0, 0.0)


def test_rapidocr_box_to_bbox_single_point():
    assert rapidocr_box_to_bbox([[5, 7]]) == (5.0, 7.0, 5.0, 7.0)


def test_rapidocr_box_to_bbox_tuple_points():
    box = [(0, 0), (50, 0), (50, 30), (0, 30)]
    assert rapidocr_box_to_bbox(box) == (0.0, 0.0, 50.0, 30.0)


# ── is_cjk_or_fullwidth ───────────────────────────────────────────────

def test_is_cjk_chinese():
    assert is_cjk_or_fullwidth("中") is True
    assert is_cjk_or_fullwidth("文") is True


def test_is_cjk_japanese_kana():
    assert is_cjk_or_fullwidth("あ") is True
    assert is_cjk_or_fullwidth("ア") is True


def test_is_cjk_korean():
    assert is_cjk_or_fullwidth("가") is True


def test_is_cjk_fullwidth_punctuation():
    assert is_cjk_or_fullwidth("、") is True
    assert is_cjk_or_fullwidth("，") is True


def test_is_cjk_latin():
    assert is_cjk_or_fullwidth("A") is False
    assert is_cjk_or_fullwidth("z") is False
    assert is_cjk_or_fullwidth("1") is False


def test_is_cjk_empty():
    assert is_cjk_or_fullwidth("") is False


# ── word_separator ────────────────────────────────────────────────────

def test_word_separator_cjk_adjacent():
    assert word_separator("中", "文") == ""


def test_word_separator_latin_adjacent():
    assert word_separator("hello", "world") == " "


def test_word_separator_cjk_latin_boundary():
    assert word_separator("中文", "hello") == " "
    assert word_separator("hello", "中文") == " "


def test_word_separator_hyphen():
    assert word_separator("pre-", "fix") == ""


def test_word_separator_punctuation():
    assert word_separator("hello", ".") == ""
    assert word_separator("hello", ",") == ""
    assert word_separator("hello", "!") == ""
    assert word_separator("中文", "。") == ""


def test_word_separator_empty():
    assert word_separator("", "x") == ""
    assert word_separator("x", "") == ""
    assert word_separator("", "") == ""


# ── compose_rapidocr_text ─────────────────────────────────────────────

def _make_block(text, box):
    return {"text": text, "box": box}


def test_compose_rapidocr_text_single_block():
    blocks = [_make_block("hello", [[0, 0], [50, 0], [50, 20], [0, 20]])]
    assert compose_rapidocr_text(blocks) == "hello"


def test_compose_rapidocr_text_empty():
    assert compose_rapidocr_text([]) == ""
    assert compose_rapidocr_text(None) == ""


def test_compose_rapidocr_text_empty_string_blocks():
    blocks = [
        _make_block("", [[0, 0], [10, 0], [10, 10], [0, 10]]),
        _make_block("   ", [[10, 0], [20, 0], [20, 10], [10, 10]]),
    ]
    assert compose_rapidocr_text(blocks) == ""


def test_compose_rapidocr_text_same_line():
    blocks = [
        _make_block("hello", [[0, 0], [50, 0], [50, 20], [0, 20]]),
        _make_block("world", [[60, 0], [110, 0], [110, 20], [60, 20]]),
    ]
    assert compose_rapidocr_text(blocks) == "hello world"


def test_compose_rapidocr_text_two_lines():
    blocks = [
        _make_block("line1", [[0, 0], [50, 0], [50, 20], [0, 20]]),
        _make_block("line2", [[0, 100], [50, 100], [50, 120], [0, 120]]),
    ]
    assert compose_rapidocr_text(blocks) == "line1\nline2"


def test_compose_rapidocr_text_cjk_no_spaces():
    blocks = [
        _make_block("你好", [[0, 0], [40, 0], [40, 20], [0, 20]]),
        _make_block("世界", [[50, 0], [90, 0], [90, 20], [50, 20]]),
    ]
    assert compose_rapidocr_text(blocks) == "你好世界"


def test_compose_rapidocr_text_sorts_by_position():
    blocks = [
        _make_block("second", [[60, 0], [110, 0], [110, 20], [60, 20]]),
        _make_block("first", [[0, 0], [50, 0], [50, 20], [0, 20]]),
    ]
    assert compose_rapidocr_text(blocks).startswith("first")


def test_compose_rapidocr_text_large_gap_adds_space():
    blocks = [
        _make_block("left", [[0, 0], [30, 0], [30, 20], [0, 20]]),
        _make_block("right", [[80, 0], [130, 0], [130, 20], [80, 20]]),
    ]
    text = compose_rapidocr_text(blocks)
    assert "left" in text
    assert "right" in text


def test_compose_rapidocr_text_none_text():
    blocks = [
        {"text": None, "box": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        _make_block("ok", [[20, 0], [50, 0], [50, 20], [20, 20]]),
    ]
    assert compose_rapidocr_text(blocks) == "ok"


def test_compose_rapidocr_text_no_box():
    blocks = [{"text": "hello"}]
    assert compose_rapidocr_text(blocks) == "hello"


def test_compose_rapidocr_text_missing_text_key():
    blocks = [{"box": [[0, 0], [10, 0], [10, 10], [0, 10]]}]
    assert compose_rapidocr_text(blocks) == ""


# ── engine singleton ──────────────────────────────────────────────────

def test_engine_singleton_returns_same_instance(monkeypatch):
    import hushsnap.ocr.rapidocr as rapidocr_module

    monkeypatch.setattr(rapidocr_module, "_engine", None)
    fake = object()
    monkeypatch.setattr(rapidocr_module, "RapidOCR", lambda: fake)

    e1 = _get_engine()
    e2 = _get_engine()
    assert e1 is e2 is fake


# ── recognize_rapidocr_qimage ─────────────────────────────────────────

class _FakeRapidOCREngine:
    """Simulates the RapidOCR engine — callable, returns RapidOCROutput-like result."""

    def __init__(self, items=None):
        self._items = items or []

    def __call__(self, *args, **kwargs):
        return self

    def to_json(self):
        if not self._items:
            return None
        return [{"box": b, "txt": t, "score": s} for b, t, s in self._items]


def test_recognize_rapidocr_qimage_blank_image(monkeypatch, qapp):
    import hushsnap.ocr.rapidocr as rapidocr_module

    fake_engine = _FakeRapidOCREngine([])
    monkeypatch.setattr(rapidocr_module, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(rapidocr_module, "_engine", fake_engine)

    img = QtGui.QImage(100, 100, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.white)

    result = recognize_rapidocr_qimage(img)
    assert result.engine_type == OCR_ENGINE_RAPID
    assert result.requested_language_supported is True
    assert result.text == ""


def test_recognize_rapidocr_qimage_with_text(monkeypatch, qapp):
    import hushsnap.ocr.rapidocr as rapidocr_module

    fake_engine = _FakeRapidOCREngine([
        ([[0, 0], [50, 0], [50, 20], [0, 20]], "hello", 0.98),
    ])
    monkeypatch.setattr(rapidocr_module, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(rapidocr_module, "_engine", fake_engine)

    img = QtGui.QImage(100, 100, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.white)

    result = recognize_rapidocr_qimage(img)
    assert result.engine_type == OCR_ENGINE_RAPID
    assert "hello" in result.text


def test_recognize_rapidocr_qimage_save_failure(monkeypatch, qapp):
    """When image.save fails, returns empty OcrRecognition with engine type."""
    import hushsnap.ocr.rapidocr as rapidocr_module

    fake_engine = object()
    monkeypatch.setattr(rapidocr_module, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(rapidocr_module, "_engine", fake_engine)

    img = QtGui.QImage()  # null image — save returns False
    result = recognize_rapidocr_qimage(img)
    assert result.engine_type == OCR_ENGINE_RAPID
    assert result.text == ""


def test_recognize_rapidocr_qimage_engine_exception(monkeypatch, qapp):
    import hushsnap.ocr.rapidocr as rapidocr_module

    def _boom(*args):
        raise RuntimeError("OCR engine crashed")

    fake = _FakeRapidOCREngine([])
    monkeypatch.setattr(fake, "__call__", _boom)
    monkeypatch.setattr(rapidocr_module, "_get_engine", lambda: fake)
    monkeypatch.setattr(rapidocr_module, "_engine", fake)

    img = QtGui.QImage(100, 100, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.white)

    result = recognize_rapidocr_qimage(img)
    assert result.engine_type == OCR_ENGINE_RAPID
    assert result.text == ""


# ── recognize_rapidocr_result_from_pixmap ─────────────────────────────

def test_recognize_rapidocr_result_from_pixmap_null(monkeypatch, qapp):
    import hushsnap.ocr.rapidocr as rapidocr_module

    fake_engine = object()
    monkeypatch.setattr(rapidocr_module, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(rapidocr_module, "_engine", fake_engine)

    result = recognize_rapidocr_result_from_pixmap(QtGui.QPixmap())
    assert result.text == ""


def test_recognize_rapidocr_result_from_pixmap_with_text(monkeypatch, qapp):
    import hushsnap.ocr.rapidocr as rapidocr_module
    from hushsnap.ocr.preprocess import OcrPreprocessResult, OcrPreprocessSettings

    fake_engine = _FakeRapidOCREngine([
        ([[0, 0], [60, 0], [60, 25], [0, 25]], "hello world", 0.99),
    ])
    monkeypatch.setattr(rapidocr_module, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(rapidocr_module, "_engine", fake_engine)

    # Mock dependencies at their source to handle local imports inside rapidocr.py
    monkeypatch.setattr(
        "hushsnap.ocr.recognition.estimate_auto_scale_factor",
        lambda *args, **kwargs: 1.0
    )

    def mock_minimal_pipeline(pixmap, **kwargs):
        return OcrPreprocessResult(
            image=pixmap.toImage(),
            settings=OcrPreprocessSettings(),
            resolved_scale_factor=1.0
        )

    monkeypatch.setattr(
        "hushsnap.ocr.preprocess.run_minimal_pipeline",
        mock_minimal_pipeline
    )

    pixmap = QtGui.QPixmap(100, 100)
    pixmap.fill(QtCore.Qt.GlobalColor.white)

    result = recognize_rapidocr_result_from_pixmap(pixmap)
    assert "hello world" in result.text
    assert result.engine_type == OCR_ENGINE_RAPID
