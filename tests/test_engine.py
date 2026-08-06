"""Tests for the OCR engine registry — register_engine, partial updates,
default engine selection, and error identification."""

import pytest

from hushsnap.ocr.engine import (
    _ENGINES,
    _DEFAULT_ENGINE,
    get_default_engine,
    get_recognize_fn,
    identify_engine_error,
    register_engine,
    registered_engines,
    release_engine,
    trim_engine,
    load_engine,
)


@pytest.fixture(autouse=True)
def _save_restore_registry():
    """Snapshot and restore the global engine registry around each test."""
    import hushsnap.ocr.engine as engine_module
    saved_engines = dict(engine_module._ENGINES)
    saved_default = engine_module._DEFAULT_ENGINE
    engine_module._ENGINES.clear()
    engine_module._DEFAULT_ENGINE = None
    yield
    engine_module._ENGINES.clear()
    engine_module._ENGINES.update(saved_engines)
    engine_module._DEFAULT_ENGINE = saved_default


def _dummy_recognize(img, language_tag=""):
    return None


def test_register_engine_sets_default_on_first_call():
    register_engine("test1", recognize=_dummy_recognize)
    assert get_default_engine() == "test1"


def test_register_engine_does_not_change_default_on_subsequent():
    register_engine("test1", recognize=_dummy_recognize)
    register_engine("test2", recognize=_dummy_recognize)
    assert get_default_engine() == "test1"


def test_register_engine_partial_update_preserves_fields():
    register_engine("test1", recognize=_dummy_recognize, metadata={"key": "val"})
    # Partial update: only change trim
    register_engine("test1", recognize=_dummy_recognize, trim=lambda: None)
    entry = _ENGINES["test1"]
    assert entry["metadata"] == {"key": "val"}  # preserved
    assert entry["trim"] is not None  # new field added
    assert entry["recognize"] is _dummy_recognize


def test_get_recognize_fn_returns_none_for_unknown():
    assert get_recognize_fn("nonexistent") is None


def test_release_engine_calls_hook():
    released = []
    register_engine("t", recognize=_dummy_recognize,
                    release=lambda: released.append(True))
    release_engine("t")
    assert released == [True]


def test_release_engine_noop_for_no_hook():
    register_engine("t", recognize=_dummy_recognize)
    release_engine("t")  # should not raise


def test_trim_engine_calls_hook():
    trimmed = []
    register_engine("t", recognize=_dummy_recognize,
                    trim=lambda: trimmed.append(True))
    trim_engine("t")
    assert trimmed == [True]


def test_load_engine_calls_hook():
    loaded = []
    register_engine("t", recognize=_dummy_recognize,
                    load=lambda: loaded.append(True))
    load_engine("t")
    assert loaded == [True]


def test_load_engine_noop_for_no_hook():
    register_engine("t", recognize=_dummy_recognize)
    load_engine("t")  # should not raise


def test_identify_engine_error_matches_prefix():
    register_engine("e1", recognize=_dummy_recognize,
                    metadata={"error_prefixes": ["e1_prefix_"]})
    assert identify_engine_error("E1_PREFIX_something went wrong") == "e1"


def test_identify_engine_error_no_match():
    register_engine("e1", recognize=_dummy_recognize,
                    metadata={"error_prefixes": ["E1_PREFIX_"]})
    assert identify_engine_error("some other error") is None


def test_identify_engine_error_case_insensitive():
    register_engine("e1", recognize=_dummy_recognize,
                    metadata={"error_prefixes": ["e1_prefix_"]})
    assert identify_engine_error("E1_PREFIX_error") == "e1"


def test_registered_engines_returns_metadata():
    register_engine("e1", recognize=_dummy_recognize,
                    metadata={"display_name": "Engine 1"})
    result = registered_engines()
    assert "e1" in result
    assert result["e1"]["display_name"] == "Engine 1"
