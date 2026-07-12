import threading
import time

import numpy as np
import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap.constants import OCR_ENGINE_PPOCR
from hushsnap.ocr.ppocr import (
    _apply_cjk_spacing,
    _get_engine,
    _normalize_blocks,
    compose_ppocr_structures,
    compose_ppocr_text,
    is_cjk_or_fullwidth,
    ppocr_box_to_bbox,
    recognize_ppocr_qimage,
    recognize_ppocr_result_from_pixmap,
    word_separator,
)
from hushsnap.ocr.models import OcrLine, OcrBox


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


# ── ppocr_box_to_bbox ──────────────────────────────────────────────

def test_ppocr_box_to_bbox_four_points():
    box = [[10, 20], [100, 20], [100, 60], [10, 60]]
    assert ppocr_box_to_bbox(box) == (10.0, 20.0, 100.0, 60.0)


def test_ppocr_box_to_bbox_empty_list():
    assert ppocr_box_to_bbox([]) == (0.0, 0.0, 0.0, 0.0)


def test_ppocr_box_to_bbox_not_a_list():
    assert ppocr_box_to_bbox(None) == (0.0, 0.0, 0.0, 0.0)
    assert ppocr_box_to_bbox("string") == (0.0, 0.0, 0.0, 0.0)
    assert ppocr_box_to_bbox({}) == (0.0, 0.0, 0.0, 0.0)


def test_ppocr_box_to_bbox_malformed_points():
    assert ppocr_box_to_bbox([[]]) == (0.0, 0.0, 0.0, 0.0)
    assert ppocr_box_to_bbox([["x"]]) == (0.0, 0.0, 0.0, 0.0)
    assert ppocr_box_to_bbox([[1]]) == (0.0, 0.0, 0.0, 0.0)


def test_ppocr_box_to_bbox_single_point():
    assert ppocr_box_to_bbox([[5, 7]]) == (5.0, 7.0, 5.0, 7.0)


def test_ppocr_box_to_bbox_tuple_points():
    box = [(0, 0), (50, 0), (50, 30), (0, 30)]
    assert ppocr_box_to_bbox(box) == (0.0, 0.0, 50.0, 30.0)


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


# ── compose_ppocr_text ─────────────────────────────────────────────

def _make_block(text, box):
    return {"text": text, "box": box}


def test_compose_ppocr_text_single_block():
    blocks = [_make_block("hello", [[0, 0], [50, 0], [50, 20], [0, 20]])]
    assert compose_ppocr_text(blocks) == "hello"


def test_compose_ppocr_text_empty():
    assert compose_ppocr_text([]) == ""
    assert compose_ppocr_text(None) == ""


def test_compose_ppocr_text_empty_string_blocks():
    blocks = [
        _make_block("", [[0, 0], [10, 0], [10, 10], [0, 10]]),
        _make_block("   ", [[10, 0], [20, 0], [20, 10], [10, 10]]),
    ]
    assert compose_ppocr_text(blocks) == ""


def test_compose_ppocr_text_same_line():
    blocks = [
        _make_block("hello", [[0, 0], [50, 0], [50, 20], [0, 20]]),
        _make_block("world", [[60, 0], [110, 0], [110, 20], [60, 20]]),
    ]
    assert compose_ppocr_text(blocks) == "hello world"


def test_compose_ppocr_text_two_lines():
    blocks = [
        _make_block("line1", [[0, 0], [50, 0], [50, 20], [0, 20]]),
        _make_block("line2", [[0, 100], [50, 100], [50, 120], [0, 120]]),
    ]
    # Far apart y → separate lines, one \n per line
    assert compose_ppocr_text(blocks) == "line1\nline2"


def test_compose_ppocr_text_cjk_no_spaces():
    blocks = [
        _make_block("你好", [[0, 0], [40, 0], [40, 20], [0, 20]]),
        _make_block("世界", [[50, 0], [90, 0], [90, 20], [50, 20]]),
    ]
    assert compose_ppocr_text(blocks) == "你好世界"


