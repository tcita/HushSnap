import logging
import threading
from pathlib import Path

from PyQt6 import QtGui

from .engine import get_default_engine, get_recognize_fn
from .models import OcrRequest, OcrResponse
from .preprocess import run_minimal_pipeline
from .text import compose_text_from_result

logger = logging.getLogger(__name__)


def _save_debug_preprocessed_image(image: QtGui.QImage, debug_dir: str | Path | None) -> None:
    """Best-effort debug image dump; failures are logged but non-fatal."""
    if not debug_dir:
        return
    try:
        debug_path = Path(debug_dir) / "ocr_debug_preprocessed.png"
        image.save(str(debug_path), "PNG")
        logger.debug(f"Saved OCR debug image to: {debug_path}")
    except Exception as exc:
        logger.warning(f"Failed to save OCR debug image: {exc}")


class OcrService:
    """
    Async/sync OCR service abstraction.
    Keeps threading and error handling outside UI modules.

    Runs a shared image preparation step (format/DPR adaptation) once, then
    passes the prepared QImage to the engine-specific recognize function.
    """

    def __init__(self):
        self._seq = 0
        self._lock = threading.Lock()
        self._pending: tuple | None = None
        self._busy = False

    def recognize(self, request: OcrRequest) -> OcrResponse:
        try:
            engine_id = request.engine or get_default_engine()
            recognize_fn = get_recognize_fn(engine_id)
            if recognize_fn is None:
                raise ValueError(f"Unknown OCR engine: {engine_id}")

            # Shared preprocessing pipeline
            preprocess_result = run_minimal_pipeline(request.pixmap)

            # Debug save
            _save_debug_preprocessed_image(preprocess_result.image, request.debug_dir)

            # Engine receives the preprocessed QImage directly
            logger.info("[OCR_CHAIN] recognize() engine call begin, engine=%s", engine_id)
            recognition = recognize_fn(
                preprocess_result.image,
                language_tag=request.language_tag,
            )
            logger.info("[OCR_CHAIN] recognize() engine call end, engine=%s", engine_id)
            if recognition:
                recognition.engine_type = engine_id

            text = compose_text_from_result(recognition, language_tag=request.language_tag)
            return OcrResponse(
                text=text,
                error="",
                pixmap=request.pixmap,
                recognition=recognition,
            )
        except Exception as exc:
            logger.exception(f"OCR service failed (engine={request.engine}): {exc}")
            return OcrResponse(
                text="",
                error=str(exc),
                pixmap=request.pixmap,
                recognition=None,
            )

    def recognize_async(self, request: OcrRequest, done_callback):
        # Intentional single-slot overwrite, NOT a queue. A later request
        # replaces any pending one, and the worker (below) only delivers a
        # result when its ``seq`` still equals ``self._seq`` — i.e. it is the
        # newest. Superseded requests run to completion but their callbacks
        # are silently dropped by the seq check. This is deliberate: the OCR
        # popup has a single text box, so only the most recent capture's
        # result should ever be shown; a queued FIFO would let an old, slow
        # result clobber a newer one. It also bounds work when a user
        # rapidly re-captures and re-OCRs — at most one in-flight inference
        # plus one pending, never an unbounded backlog.
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._pending = (request, done_callback, seq)
            spawn = not self._busy
            if spawn:
                self._busy = True
        logger.debug("[OCR_CHAIN] recognize_async, seq=%d, spawned_worker=%s", seq, spawn)
        if spawn:
            threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            with self._lock:
                if self._pending is None:
                    self._busy = False
                    return
                request, callback, seq = self._pending
                self._pending = None
            logger.debug("[OCR_CHAIN] worker picked up, seq=%d", seq)

            response = None  # guard: ensure del response in finally never raises UnboundLocalError
            try:
                response = self.recognize(request)
                logger.debug("[OCR_CHAIN] worker recognize done, seq=%d", seq)
                # Decide under the lock whether this result is still the
                # newest, but emit OUTSIDE the lock. Calling the callback
                # (which emits a Qt signal carrying the response — including
                # a QPixmap — across threads) while holding self._lock risks
                # a deadlock / reentrant-lock crash if anything on the main
                # thread tries to acquire the lock while the emit is in
                # flight. The seq check is the only thing that needs the
                # lock; the delivery does not.
                deliver = False
                with self._lock:
                    if seq == self._seq:
                        deliver = True
                    else:
                        logger.info("[OCR_CHAIN] worker result superseded, seq=%d", seq)
                if deliver:
                    callback(response)
                    logger.info("[OCR_CHAIN] worker callback emitted, seq=%d", seq)
            except Exception as exc:
                logger.exception(f"Unexpected error in OCR worker thread: {exc}")
                logger.info("[OCR_CHAIN] worker exception, seq=%d", seq)
                response = OcrResponse(
                    text="",
                    error=str(exc),
                    pixmap=request.pixmap,
                    recognition=None,
                )
                deliver_err = False
                with self._lock:
                    if seq == self._seq:
                        deliver_err = True
                if deliver_err:
                    callback(response)
            finally:
                # Explicitly clear local references so that pixmap and
                # recognition objects are eligible for immediate reclamation
                # by CPython's reference counting.  Do NOT call gc.collect()
                # here — the full-heap scan would touch pages that were
                # trimmed out of the working set by a prior idle trim,
                # pulling them back into physical RAM for no benefit.
                del request
                del response
                del callback
