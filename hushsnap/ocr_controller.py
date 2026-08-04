import logging

from PyQt6 import QtCore

from .config import (
    get_auto_copy_ocr_result,
    get_config_path,
)
from .constants import (
    OCR_ENGINE_PPOCR,
)
from .ocr import OcrRequest, OcrService

from .signal_bridge import SignalBridge
from .system.memory_utils import get_working_set_mb, fmt_memory
from .ui.ocr_popup import OcrPopup
from .ui.thumbnail import thumbnail_manager
from .ui.toast import show_toast


class _ToastBridge(QtCore.QObject):
    """Temporary signal bridge for pinned image OCR results."""
    done = QtCore.pyqtSignal(object)


class OcrController:
    """Coordinate OCR requests, results, popup interactions, and persisted settings."""

    def __init__(
        self,
        app,
        translate,
        config_path,
        user_data_dir,
        save_debug_image=False,
        popup=None,
        service=None,
        warmup=True,
    ):
        self.app = app
        self.translate = translate
        self.config_path = config_path
        self.user_data_dir = user_data_dir
        self.save_debug_image = save_debug_image
        self.popup = popup or OcrPopup(translate)
        self.service = service or OcrService()
        self.bridge = SignalBridge()
        self.tray_icon = None
        self.capture_requester = None
        self.needs_ocr = False
        self._expecting_ocr_result = False
        self._toast_bridge = None
        self._pinned_popups = []

        self._current_engine = OCR_ENGINE_PPOCR

        self.bridge.ocr_result.connect(self.on_ocr_finished)
        self.bridge.warmup_finished.connect(self._schedule_post_warmup_trim)
        
        # New popups always start unpinned to avoid "sticky" state confusion.
        self.popup.pin_toggled.connect(self._handle_pin_toggled)

        self._trim_timer = QtCore.QTimer()
        self._trim_timer.setSingleShot(True)
        self._trim_timer.timeout.connect(self._trim_current_engine)

        # Warm up background engine
        if warmup:
            logging.debug("[OcrController] Scheduling background warmup on event loop start...")
            QtCore.QTimer.singleShot(0, self._background_warmup)

    def set_capture_requester(self, capture_requester):
        """Set callback used to request screenshot captures on demand."""
        self.capture_requester = capture_requester

    def is_busy(self):
        """Return True if an OCR request is currently in progress."""
        return self._expecting_ocr_result or self._toast_bridge is not None

    def _detach_if_pinned(self):
        """If the active popup is pinned and contains content/is visible, move it to _pinned_popups
        and create a new active popup instance. This prevents a new OCR request from
        overwriting a user-pinned result."""
        self._clean_pinned_popups()
        # If the active popup is pinned and has content/is visible, detach it
        if self.popup.is_pinned() and (self.popup.isVisible() or self.popup.text_edit.toPlainText()):
            old_active = self.popup

            # Disconnect the old popup's pin signal so it no longer
            # triggers logic in the controller.
            old_active.pin_toggled.disconnect(self._handle_pin_toggled)

            # Create a new active popup.
            self.popup = OcrPopup(self.translate)
            self.popup.apply_font_size()
            self.popup.pin_toggled.connect(self._handle_pin_toggled)

            # Old popup gets deleted when closed
            old_active.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self._pinned_popups.append(old_active)

    def set_popup_anchor(self, x, y, width=None, height=None):
        """Set preferred screen position for the next OCR popup appearance."""
        self._detach_if_pinned()
        self.popup.set_anchor_pos(x, y, width, height)

    def copy_text_from_image(self, pixmap, toast_window, on_done=None):
        """Run OCR on *pixmap* and copy recognized text to clipboard.

        ``on_done`` is an optional no-arg callback invoked after the result is
        handled (text copied, toast shown).  The thumbnail's silent-OCR path
        uses it to dismiss the thumbnail once OCR completes; the pinned-image
        caller leaves it None.
        """
        from PyQt6 import QtGui
        image = pixmap.toImage() if isinstance(pixmap, QtGui.QPixmap) else pixmap
        request = OcrRequest(pixmap=image, debug_dir=None)

        bridge = _ToastBridge()
        bridge.done.connect(
            lambda resp: self._on_toast_ocr_done(resp, toast_window, on_done),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self.service.recognize_async(request, bridge.done.emit)
        self._toast_bridge = bridge

    def _on_toast_ocr_done(self, response, toast_window, on_done=None):
        """Main-thread handler: copy OCR text and show global toast."""
        self._toast_bridge = None
        self._trim_timer.start(30000)
        text = response.text or ""
            
        if text:
            clipboard = self.app.clipboard()
            if clipboard:
                clipboard.setText(text)
        
        try:
            visible = toast_window.isVisible()
        except RuntimeError:
            visible = False

        if visible:
            if text:
                show_toast(self.translate("pin_ocr_copied"))
            else:
                show_toast(self.translate("pin_ocr_empty"), is_error=True)

        if on_done:
            on_done()

    def schedule_ocr(self):
        # Internal/debug-only hook: arms the "auto-OCR on next capture" flag.
        # The normal capture flow does NOT call this — after a screenshot the
        # user triggers OCR explicitly by clicking the thumbnail. This entry
        # point exists for the debug interface (hushsnap.system.debug_interface)
        # and tests so they can drive the auto-OCR path without going through
        # the UI. It is intentionally not wired into production capture.
        # Safety: setting needs_ocr=True alone does nothing harmful — it only
        # causes handle_capture_completed() to run OCR on the *next* captured
        # pixmap instead of ignoring it. No network, no persistence, local only.
        # If this ever gets promoted to a real user-facing "auto-OCR" setting,
        # gate it behind a config flag and re-audit the warmup/trim interactions.
        self.needs_ocr = True

    def _trim_current_engine(self):
        if self.needs_ocr or self._expecting_ocr_result:
            return
        from .ocr.engine import trim_engine
        try:
            trim_engine(self._current_engine)
        except Exception:
            logging.getLogger(__name__).exception("Trim failed")

    def _handle_pin_toggled(self, pinned):
        # Pinning is now purely visual — the popup becomes an independent
        # text box that stays where the user placed it.  No persistence,
        # no auto-avoidance.  The handler exists as a future hook point.
        pass

    def _clean_pinned_popups(self):
        cleaned = []
        for p in self._pinned_popups:
            try:
                # Accessing isVisible raises RuntimeError if C++ object is deleted
                p.isVisible()
                cleaned.append(p)
            except RuntimeError:
                pass
        self._pinned_popups = cleaned

    def _remove_pinned_popup(self, popup):
        if popup in self._pinned_popups:
            self._pinned_popups.remove(popup)

    def handle_capture_completed(self, captured_pixmap):
        if not self.needs_ocr:
            return

        self.needs_ocr = False
        self._detach_if_pinned()

        self.popup.clear_anchor()
        self.popup.show_loading(pixmap=captured_pixmap)
        self.start_request(captured_pixmap.copy())

    def apply_font_sizes(self):
        """Apply font size settings to both the active popup and all active pinned popups."""
        self._clean_pinned_popups()
        if self.popup:
            self.popup.apply_font_size()
        for p in self._pinned_popups:
            try:
                p.apply_font_size()
            except RuntimeError:
                pass

    def has_visible_popups(self) -> bool:
        """Return True if the active popup or any pinned popup is visible."""
        self._clean_pinned_popups()
        if self.popup and self.popup.isVisible():
            return True
        for p in self._pinned_popups:
            try:
                if p.isVisible():
                    return True
            except RuntimeError:
                pass
        return False

    def on_ocr_finished(self, response, target_popup=None):
        self._trim_timer.start(30000)
        logging.info(
            "[OCR_CHAIN] on_ocr_finished entered, text_len=%d, has_error=%s, target_is_active=%s",
            len(response.text or ""),
            bool(response.error),
            target_popup is None or target_popup is self.popup,
        )
        logging.debug("[on_ocr_finished] engine=%s, text_len=%d",
                      response.recognition.engine_type if response.recognition else "unknown",
                      len(response.text or ""))

        # If this is a stale result for a request we no longer track as 'busy'
        # (e.g. multiple requests in flight), we still allow it to show in its 
        # dedicated popup.
        if not self._expecting_ocr_result and target_popup is None:
            return

        # Only reset the global flag if this result belongs to the *current* active popup
        # or if we don't have a specific target (legacy/fallback).
        if target_popup is None or target_popup is self.popup:
            self._expecting_ocr_result = False
            thumbnail_manager.dismiss_current()

        target = target_popup or self.popup
        text = response.text
        error = response.error
        pixmap = response.pixmap

        # Use-after-free guard: ``target`` was captured in start_request at
        # request-dispatch time (``target = self.popup``). If the active popup
        # was since retired — _detach_if_pinned retires a pinned popup and
        # marks it WA_DeleteOnClose, after which Qt frees the C++ OcrPopup
        # once it closes — ``target`` may now point to a Python wrapper whose
        # underlying C++ object is gone. Any attribute access on such a
        # wrapper raises ``RuntimeError: wrapped C/C++ object of type OcrPopup
        # has been deleted``; a *native* call through the dangling pointer
        # (the 0x9fa04 / mov rax,[rcx] vtable read) crashes the process
        # instead. Probe with a lightweight attribute access before any
        # show_text() path touches the object; on RuntimeError, log and drop
        # the result rather than dereference freed memory.
        try:
            _ = target.isVisible()
        except RuntimeError:
            logging.warning(
                "[OCR_CHAIN] on_ocr_finished: target popup already deleted "
                "(use-after-free guard) — dropping result, target=%r",
                target_popup,
            )
            return

        if error:
            logging.error(f"OCR Error: {error}")
            logging.debug("[OCR_CHAIN] on_ocr_finished calling show_text (error)")
            target.show_text(
                f"{self.translate('ocr_failed_title')}\n{self.translate('ocr_failed_body')}",
                pixmap=pixmap,
            )
            return

        recognized = text or ""
        if not recognized:
            logging.debug("OCR result is empty.")
            logging.debug("[OCR_CHAIN] on_ocr_finished calling show_text (empty)")
            target.show_text(self.translate("ocr_empty_popup_hint"), pixmap=pixmap)
            return

        if get_auto_copy_ocr_result(self.config_path):
            clipboard = self.app.clipboard()
            if clipboard:
                clipboard.setText(recognized)

        logging.debug("[OCR_CHAIN] on_ocr_finished calling show_text (result)")
        target.show_text(
            recognized,
            pixmap=pixmap,
            lines=response.recognition.lines if response.recognition else None,
        )
        
    def start_request(self, pixmap):
        self._trim_timer.stop()
        self._expecting_ocr_result = True
        logging.debug("[OCR_CHAIN] start_request")
        debug_dir = self.user_data_dir if self.save_debug_image else None

        # Capture current popup instance to ensure result is delivered correctly
        # even if self.popup changes before the request finishes.
        target = self.popup

        from PyQt6 import QtGui
        image = pixmap.toImage() if isinstance(pixmap, QtGui.QPixmap) else pixmap
        request = OcrRequest(pixmap=image, engine=OCR_ENGINE_PPOCR, debug_dir=debug_dir)
        self.service.recognize_async(request, lambda resp: self.bridge.ocr_result.emit(resp, target))
        logging.debug("[OCR_CHAIN] start_request dispatched")

    def _background_warmup(self):
        import threading
        from .ocr.engine import warmup_engine
        if self.needs_ocr or self._expecting_ocr_result:
            self.bridge.warmup_finished.emit()
            return

        def run_warmup():
            try:
                warmup_engine(self._current_engine)
            except Exception:
                logging.error("Warmup failed", exc_info=True)
            finally:
                self.bridge.warmup_finished.emit()

        threading.Thread(target=run_warmup, daemon=True).start()

    def _schedule_post_warmup_trim(self):
        if self.needs_ocr or self._expecting_ocr_result:
            return
        self._trim_timer.start(0)