def test_compose_ppocr_text_sorts_by_position():
    blocks = [
        _make_block("second", [[60, 0], [110, 0], [110, 20], [60, 20]]),
        _make_block("first", [[0, 0], [50, 0], [50, 20], [0, 20]]),
    ]
    assert compose_ppocr_text(blocks).startswith("first")


def test_compose_ppocr_text_large_gap_adds_space():
    blocks = [
        _make_block("left", [[0, 0], [30, 0], [30, 20], [0, 20]]),
        _make_block("right", [[80, 0], [130, 0], [130, 20], [80, 20]]),
    ]
    text = compose_ppocr_text(blocks)
    assert "left" in text
    assert "right" in text


def test_compose_ppocr_text_none_text():
    blocks = [
        {"text": None, "box": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        _make_block("ok", [[20, 0], [50, 0], [50, 20], [20, 20]]),
    ]
    assert compose_ppocr_text(blocks) == "ok"


def test_compose_ppocr_text_no_box():
    blocks = [{"text": "hello"}]
    assert compose_ppocr_text(blocks) == "hello"


def test_compose_ppocr_text_missing_text_key():
    blocks = [{"box": [[0, 0], [10, 0], [10, 10], [0, 10]]}]
    assert compose_ppocr_text(blocks) == ""


# ── engine singleton ──────────────────────────────────────────────────

def test_engine_singleton_returns_same_instance(monkeypatch):
    import hushsnap.ocr.ppocr as ppocr_module

    # Drain any in-progress daemon warmup thread from previous tests.
    # _get_engine() blocks on _engine_lock until the thread finishes,
    # so after this call no background thread will race with our
    # monkeypatch below.
    _get_engine()

    monkeypatch.setattr(ppocr_module, "_engine", None)
    fake = object()
    monkeypatch.setattr(ppocr_module, "PPOCR", lambda **kwargs: fake)

    e1 = _get_engine()
    e2 = _get_engine()
    assert e1 is e2 is fake


def test_release_engine_waits_while_request_is_loading_engine(monkeypatch, qapp):
    import hushsnap.ocr.ppocr as ppocr_module

    fake_engine = _FakeRapidOCREngine([
        ([[0, 0], [50, 0], [50, 20], [0, 20]], "hello", 0.98),
    ])
    entered_get_engine = threading.Event()
    allow_get_engine = threading.Event()
    release_done = threading.Event()
    result_holder = {}

    def _blocking_get_engine():
        entered_get_engine.set()
        assert allow_get_engine.wait(timeout=2)
        return fake_engine

    monkeypatch.setattr(ppocr_module, "_engine", fake_engine)
    monkeypatch.setattr(ppocr_module, "_active_requests", 0)
    monkeypatch.setattr(ppocr_module, "_get_engine", _blocking_get_engine)
    monkeypatch.setattr(ppocr_module, "_trim_working_set", lambda: None)

    img = QtGui.QImage(100, 100, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.white)

    def _recognize():
        result_holder["result"] = recognize_ppocr_qimage(img)

    def _release():
        ppocr_module.release_engine()
        release_done.set()

    recognize_thread = threading.Thread(target=_recognize)
    recognize_thread.start()
    assert entered_get_engine.wait(timeout=2)

    release_thread = threading.Thread(target=_release)
    release_thread.start()
    time.sleep(0.05)

    assert not release_done.is_set()

    allow_get_engine.set()
    recognize_thread.join(timeout=2)
    release_thread.join(timeout=2)

    assert not recognize_thread.is_alive()
    assert not release_thread.is_alive()
    assert release_done.is_set()
    assert result_holder["result"].text == "hello"
    assert ppocr_module._active_requests == 0


# ── recognize_ppocr_qimage ─────────────────────────────────────────

class _FakeRapidOCREngine:
    """Simulates the RapidOCR engine — callable, returns RapidOCROutput-like result."""

    def __init__(self, items=None):
        self._items = items or []
        self.use_det = True
        self.use_cls = True

    def __call__(self, *args, **kwargs):
        return self

    def to_json(self):
        if not self._items:
            return None
        return [{"box": b, "txt": t, "score": s} for b, t, s in self._items]


def test_recognize_ppocr_qimage_blank_image(monkeypatch, qapp):
    import hushsnap.ocr.ppocr as ppocr_module

    fake_engine = _FakeRapidOCREngine([])
    monkeypatch.setattr(ppocr_module, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(ppocr_module, "_engine", fake_engine)

    img = QtGui.QImage(100, 100, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.white)

    result = recognize_ppocr_qimage(img)
    assert result.engine_type == OCR_ENGINE_PPOCR
    assert result.text == ""


def test_recognize_ppocr_qimage_with_text(monkeypatch, qapp):
    import hushsnap.ocr.ppocr as ppocr_module

    fake_engine = _FakeRapidOCREngine([
        ([[0, 0], [50, 0], [50, 20], [0, 20]], "hello", 0.98),
    ])
    monkeypatch.setattr(ppocr_module, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(ppocr_module, "_engine", fake_engine)

    img = QtGui.QImage(100, 100, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.white)

    result = recognize_ppocr_qimage(img)
    assert result.engine_type == OCR_ENGINE_PPOCR
    assert "hello" in result.text


def test_recognize_ppocr_qimage_save_failure(monkeypatch, qapp):
    """When image.save fails, returns empty OcrRecognition with engine type."""
    import hushsnap.ocr.ppocr as ppocr_module

    fake_engine = object()
    monkeypatch.setattr(ppocr_module, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(ppocr_module, "_engine", fake_engine)

    img = QtGui.QImage()  # null image — save returns False
    result = recognize_ppocr_qimage(img)
    assert result.engine_type == OCR_ENGINE_PPOCR
    assert result.text == ""


def test_recognize_ppocr_qimage_engine_exception(monkeypatch, qapp):
    import hushsnap.ocr.ppocr as ppocr_module

    def _boom(*args):
        raise RuntimeError("OCR engine crashed")

    fake = _FakeRapidOCREngine([])
    monkeypatch.setattr(fake, "__call__", _boom)
    monkeypatch.setattr(ppocr_module, "_get_engine", lambda: fake)
    monkeypatch.setattr(ppocr_module, "_engine", fake)

    img = QtGui.QImage(100, 100, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.white)

    result = recognize_ppocr_qimage(img)
    assert result.engine_type == OCR_ENGINE_PPOCR
    assert result.text == ""


# ── recognize_ppocr_result_from_pixmap ─────────────────────────────

def test_recognize_ppocr_result_from_pixmap_null(monkeypatch, qapp):
    import hushsnap.ocr.ppocr as ppocr_module

    fake_engine = object()
    monkeypatch.setattr(ppocr_module, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(ppocr_module, "_engine", fake_engine)

    result = recognize_ppocr_result_from_pixmap(QtGui.QPixmap())
    assert result.text == ""


def test_recognize_ppocr_result_from_pixmap_with_text(monkeypatch, qapp):
    import hushsnap.ocr.ppocr as ppocr_module

    fake_engine = _FakeRapidOCREngine([
        ([[0, 0], [60, 0], [60, 25], [0, 25]], "hello world", 0.99),
    ])
    monkeypatch.setattr(ppocr_module, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(ppocr_module, "_engine", fake_engine)

    image = QtGui.QImage(100, 100, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtCore.Qt.GlobalColor.white)

    result = recognize_ppocr_result_from_pixmap(image)
    assert "hello world" in result.text
    assert result.engine_type == OCR_ENGINE_PPOCR


# ── _normalize_blocks ───────────────────────────────────────────────

def test_normalize_blocks_filters_empty_and_whitespace():
    blocks = [
        {"text": "", "box": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        {"text": "   ", "box": [[10, 0], [20, 0], [20, 10], [10, 10]]},
        {"text": "hello", "box": [[20, 0], [50, 0], [50, 10], [20, 10]]},
    ]
    result = _normalize_blocks(blocks)
    assert len(result) == 1
    assert result[0]["text"] == "hello"


def test_normalize_blocks_none():
    assert _normalize_blocks(None) == []


def test_normalize_blocks_missing_box_gives_minimal_placement():
    blocks = [{"text": "text-no-box"}]
    result = _normalize_blocks(blocks)
    assert len(result) == 1
    assert result[0]["width"] == 1.0
    assert result[0]["height"] == 1.0


# ── _apply_cjk_spacing ──────────────────────────────────────────────

def test_cjk_spacing_inserts_space_between_cjk_and_latin():
    assert _apply_cjk_spacing("中文hello") == "中文 hello"
    assert _apply_cjk_spacing("hello中文") == "hello 中文"


def test_cjk_spacing_preserves_existing_spaces():
    assert _apply_cjk_spacing("中文 hello") == "中文 hello"
    assert _apply_cjk_spacing("中文  hello") == "中文  hello"  # won't dedupe


def test_cjk_spacing_with_numbers_and_symbols():
    assert _apply_cjk_spacing("测试123abc") == "测试 123abc"
    assert _apply_cjk_spacing("第1个") == "第 1 个"


def test_cjk_spacing_empty():
    assert _apply_cjk_spacing("") == ""
    assert _apply_cjk_spacing(None) is None


def test_cjk_spacing_pure_cjk():
    assert _apply_cjk_spacing("纯中文文本") == "纯中文文本"


def test_cjk_spacing_pure_latin():
    assert _apply_cjk_spacing("hello world") == "hello world"


def test_cjk_spacing_protects_url_with_cjk_query():
    """Regression: a URL whose query value is CJK must not be split.

    Before URL protection, ``kw=测试页面&fr=pb`` was rewritten to
    ``kw= 测试页面 &fr=pb`` (a space at every CJK↔Latin boundary), so the link
    highlighter — which stops at whitespace — only coloured the fragment up to
    the first inserted space.  The URL span is now exempt from the spacers.
    """
    assert _apply_cjk_spacing("https://tieba.baidu.com/f?kw=测试页面&fr=pb") == \
        "https://tieba.baidu.com/f?kw=测试页面&fr=pb"


def test_cjk_spacing_still_spaces_cjk_latin_outside_url():
    """Spacing still applies to CJK↔Latin runs that are not part of a URL."""
    assert _apply_cjk_spacing("访问 example.com 看看") == "访问 example.com 看看"
    # No URL scheme here, so the CJK↔Latin boundaries are spaced as usual.
    assert _apply_cjk_spacing("中文hello") == "中文 hello"


# ── compose_ppocr_structures (public API integration) ───────────────

def _block(text, box):
    return {"text": text, "box": box}


def test_compose_ppocr_structures_returns_lines():
    blocks = [_block("hello", [[0, 0], [50, 0], [50, 20], [0, 20]])]
    lines = compose_ppocr_structures(blocks)
    assert len(lines) == 1
    assert lines[0].text == "hello"
    assert len(lines[0].words) == 1
    assert lines[0].words[0].text == "hello"


def test_compose_ppocr_structures_empty():
    assert compose_ppocr_structures([]) == []
    assert compose_ppocr_structures(None) == []


def test_compose_ppocr_structures_cjk_spacing_applied():
    blocks = [
        _block("中文hello", [[0, 0], [70, 0], [70, 20], [0, 20]]),
    ]
    lines = compose_ppocr_structures(blocks)
    assert lines[0].text == "中文 hello"
