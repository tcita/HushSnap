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
        "ocr_recapture_tooltip": "Capture and OCR",
        "ocr_failed_title": "Failed",
        "ocr_failed_body": "OCR failed",
        "ocr_empty_title": "Empty",
        "ocr_empty_body": "No text found",
        "ocr_empty_popup_hint": "No text recognized.",
        "ocr_toggle_title": "OCR",
        "ocr_enabled_body": "Enabled",
        "ocr_disabled_body": "Disabled",
        "ocr_copied": "✓ Copied!",
        "ocr_char_count": "{count} chars",
        "ocr_editable_hint": "Text is editable",
        "ocr_status_done": "Recognition complete",
        "ocr_status_paste_hint": "Ctrl+V to paste",
        "ocr_pin_btn": "Pin",
        "ocr_unpin_btn": "Unpin",
        "close_btn": "Close",
        "ocr_edit_btn": "Edit",
        "ocr_update_btn": "Update",
        "ocr_cancel_btn": "Cancel",
        "back_to_image_btn": "Back to Image",
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

    def emit(self, *args):
        for handler in list(self._handlers):
            handler(*args)


class FakeTrayIcon:
    def __init__(self):
        self.messages = []

    def showMessage(self, title, body, icon, timeout):
        self.messages.append((title, body, icon, timeout))


def _build_controller(monkeypatch, qapp, tmp_path, service=None):
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
        load=False,
    )
    tray_icon = FakeTrayIcon()
    controller.tray_icon = tray_icon
    return controller, tray_icon



