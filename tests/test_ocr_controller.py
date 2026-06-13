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
    )
    tray_icon = FakeTrayIcon()
    controller.tray_icon = tray_icon
    return controller, tray_icon


def test_capture_completed_starts_ocr_request(monkeypatch, qapp, tmp_path, sample_pixmap):
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    controller.schedule_ocr()

    controller.handle_capture_completed(sample_pixmap)

    assert len(service.requests) == 1
    assert service.requests[0].language_tag == ""
    assert service.requests[0].debug_dir == Path("data")


def test_ocr_finished_copies_text_and_updates_popup(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = True

    shown = {}

    def _show_text(text, pixmap=None, lines=None):
        shown["text"] = text
        shown["pixmap"] = pixmap

    controller.popup.show_text = _show_text
    qapp.clipboard().clear()

    controller.on_ocr_finished(
        OcrResponse(text="hello world", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )

    assert qapp.clipboard().text() == "hello world"
    assert shown["text"] == "hello world"
    assert shown["pixmap"] is sample_pixmap


def test_schedule_ocr_sets_needs_ocr(monkeypatch, qapp, tmp_path):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    assert controller.needs_ocr is False

    controller.schedule_ocr()
    assert controller.needs_ocr is True


def test_handle_capture_completed_skips_when_not_enabled(monkeypatch, qapp, tmp_path, sample_pixmap):
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)

    controller.handle_capture_completed(sample_pixmap)

    assert len(service.requests) == 0


def test_on_ocr_finished_clears_needs_ocr(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    qapp.clipboard().clear()

    controller._expecting_ocr_result = True
    controller.on_ocr_finished(
        OcrResponse(text="test", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )

    assert controller._expecting_ocr_result is False


def test_on_ocr_finished_skips_when_not_enabled(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    qapp.clipboard().clear()

    controller._expecting_ocr_result = False
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
    assert controller._trim_timer.interval() == 5000

    # 3. Verify trim_engine is called on timeout
    trimmed_engine = []
    monkeypatch.setattr("hushsnap.ocr.engine.trim_engine", lambda engine: trimmed_engine.append(engine))

    controller._trim_current_engine()
    assert trimmed_engine == ["ppocr"]



# ── Warmup vs. OCR collision tests ──────────────────────────────────────

def test_warmup_skipped_when_ocr_already_requested(monkeypatch, qapp, tmp_path):
    """If user already triggered OCR, skip warmup — the OCR path will
    initialize the engine on its own."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller.needs_ocr = True

    warmup_calls = []
    monkeypatch.setattr(
        "hushsnap.ocr.engine.warmup_engine",
        lambda engine: warmup_calls.append(engine),
    )

    controller._background_warmup()

    assert warmup_calls == []
    assert controller.needs_ocr is True


def test_warmup_runs_when_no_ocr_pending(monkeypatch, qapp, tmp_path):
    """Warmup should initialize the engine and emit warmup_finished
    when no OCR request is in progress."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller.needs_ocr = False
    controller._expecting_ocr_result = False

    warmup_calls = []
    monkeypatch.setattr(
        "hushsnap.ocr.engine.warmup_engine",
        lambda engine: warmup_calls.append(engine),
    )

    # Replace threading.Thread so the warmup body runs synchronously
    class SyncThread:
        def __init__(self, target=None, daemon=False):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr("threading.Thread", SyncThread)

    warmup_received = []
    controller.bridge.warmup_finished.connect(
        lambda: warmup_received.append(True)
    )

    controller._background_warmup()

    assert warmup_calls == [controller._current_engine]
    assert warmup_received == [True]


def test_post_warmup_trim_skipped_when_ocr_in_progress(monkeypatch, qapp, tmp_path):
    """_schedule_post_warmup_trim must not start the trim timer when
    an OCR request is active."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = True

    controller._trim_timer.stop()
    assert not controller._trim_timer.isActive()

    controller._schedule_post_warmup_trim()

    assert not controller._trim_timer.isActive()


def test_post_warmup_trim_starts_timer_when_idle(monkeypatch, qapp, tmp_path):
    """_schedule_post_warmup_trim should start the trim timer (interval=0)
    when no OCR is pending."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller.needs_ocr = False
    controller._expecting_ocr_result = False

    controller._trim_timer.stop()
    assert not controller._trim_timer.isActive()

    controller._schedule_post_warmup_trim()

    assert controller._trim_timer.isActive()
    assert controller._trim_timer.interval() == 0


def test_ocr_request_cancels_pending_trim(monkeypatch, qapp, tmp_path, sample_pixmap):
    """_start_request must stop the trim timer, cancelling any pending
    post-warmup or post-OCR trim."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = True

    # Simulate a pending trimming timer (post-warmup or post-OCR)
    controller._trim_timer.start(0)
    assert controller._trim_timer.isActive()

    controller.start_request(sample_pixmap)

    assert not controller._trim_timer.isActive()


def test_start_request_does_not_show_loading(monkeypatch, qapp, tmp_path, sample_pixmap):
    """start_request should NOT call show_loading — loading is now shown
    on the thumbnail (for the thumbnail-click path) or by handle_capture_completed
    (for the OCR-hotkey path)."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)

    loading_called = []
    def _show_loading(pixmap=None):
        loading_called.append(pixmap)

    controller.popup.show_loading = _show_loading
    controller.start_request(sample_pixmap)

    assert len(loading_called) == 0, "start_request must not call show_loading"


def test_handle_capture_completed_shows_loading(monkeypatch, qapp, tmp_path, sample_pixmap):
    """handle_capture_completed (OCR-hotkey path) should call show_loading
    since there is no thumbnail to show loading on."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)

    loading_pixmap = []
    def _show_loading(pixmap=None):
        loading_pixmap.append(pixmap)

    controller.popup.show_loading = _show_loading
    controller.needs_ocr = True
    controller.handle_capture_completed(sample_pixmap)

    assert len(loading_pixmap) == 1
    assert loading_pixmap[0] is sample_pixmap


def test_warmup_finished_signal_triggers_trim(monkeypatch, qapp, tmp_path):
    """The warmup_finished Qt signal must be connected to
    _schedule_post_warmup_trim, which starts the trim timer when idle."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller.needs_ocr = False
    controller._expecting_ocr_result = False

    controller._trim_timer.stop()
    assert not controller._trim_timer.isActive()

    # Emit the signal directly — simulates warmup thread finishing
    controller.bridge.warmup_finished.emit()

    assert controller._trim_timer.isActive()
    assert controller._trim_timer.interval() == 0


def test_trim_current_engine_skips_when_ocr_active(monkeypatch, qapp, tmp_path):
    """_trim_current_engine must be a no-op when OCR is active,
    regardless of which path (post-warmup or post-OCR) triggered it."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._expecting_ocr_result = True

    trim_calls = []
    monkeypatch.setattr(
        "hushsnap.ocr.engine.trim_engine",
        lambda engine: trim_calls.append(engine),
    )

    controller._trim_current_engine()

    assert trim_calls == []  # trim was skipped


def test_handle_capture_completed_detaches_pinned_popup(monkeypatch, qapp, tmp_path, sample_pixmap):
    """If the active popup is pinned and contains content/is visible, handle_capture_completed
    should detach it and create a new active popup."""
    # Globally mock show/raise/activateWindow for all popups created in this test
    monkeypatch.setattr(ocr_controller.OcrPopup, "show", lambda self: None)
    monkeypatch.setattr(ocr_controller.OcrPopup, "raise_", lambda self: None)
    monkeypatch.setattr(ocr_controller.OcrPopup, "activateWindow", lambda self: None)

    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)

    # Simulate first popup is pinned and visible
    controller.popup.set_pinned(True)
    monkeypatch.setattr(controller.popup, "isVisible", lambda: True)

    original_popup = controller.popup

    # Perform a capture completed
    controller.needs_ocr = True
    controller.handle_capture_completed(sample_pixmap)

    # The active popup should be a new instance now
    assert controller.popup is not original_popup

    # The old popup should be in _pinned_popups list
    assert original_popup in controller._pinned_popups

    # The old popup should have WA_DeleteOnClose set
    assert original_popup.testAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)

    # The old popup's pin signal should be disconnected from the controller
    assert original_popup.receivers(original_popup.pin_toggled) == 0

    # _clean_pinned_popups removes stale entries whose C++ objects are gone.
    # Simulate this by deleting the old popup, then calling clean.
    # Remove the instance-level isVisible monkeypatch first so the real Qt
    # method (which raises RuntimeError once the C++ object is gone) is used.
    del original_popup.isVisible
    original_popup.deleteLater()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    controller._clean_pinned_popups()
    assert original_popup not in controller._pinned_popups


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


def test_stagger_if_needed(monkeypatch, qapp, tmp_path):
    """If a new popup shows up exactly at the same position as an existing pinned popup, 
    it should be staggered."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    
    # 1. Setup a pinned popup at a fixed position
    pinned_popup = ocr_controller.OcrPopup(_translate)
    pinned_popup.move(100, 100)
    monkeypatch.setattr(pinned_popup, "isVisible", lambda: True)
    controller._pinned_popups.append(pinned_popup)
    
    # 2. Setup active popup at the same position
    controller.popup.move(100, 100)
    
    # 3. Trigger staggering
    controller._stagger_if_needed(controller.popup)
    
    # Position should have shifted by 28 pixels
    assert controller.popup.pos() == QtCore.QPoint(128, 128)


def test_concurrency_correct_popup_updated(monkeypatch, qapp, tmp_path, sample_pixmap):
    """If multiple requests are in flight, each result should go to the popup 
    that was active when the request started."""
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    
    # Request 1
    controller.schedule_ocr()
    popup1 = controller.popup
    controller.handle_capture_completed(sample_pixmap)
    assert len(service.callbacks) == 1
    callback1 = service.callbacks[0]
    
    # Request 2 (pins popup1 and creates popup2)
    popup1.set_pinned(True)
    monkeypatch.setattr(popup1, "isVisible", lambda: True)
    controller.schedule_ocr()
    controller.handle_capture_completed(sample_pixmap)
    popup2 = controller.popup
    assert popup2 is not popup1
    assert len(service.callbacks) == 2
    callback2 = service.callbacks[1]
    
    # Mock show_text for both
    results = {}
    popup1.show_text = lambda text, **kwargs: results.update({"p1": text})
    popup2.show_text = lambda text, **kwargs: results.update({"p2": text})
    
    # Deliver Result 2 FIRST (out of order)
    callback2(OcrResponse(text="Result 2", error="", pixmap=sample_pixmap, recognition=OcrRecognition()))
    assert results["p2"] == "Result 2"
    assert "p1" not in results
    
    # Deliver Result 1
    callback1(OcrResponse(text="Result 1", error="", pixmap=sample_pixmap, recognition=OcrRecognition()))
    assert results["p1"] == "Result 1"


