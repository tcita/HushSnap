import logging

from PyQt6 import QtCore

from .config import (
    get_config_path,
)
from .constants import (
    OCR_ENGINE_PPOCR,
)
from .ocr import OcrRequest, OcrService

from .signal_bridge import SignalBridge, _LoadFinishedEvent
from .system.memory_utils import get_working_set_mb, fmt_memory
from .ui.ocr_popup import OcrPopup
from .ui.thumbnail import thumbnail_manager
from .ui.toast import show_toast


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
        load=True,
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
        self._expecting_ocr_result = False
        self._pending_target = None   # popup that start_request captured
        self._pinned_popups = []

        self._current_engine = OCR_ENGINE_PPOCR

        self.bridge.ocr_result.connect(self.on_ocr_finished)
        self.bridge.auto_ocr_done.connect(self._on_auto_ocr_done)
        self.bridge.load_finished.connect(self._schedule_post_load_trim)
        
        # New popups always start unpinned to avoid "sticky" state confusion.
        self.popup.pin_toggled.connect(self._handle_pin_toggled)

        self._trim_timer = QtCore.QTimer()
        self._trim_timer.setSingleShot(True)
        self._trim_timer.timeout.connect(self._trim_current_engine)

        # Load engine in background (model init only, no inference)
        if load:
            logging.debug("[OcrController] Scheduling background load on event loop start...")
            QtCore.QTimer.singleShot(0, self._background_load)

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

    def auto_ocr_to_clipboard(self, pixmap):
        """Run OCR silently and copy recognized text to clipboard.

        Fire-and-forget: the result arrives as a toast.  No popup.

        Uses QCoreApplication.postEvent (thread-safe per Qt docs) to
        hand the response from the worker thread to the main thread,
        avoiding any cross-thread Qt signal emission.
        """
        from PyQt6 import QtGui
        from .signal_bridge import _OcrResultEvent

        image = pixmap.toImage() if isinstance(pixmap, QtGui.QPixmap) else pixmap
        request = OcrRequest(pixmap=image, debug_dir=None)

        self.service.recognize_async(
            request,
            lambda resp: QtCore.QCoreApplication.postEvent(
                self.bridge, _OcrResultEvent(resp, "toast"),
            ),
        )

    def _on_auto_ocr_done(self, response):
        """Main-thread handler: show click-to-copy toast near cursor.

        The screenshot stays on the clipboard untouched; the toast offers
        one-click copy of the recognized text when the user wants it.
        """
        self._trim_timer.start(0)
        text = response.text or ""
        if text:
            from .ui.toast import show_ocr_copy_toast
            show_ocr_copy_toast(
                text,
                label=self.translate("ocr_copy_chip_label"),
                done_label=self.translate("ocr_copy_chip_copied"),
            )
        else:
            show_toast(self.translate("pin_ocr_empty"), is_error=True)

    def _trim_current_engine(self):
        if self._expecting_ocr_result:
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

    def on_ocr_finished(self, response):
        """Deliver OCR result to the popup that was active when start_request
        was called.  _pending_target is set on the main thread by
        start_request and read here on the main thread (via QueuedConnection),
        so there is no cross-thread QObject pointer in flight."""
        self._trim_timer.start(0)

        target = self._pending_target
        self._pending_target = None

        if target is None:
            return

        logging.info(
            "[OCR_CHAIN] on_ocr_finished entered, text_len=%d, has_error=%s",
            len(response.text or ""),
            bool(response.error),
        )
        logging.debug("[on_ocr_finished] engine=%s, text_len=%d",
                      response.recognition.engine_type if response.recognition else "unknown",
                      len(response.text or ""))

        # If the popup was rotated (user pinned it, then took another
        # screenshot), this result goes to the pinned popup — not the
        # current active one.  Only reset _expecting_ocr_result when
        # the target is still the active popup.
        _is_active_request = (target is self.popup)
        if _is_active_request:
            self._expecting_ocr_result = False
        elif not self._expecting_ocr_result:
            # Stale result for a retired popup and no request is pending.
            return

        text = response.text
        error = response.error
        pixmap = response.pixmap

        # Use-after-free guard: if the popup was retired (_detach_if_pinned
        # sets WA_DeleteOnClose) and the user closed it, the C++ QObject is
        # gone.  Probe before touching anything.
        try:
            _ = target.isVisible()
        except RuntimeError:
            logging.warning(
                "[OCR_CHAIN] on_ocr_finished: target popup already deleted "
                "(use-after-free guard) — dropping result",
            )
            return

        if error:
            logging.error(f"OCR Error: {error}")
            logging.debug("[OCR_CHAIN] on_ocr_finished calling show_text (error)")
            target.show_text(
                f"{self.translate('ocr_failed_title')}\n{self.translate('ocr_failed_body')}",
                pixmap=pixmap,
            )
        elif not (text or ""):
            logging.debug("OCR result is empty.")
            logging.debug("[OCR_CHAIN] on_ocr_finished calling show_text (empty)")
            target.show_text(self.translate("ocr_empty_popup_hint"), pixmap=pixmap)
        else:
            recognized = text or ""
            clipboard = self.app.clipboard()
            if clipboard:
                clipboard.setText(recognized)

            logging.debug("[OCR_CHAIN] on_ocr_finished calling show_text (result)")
            target.show_text(
                recognized,
                pixmap=pixmap,
                lines=response.recognition.lines if response.recognition else None,
            )

        # Dismiss thumbnail AFTER showing popup so there's no visible gap
        # between two top-level windows (DWM briefly exposes the desktop if
        # the thumbnail closes before the popup appears).
        if _is_active_request:
            thumbnail_manager.dismiss_current()
        
    def start_request(self, pixmap):
        self._trim_timer.stop()
        self._expecting_ocr_result = True
        self._pending_target = self.popup
        logging.debug("[OCR_CHAIN] start_request")

        from PyQt6 import QtGui
        from .signal_bridge import _OcrResultEvent

        debug_dir = self.user_data_dir if self.save_debug_image else None
        image = pixmap.toImage() if isinstance(pixmap, QtGui.QPixmap) else pixmap
        request = OcrRequest(pixmap=image, engine=OCR_ENGINE_PPOCR, debug_dir=debug_dir)
        self.service.recognize_async(
            request,
            lambda resp: QtCore.QCoreApplication.postEvent(
                self.bridge, _OcrResultEvent(resp, "popup"),
            ),
        )
        logging.debug("[OCR_CHAIN] start_request dispatched")

    def _background_load(self):
        import threading
        from .ocr.engine import load_engine
        if self._expecting_ocr_result:
            self.bridge.load_finished.emit()
            return

        def run_load():
            try:
                load_engine(self._current_engine)
            except Exception:
                logging.error("Load failed", exc_info=True)
            finally:
                QtCore.QCoreApplication.postEvent(
                    self.bridge, _LoadFinishedEvent(),
                )

        threading.Thread(target=run_load, daemon=True).start()

    def _schedule_post_load_trim(self):
        if self._expecting_ocr_result:
            return
        self._trim_timer.start(0)
