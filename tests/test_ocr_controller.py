from pathlib import Path

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap import ocr_controller
from hushsnap.constants import OCR_RAPID_IDLE_RELEASE_MS
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
        "ocr_engine_unavailable_title": "Engine unavailable",
        "ocr_engine_unavailable_body": "Windows OCR unavailable on this system",
        "ocr_lang_missing_title": "Missing language pack",
        "ocr_lang_missing_body": "{requested_lang} not installed; switch language or install it",
        "ocr_lang_missing_switch_btn": "Switch to {available_lang}",
        "ocr_lang_missing_open_settings_btn": "Open language settings",
        "ocr_lang_missing_cancel_btn": "Not now",
        "ocr_lang_installed_fallback": "Installed language",
        "ocr_open_settings_failed_title": "Cannot open settings",
        "ocr_open_settings_failed_body": "Cannot open settings",
        "ocr_lang_system_default": "system language",
        "ocr_lang_english": "English",
        "ocr_lang_chinese_simplified": "Simplified Chinese",
        "ocr_lang_chinese_traditional": "Traditional Chinese",
        "ocr_lang_selector_tooltip": "Select OCR language",
        "ocr_engine_windows": "WindowsOCR",
        "ocr_engine_rapid": "RapidOCR",
        "ocr_empty_title": "Empty",
        "ocr_empty_body": "No text found",
        "ocr_empty_popup_hint": "No text recognized. Try selecting a larger area or making sure the screenshot contains clear text.",
        "ocr_toggle_title": "OCR",
        "ocr_enabled_body": "Enabled",
        "ocr_disabled_body": "Disabled",
        "ocr_copied": "✓ Copied!",
        "ocr_char_count": "{count} chars",
        "ocr_editable_hint": "Text is editable",
        "ocr_recognizing": "Recognizing…",
        "ocr_status_done": "Recognition complete",
        "ocr_status_paste_hint": "Ctrl+V to paste",
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

    def emit(self, value):
        for handler in list(self._handlers):
            handler(value)


class FakeTrayIcon:
    def __init__(self):
        self.messages = []

    def showMessage(self, title, body, icon, timeout):
        self.messages.append((title, body, icon, timeout))


def _build_controller(monkeypatch, qapp, tmp_path, service=None):
    monkeypatch.setattr(ocr_controller, "get_ocr_lang", lambda state_path=None, config_path=None: "en-US")

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
    controller.enable_ocr_next_capture()

    controller.handle_capture_completed(sample_pixmap)

    assert len(service.requests) == 1
    assert service.requests[0].language_tag == "en-US"
    assert service.requests[0].debug_dir == Path("data")


def test_ocr_finished_copies_text_and_updates_popup(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller.enable_ocr_next_capture()

    shown = {}

    def _show_text(text, pixmap=None, lang=None, engine=None):
        shown["text"] = text
        shown["pixmap"] = pixmap
        shown["lang"] = lang
        shown["engine"] = engine

    controller.popup.show_text = _show_text
    qapp.clipboard().clear()

    controller.on_ocr_finished(
        OcrResponse(text=" hello world ", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )

    assert qapp.clipboard().text() == "hello world"
    assert shown["text"] == "hello world"
    assert shown["pixmap"] is sample_pixmap
    assert shown["lang"] == "en-US"


def test_ocr_finished_shows_notice_when_selected_language_is_not_installed(
    monkeypatch, qapp, tmp_path, sample_pixmap
):
    controller, tray_icon = _build_controller(monkeypatch, qapp, tmp_path)
    controller.enable_ocr_next_capture()
    tray_icon.messages.clear()
    shown = {}
    controller.popup.show_text = lambda *args, **kwargs: shown.update({"shown": True})

    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-CN",
        ),
    )

    controller.on_ocr_finished(response)
    controller.on_ocr_finished(response)

    assert tray_icon.messages == []
    assert shown["shown"] is True
    assert controller.popup.notice_frame.isHidden() is False
    assert "English" in controller.popup.notice_label.text()
    assert controller.popup.notice_switch_btn.property("target_lang") == "zh-CN"