def test_ocr_finished_shows_popup_does_not_copy_to_clipboard(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = True
    controller._pending_target = controller.popup

    shown = {}

    def _show_text(text, pixmap=None, lines=None):
        shown["text"] = text
        shown["pixmap"] = pixmap

    controller.popup.show_text = _show_text
    qapp.clipboard().clear()

    controller.on_ocr_finished(
        OcrResponse(text="hello world", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )

    # Clipboard must NOT be touched by manual (popup) path — the popup has
    # its own copy button; auto-OCR is the only path that writes clipboard.
    assert qapp.clipboard().text() == ""
    assert shown["text"] == "hello world"
    assert shown["pixmap"] is sample_pixmap
    assert controller._pending_target is None  # consumed by on_ocr_finished


def test_on_ocr_finished_clears_expecting_result(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    qapp.clipboard().clear()

    controller._expecting_ocr_result = True
    controller._pending_target = controller.popup
    controller.on_ocr_finished(
        OcrResponse(text="test", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )

    assert controller._expecting_ocr_result is False


def test_on_ocr_finished_skips_when_no_target(monkeypatch, qapp, tmp_path, sample_pixmap):
    """If _pending_target is None (no start_request captured a popup),
    on_ocr_finished must be a no-op."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    qapp.clipboard().clear()

    controller._expecting_ocr_result = False
    controller._pending_target = None
    controller.on_ocr_finished(
        OcrResponse(text="should not appear", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )

    assert qapp.clipboard().text() == ""


def test_memory_trim_timer_behavior(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)

    # 1. Start OCR -> timer should stop
    controller._trim_timer.start(5000)
    assert controller._trim_timer.isActive()

    controller.start_request(sample_pixmap)
    assert not controller._trim_timer.isActive()

    # 2. Finish OCR -> timer should start
    controller.on_ocr_finished(
        OcrResponse(text="test", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )
    assert controller._trim_timer.isActive()
    assert controller._trim_timer.interval() == 0

    # 3. Verify trim_engine is called on timeout
    trimmed_engine = []
    monkeypatch.setattr("hushsnap.ocr.engine.trim_engine", lambda engine: trimmed_engine.append(engine))

    controller._trim_current_engine()
    assert trimmed_engine == ["ppocr"]



# ── Load vs. OCR collision tests ──────────────────────────────────────

def test_load_skipped_when_ocr_already_requested(monkeypatch, qapp, tmp_path):
    """If user already triggered OCR, skip load — the OCR path will
    initialize the engine on its own."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = True

    load_calls = []
    monkeypatch.setattr(
        "hushsnap.ocr.engine.load_engine",
        lambda engine: load_calls.append(engine),
    )

    controller._background_load()

    assert load_calls == []
    assert controller._expecting_ocr_result is True


def test_load_runs_when_no_ocr_pending(monkeypatch, qapp, tmp_path):
    """Load should initialize the engine and emit load_finished
    when no OCR request is in progress."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = False

    load_calls = []
    monkeypatch.setattr(
        "hushsnap.ocr.engine.load_engine",
        lambda engine: load_calls.append(engine),
    )

    # Replace threading.Thread so the load body runs synchronously
    class SyncThread:
        def __init__(self, target=None, daemon=False):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr("threading.Thread", SyncThread)

    load_received = []
    controller.bridge.load_finished.connect(
        lambda: load_received.append(True)
    )

    controller._background_load()

    # flush the event queue so the posted _LoadFinishedEvent is delivered
    qapp.processEvents()

    assert load_calls == [controller._current_engine]
    assert load_received == [True]


def test_post_load_trim_skipped_when_ocr_in_progress(monkeypatch, qapp, tmp_path):
    """_schedule_post_load_trim must not start the trim timer when
    an OCR request is active."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = True

    controller._trim_timer.stop()
    assert not controller._trim_timer.isActive()

    controller._schedule_post_load_trim()

    assert not controller._trim_timer.isActive()


def test_post_load_trim_starts_timer_when_idle(monkeypatch, qapp, tmp_path):
    """_schedule_post_load_trim should start the trim timer (interval=0)
    when no OCR is pending."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = False
    controller._expecting_ocr_result = False

    controller._trim_timer.stop()
    assert not controller._trim_timer.isActive()

    controller._schedule_post_load_trim()

    assert controller._trim_timer.isActive()
    assert controller._trim_timer.interval() == 0


def test_ocr_request_cancels_pending_trim(monkeypatch, qapp, tmp_path, sample_pixmap):
    """_start_request must stop the trim timer, cancelling any pending
    post-load or post-OCR trim."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = True

    # Simulate a pending trimming timer (post-load or post-OCR)
    controller._trim_timer.start(0)
    assert controller._trim_timer.isActive()

    controller.start_request(sample_pixmap)

    assert not controller._trim_timer.isActive()


def test_start_request_does_not_show_loading(monkeypatch, qapp, tmp_path, sample_pixmap):
    """start_request should NOT call show_loading — loading is shown
    on the thumbnail, not on the popup."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)

    loading_called = []
    def _show_loading(pixmap=None):
        loading_called.append(pixmap)

    controller.popup.show_loading = _show_loading
    controller.start_request(sample_pixmap)

    assert len(loading_called) == 0, "start_request must not call show_loading"


def test_load_finished_signal_triggers_trim(monkeypatch, qapp, tmp_path):
    """The load_finished Qt signal must be connected to
    _schedule_post_load_trim, which starts the trim timer when idle."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = False

    controller._trim_timer.stop()
    assert not controller._trim_timer.isActive()

    # Emit the signal directly — simulates load thread finishing
    controller.bridge.load_finished.emit()

    assert controller._trim_timer.isActive()
    assert controller._trim_timer.interval() == 0


def test_trim_current_engine_skips_when_ocr_active(monkeypatch, qapp, tmp_path):
    """_trim_current_engine must be a no-op when OCR is active,
    regardless of which path (post-load or post-OCR) triggered it."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = True

    trim_calls = []
    monkeypatch.setattr(
        "hushsnap.ocr.engine.trim_engine",
        lambda engine: trim_calls.append(engine),
    )

    controller._trim_current_engine()

    assert trim_calls == []  # trim was skipped


def test_set_popup_anchor_detaches_pinned_popup(monkeypatch, qapp, tmp_path):
    """If the active popup is pinned and contains content/is visible, set_popup_anchor
    (triggered by clicking a thumbnail) should detach it and create a new active popup instance."""
    monkeypatch.setattr(ocr_controller.OcrPopup, "show", lambda self: None)
    monkeypatch.setattr(ocr_controller.OcrPopup, "raise_", lambda self: None)
    monkeypatch.setattr(ocr_controller.OcrPopup, "activateWindow", lambda self: None)

    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)

    # Simulate first popup is pinned and visible
    controller.popup.set_pinned(True)
    monkeypatch.setattr(controller.popup, "isVisible", lambda: True)
    controller.popup.text_edit.setPlainText("Old Result")

    original_popup = controller.popup

    # Trigger anchor setting (simulates thumbnail click)
    controller.set_popup_anchor(100, 100)

    # The active popup should be a new instance now
    assert controller.popup is not original_popup
    assert original_popup in controller._pinned_popups
    assert original_popup.get_plain_text() == "Old Result"
    assert controller.popup.get_plain_text() == ""


def test_concurrency_correct_popup_updated(monkeypatch, qapp, tmp_path, sample_pixmap):
    """When two requests are in flight, each result goes to the popup that
    was active when its start_request was called.

    _pending_target is a single slot — the most recent start_request
    overwrites it.  This is consistent with OcrService seq-overwrite:
    in practice only the newest request's callback fires; an older
    result that somehow survives seq-overwrite is safely dropped when
    _pending_target was already consumed by the newer request."""
    monkeypatch.setattr(ocr_controller.OcrPopup, "show", lambda self: None)
    monkeypatch.setattr(ocr_controller.OcrPopup, "raise_", lambda self: None)
    monkeypatch.setattr(ocr_controller.OcrPopup, "activateWindow", lambda self: None)
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)

    # Request 1 — captures the current (only) popup as target.
    controller.start_request(sample_pixmap)
    popup1 = controller.popup
    assert controller._pending_target is popup1
    assert len(service.callbacks) == 1
    callback1 = service.callbacks[0]

    # Pin popup1, then simulate a second thumbnail click: set_popup_anchor
    # detaches popup1 and creates popup2, then start_request captures popup2.
    popup1.set_pinned(True)
    monkeypatch.setattr(popup1, "isVisible", lambda: True)
    controller.set_popup_anchor(100, 100)
    popup2 = controller.popup
    assert popup2 is not popup1
    controller.start_request(sample_pixmap)
    assert controller._pending_target is popup2  # overwritten by request 2
    assert len(service.callbacks) == 2
    callback2 = service.callbacks[1]

    # Mock show_text for both
    results = {}
    popup1.show_text = lambda text, **kwargs: results.update({"p1": text})
    popup2.show_text = lambda text, **kwargs: results.update({"p2": text})

    # Deliver Result 2 (newest) — _pending_target is popup2
    callback2(OcrResponse(text="Result 2", error="", pixmap=sample_pixmap, recognition=OcrRecognition()))
    qapp.processEvents()
    assert results["p2"] == "Result 2"
    assert "p1" not in results
    assert controller._pending_target is None  # consumed

    # Deliver Result 1 (stale) — _pending_target is None → safely dropped
    callback1(OcrResponse(text="Result 1", error="", pixmap=sample_pixmap, recognition=OcrRecognition()))
    qapp.processEvents()
    assert "p1" not in results  # still not delivered — dropped correctly


# ── Auto-OCR cache / reuse tests ──────────────────────────────────────

def test_clear_auto_ocr_cache(monkeypatch, qapp, tmp_path):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)

    controller._auto_ocr_cache = OcrResponse(
        text="cached", error="", pixmap=None, recognition=OcrRecognition(),
    )
    controller._auto_ocr_in_flight = True
    controller._pending_popup_pixmap = "stale"

    controller._clear_auto_ocr_cache()

    assert controller._auto_ocr_cache is None
    assert controller._auto_ocr_in_flight is False
    assert controller._pending_popup_pixmap is None


def test_auto_ocr_to_clipboard_sets_in_flight(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    assert controller._auto_ocr_in_flight is False

    controller.auto_ocr_to_clipboard(sample_pixmap)

    assert controller._auto_ocr_in_flight is True


def test_on_auto_ocr_done_caches_success(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._auto_ocr_in_flight = True

    response = OcrResponse(
        text="hello", error="", pixmap=sample_pixmap, recognition=OcrRecognition(),
    )
    controller._on_auto_ocr_done(response)

    assert controller._auto_ocr_in_flight is False
    assert controller._auto_ocr_cache is response


def test_on_auto_ocr_done_does_not_cache_empty(monkeypatch, qapp, tmp_path):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._auto_ocr_in_flight = True

    controller._on_auto_ocr_done(
        OcrResponse(text="", error="", pixmap=None, recognition=OcrRecognition()),
    )

    assert controller._auto_ocr_in_flight is False
    assert controller._auto_ocr_cache is None


def test_on_auto_ocr_done_does_not_cache_error(monkeypatch, qapp, tmp_path):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._auto_ocr_in_flight = True

    controller._on_auto_ocr_done(
        OcrResponse(text="some text", error="OCR failed", pixmap=None, recognition=OcrRecognition()),
    )

    assert controller._auto_ocr_in_flight is False
    assert controller._auto_ocr_cache is None


def test_start_request_cache_hit_skips_recognize_async(monkeypatch, qapp, tmp_path, sample_pixmap):
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    monkeypatch.setattr(
        "hushsnap.ocr_controller.get_auto_ocr_after_capture", lambda: True,
    )

    controller._auto_ocr_cache = OcrResponse(
        text="cached result", error="", pixmap=sample_pixmap, recognition=OcrRecognition(),
    )

    controller.start_request(sample_pixmap)

    # Must NOT have called recognize_async — cache hit should short-circuit.
    assert len(service.requests) == 0
    # Cache consumed.
    assert controller._auto_ocr_cache is None


def test_start_request_in_flight_wait_stashes_pixmap(monkeypatch, qapp, tmp_path, sample_pixmap):
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    monkeypatch.setattr(
        "hushsnap.ocr_controller.get_auto_ocr_after_capture", lambda: True,
    )

    controller._auto_ocr_in_flight = True
    assert controller._pending_popup_pixmap is None

    controller.start_request(sample_pixmap)

    # Must NOT have called recognize_async.
    assert len(service.requests) == 0
    # Pixmap stashed so _on_auto_ocr_done can deliver.
    assert controller._pending_popup_pixmap is sample_pixmap


def test_start_request_normal_when_auto_ocr_disabled(monkeypatch, qapp, tmp_path, sample_pixmap):
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    monkeypatch.setattr(
        "hushsnap.ocr_controller.get_auto_ocr_after_capture", lambda: False,
    )

    controller.start_request(sample_pixmap)

    assert len(service.requests) == 1


def test_start_request_normal_when_cache_empty_and_not_in_flight(monkeypatch, qapp, tmp_path, sample_pixmap):
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    monkeypatch.setattr(
        "hushsnap.ocr_controller.get_auto_ocr_after_capture", lambda: True,
    )
    # No cache, not in flight → should fall through to normal OCR.
    assert controller._auto_ocr_cache is None
    assert controller._auto_ocr_in_flight is False

    controller.start_request(sample_pixmap)

    assert len(service.requests) == 1


def test_on_auto_ocr_done_delivers_to_pending_popup(monkeypatch, qapp, tmp_path, sample_pixmap):
    """When a start_request waited for in-flight auto-OCR, _on_auto_ocr_done
    must redirect the result to the popup via the cache-hit path in
    start_request."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    monkeypatch.setattr(
        "hushsnap.ocr_controller.get_auto_ocr_after_capture", lambda: True,
    )

    controller._auto_ocr_in_flight = True
    controller._pending_popup_pixmap = sample_pixmap

    response = OcrResponse(
        text="auto result", error="", pixmap=sample_pixmap, recognition=OcrRecognition(),
    )

    # Capture what start_request does by watching the bridge.
    events_received = []
    controller.bridge.ocr_result.connect(lambda resp: events_received.append(resp))

    controller._on_auto_ocr_done(response)

    # In-flight flag cleared.
    assert controller._auto_ocr_in_flight is False
    # Cache was set then consumed by inner start_request (cache-hit clears it).
    assert controller._auto_ocr_cache is None
    # Pending pixmap consumed.
    assert controller._pending_popup_pixmap is None

    # start_request was called, hit the cache, and posted an OcrResultEvent.
    qapp.processEvents()
    assert len(events_received) == 1
    assert events_received[0] is response


def test_start_request_cache_hit_has_priority_over_in_flight(monkeypatch, qapp, tmp_path, sample_pixmap):
    """Cache hit is checked before in-flight — if both were somehow true,
    cache wins."""
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    monkeypatch.setattr(
        "hushsnap.ocr_controller.get_auto_ocr_after_capture", lambda: True,
    )

    cached = OcrResponse(
        text="cached", error="", pixmap=sample_pixmap, recognition=OcrRecognition(),
    )
    controller._auto_ocr_cache = cached
    controller._auto_ocr_in_flight = True  # stale — cache was set by prior auto-OCR
    controller._pending_popup_pixmap = None

    controller.start_request(sample_pixmap)

    # Cache hit path: recognize_async NOT called, cache consumed.
    assert len(service.requests) == 0
    assert controller._auto_ocr_cache is None


def test_pending_popup_overwritten_by_second_click(monkeypatch, qapp, tmp_path, sample_pixmap):
    """A second thumbnail click while waiting for auto-OCR overwrites the
    stashed pixmap — only the last clicker gets the result."""
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    monkeypatch.setattr(
        "hushsnap.ocr_controller.get_auto_ocr_after_capture", lambda: True,
    )
    controller._auto_ocr_in_flight = True

    pixmap2 = QtGui.QPixmap(64, 64)
    pixmap2.fill(QtCore.Qt.GlobalColor.black)

    controller.start_request(sample_pixmap)
    assert controller._pending_popup_pixmap is sample_pixmap

    controller.start_request(pixmap2)
    # Overwritten.
    assert controller._pending_popup_pixmap is pixmap2
    assert len(service.requests) == 0