def test_ocr_lang_changed_persists_and_reruns(monkeypatch, qapp, tmp_path, sample_pixmap):
    saved = {}
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    controller.enable_ocr_next_capture()
    monkeypatch.setattr(
        ocr_controller,
        "update_ocr_lang",
        lambda lang, state_path=None: saved.update({"lang": lang}),
    )
    controller.popup._last_pixmap = sample_pixmap

    controller.on_ocr_lang_changed("zh-CN")

    assert saved["lang"] == "zh-CN"
    assert len(service.requests) == 1
    assert service.requests[0].language_tag == "zh-CN"


def test_ocr_finished_warns_once_when_engine_is_unavailable(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, tray_icon = _build_controller(monkeypatch, qapp, tmp_path)
    controller.enable_ocr_next_capture()

    response = OcrResponse(
        text="",
        error="Windows OCR engine unavailable.",
        pixmap=sample_pixmap,
        recognition=None,
    )

    controller.on_ocr_finished(response)
    controller.on_ocr_finished(response)

    assert tray_icon.messages == []
    assert "windows" in controller._warned_engine_unavailable


def test_ocr_missing_language_switches_and_reruns(monkeypatch, qapp, tmp_path, sample_pixmap):
    saved = {}
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    controller.enable_ocr_next_capture()
    controller.popup.lang_combo.setCurrentIndex(controller.popup.lang_combo.findData("zh-CN"))
    monkeypatch.setattr(
        ocr_controller,
        "update_ocr_lang",
        lambda lang, state_path=None: saved.update({"lang": lang}),
    )
    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-TW",
        ),
    )

    controller.on_ocr_finished(response)
    controller.popup.notice_switch_btn.click()

    assert saved["lang"] == "zh-TW"
    assert len(service.requests) == 1
    assert service.requests[0].language_tag == "zh-TW"
    assert controller.popup.last_pixmap is sample_pixmap


def test_ocr_missing_language_can_open_settings(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller.enable_ocr_next_capture()
    controller.popup.lang_combo.setCurrentIndex(controller.popup.lang_combo.findData("zh-CN"))
    opened = {}
    monkeypatch.setattr(ocr_controller.os, "startfile", lambda uri: opened.update({"called": uri}))

    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-TW",
        ),
    )

    controller.on_ocr_finished(response)
    controller.popup.notice_settings_btn.click()

    assert opened["called"] == "ms-settings:regionlanguage"


def test_ocr_missing_language_switch_falls_back_to_other_combo_language(
    monkeypatch, qapp, tmp_path, sample_pixmap
):
    saved = {}
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)
    controller.enable_ocr_next_capture()
    monkeypatch.setattr(
        ocr_controller,
        "update_ocr_lang",
        lambda lang, state_path=None: saved.update({"lang": lang}),
    )
    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="",
        ),
    )

    controller.on_ocr_finished(response)
    controller.popup.notice_switch_btn.click()

    assert saved["lang"] == "zh-CN"
    assert len(service.requests) == 1
    assert service.requests[0].language_tag == "zh-CN"


def test_chinese_family_fallback_does_not_prompt_when_variant_is_available(
    monkeypatch, qapp, tmp_path, sample_pixmap
):
    controller, tray_icon = _build_controller(monkeypatch, qapp, tmp_path)
    controller.enable_ocr_next_capture()
    controller.popup.lang_combo.setCurrentIndex(controller.popup.lang_combo.findData("zh-TW"))

    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-TW",
        ),
    )

    controller.on_ocr_finished(response)

    assert controller.popup.notice_frame.isHidden() is True
    assert tray_icon.messages == []


def test_simplified_chinese_family_fallback_does_not_prompt_when_variant_is_available(
    monkeypatch, qapp, tmp_path, sample_pixmap
):
    controller, tray_icon = _build_controller(monkeypatch, qapp, tmp_path)
    controller.enable_ocr_next_capture()
    controller.popup.lang_combo.setCurrentIndex(controller.popup.lang_combo.findData("zh-CN"))

    response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-SG",
        ),
    )

    controller.on_ocr_finished(response)

    assert controller.popup.notice_frame.isHidden() is True
    assert tray_icon.messages == []


def test_notice_hides_after_compatible_response(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller.popup.lang_combo.setCurrentIndex(controller.popup.lang_combo.findData("zh-CN"))

    incompatible_response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=False,
            used_user_profile_fallback=True,
            engine_language_tag="zh-TW",
        ),
    )
    compatible_response = OcrResponse(
        text="hello",
        error="",
        pixmap=sample_pixmap,
        recognition=OcrRecognition(
            text="hello",
            requested_language_supported=True,
            used_user_profile_fallback=False,
            engine_language_tag="zh-CN",
        ),
    )

    controller._should_ocr = True
    controller.on_ocr_finished(incompatible_response)
    assert controller.popup.notice_frame.isHidden() is False

    controller._should_ocr = True
    controller.on_ocr_finished(compatible_response)
    assert controller.popup.notice_frame.isHidden() is True


def test_should_ocr_next_capture_sets_flag(monkeypatch, qapp, tmp_path):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    assert controller._should_ocr is False

    controller.enable_ocr_next_capture()
    assert controller._should_ocr is True


def test_recapture_button_requests_fresh_ocr_capture(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    requested = []
    hidden = []

    controller.set_capture_requester(lambda pixmap: requested.append(pixmap))
    controller.popup.hide = lambda: hidden.append(True)

    class FakeScreen:
        def devicePixelRatio(self):
            return 1.0

        def grabWindow(self, _window_id):
            return sample_pixmap

    monkeypatch.setattr(
        ocr_controller.QtWidgets.QApplication,
        "primaryScreen",
        staticmethod(lambda: FakeScreen()),
    )
    monkeypatch.setattr(
        ocr_controller.QtCore.QTimer,
        "singleShot",
        staticmethod(lambda _delay, callback: callback()),
    )

    controller.popup.recapture_btn.click()

    assert hidden == [True]
    assert controller._should_ocr is True
    assert requested == [sample_pixmap]


def test_handle_capture_completed_skips_when_not_enabled(monkeypatch, qapp, tmp_path, sample_pixmap):
    service = FakeService()
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path, service=service)

    controller.handle_capture_completed(sample_pixmap)

    assert len(service.requests) == 0


def test_on_ocr_finished_clears_should_ocr_flag(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    qapp.clipboard().clear()

    controller._should_ocr = True
    controller.on_ocr_finished(
        OcrResponse(text="test", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )

    assert controller._should_ocr is False


def test_on_ocr_finished_skips_when_not_enabled(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    qapp.clipboard().clear()

    controller._should_ocr = False
    controller.on_ocr_finished(
        OcrResponse(text="should not appear", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )

    assert qapp.clipboard().text() == ""


def test_memory_trim_timer_behavior(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    
    # 1. Start OCR -> timer should stop
    controller._trim_timer.start(5000)
    assert controller._trim_timer.isActive()
    
    controller._start_request(sample_pixmap, "en-US", "rapidocr")
    assert not controller._trim_timer.isActive()
    
    # 2. Finish OCR -> timer should start
    controller.on_ocr_finished(
        OcrResponse(text="test", error="", pixmap=sample_pixmap, recognition=OcrRecognition())
    )
    assert controller._trim_timer.isActive()
    assert controller._trim_timer.interval() == 30000
    
    # 3. Verify trim_engine is called on timeout
    trimmed_engine = []
    monkeypatch.setattr("hushsnap.ocr.engine.trim_engine", lambda engine: trimmed_engine.append(engine))
    
    controller._trim_current_engine()
    assert trimmed_engine == ["rapidocr"]


def test_rapidocr_idle_release_timer_behavior(monkeypatch, qapp, tmp_path, sample_pixmap):
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)

    controller._rapid_release_timer.start(5000)
    assert controller._rapid_release_timer.isActive()

    controller._start_request(sample_pixmap, "en-US", "rapidocr")
    assert not controller._rapid_release_timer.isActive()

    controller.on_ocr_finished(
        OcrResponse(
            text="test",
            error="",
            pixmap=sample_pixmap,
            recognition=OcrRecognition(engine_type="rapidocr"),
        )
    )
    assert controller._rapid_release_timer.isActive()
    assert controller._rapid_release_timer.interval() == OCR_RAPID_IDLE_RELEASE_MS

    released_engine = []
    monkeypatch.setattr(ocr_controller, "release_engine", lambda engine: released_engine.append(engine))

    controller._release_idle_rapidocr()
    assert released_engine == ["rapidocr"]


# ── Warmup vs. OCR collision tests ──────────────────────────────────────

def test_warmup_skipped_when_ocr_already_requested(monkeypatch, qapp, tmp_path):
    """If user already triggered OCR, skip warmup — the OCR path will
    initialize the engine via _get_engine() on its own."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._should_ocr = True

    warmup_calls = []
    monkeypatch.setattr(
        "hushsnap.ocr.engine.warmup_engine",
        lambda engine: warmup_calls.append(engine),
    )

    controller._background_warmup()

    assert warmup_calls == []
    assert controller._should_ocr is True  # flag unchanged


def test_warmup_runs_when_no_ocr_pending(monkeypatch, qapp, tmp_path):
    """Warmup should initialize the engine and emit warmup_finished
    when no OCR request is in progress."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._should_ocr = False

    warmup_calls = []
    monkeypatch.setattr(
        "hushsnap.ocr.engine.warmup_engine",
        lambda engine: warmup_calls.append(engine),
    )

    # Replace threading.Thread so the warmup body runs synchronously
    # inside _background_warmup — deterministic assertions.
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
    an OCR request is active (_should_ocr == True)."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._should_ocr = True

    controller._trim_timer.stop()
    assert not controller._trim_timer.isActive()

    controller._schedule_post_warmup_trim()

    assert not controller._trim_timer.isActive()


def test_post_warmup_trim_starts_timer_when_idle(monkeypatch, qapp, tmp_path):
    """_schedule_post_warmup_trim should start the trim timer (interval=0)
    when no OCR is pending."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._should_ocr = False

    controller._trim_timer.stop()
    assert not controller._trim_timer.isActive()

    controller._schedule_post_warmup_trim()

    assert controller._trim_timer.isActive()
    assert controller._trim_timer.interval() == 0


def test_ocr_request_cancels_pending_trim(monkeypatch, qapp, tmp_path, sample_pixmap):
    """_start_request must stop the trim timer, cancelling any pending
    post-warmup or post-OCR trim."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._should_ocr = True

    # Simulate a pending trimming timer (post-warmup or post-OCR)
    controller._trim_timer.start(0)
    assert controller._trim_timer.isActive()

    controller._start_request(sample_pixmap, "en-US", "rapidocr")

    assert not controller._trim_timer.isActive()


def test_warmup_finished_signal_triggers_trim(monkeypatch, qapp, tmp_path):
    """The warmup_finished Qt signal must be connected to
    _schedule_post_warmup_trim, which starts the trim timer when idle."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._should_ocr = False

    controller._trim_timer.stop()
    assert not controller._trim_timer.isActive()

    # Emit the signal directly — simulates warmup thread finishing
    controller.bridge.warmup_finished.emit()

    assert controller._trim_timer.isActive()
    assert controller._trim_timer.interval() == 0


def test_trim_current_engine_skips_when_ocr_active(monkeypatch, qapp, tmp_path):
    """_trim_current_engine must be a no-op when _should_ocr is True,
    regardless of which path (post-warmup or post-OCR) triggered it."""
    controller, _ = _build_controller(monkeypatch, qapp, tmp_path)
    controller._should_ocr = True

    trim_calls = []
    monkeypatch.setattr(
        "hushsnap.ocr.engine.trim_engine",
        lambda engine: trim_calls.append(engine),
    )

    controller._trim_current_engine()

    assert trim_calls == []  # trim was skipped
