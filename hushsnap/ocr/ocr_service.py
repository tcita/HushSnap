import logging
import threading
from pathlib import Path

from PyQt6 import QtGui

from ..constants import OCR_SUPERSEDED_ERROR
from .engine import get_default_engine, get_recognize_fn
from .models import OcrRequest, OcrResponse
from .preprocess import run_minimal_pipeline

logger = logging.getLogger(__name__)


def _save_debug_word_boxes(
    image: QtGui.QImage,
    recognition,
    debug_dir: str | Path | None,
) -> None:
    """Draw raw PP-OCR detector word boxes on the preprocessed image.
    Red filled rects + text labels — shows what the detector found before clustering.
    Failures are logged but non-fatal."""
    if not debug_dir or not recognition or not recognition.lines:
        return
    try:
        canvas = QtGui.QImage(image)
        painter = QtGui.QPainter(canvas)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)

        font = painter.font()
        font.setPixelSize(max(10, min(16, int(canvas.height() * 0.012))))
        painter.setFont(font)

        fill = QtGui.QColor(220, 40, 40, 50)
        pen = QtGui.QPen(QtGui.QColor(220, 40, 40), 2)

        for line in recognition.lines:
            for word in line.words:
                b = word.bounding_box
                x, y, w, h = int(b.x), int(b.y), int(b.width), int(b.height)
                if w <= 0 or h <= 0:
                    continue
                painter.fillRect(x, y, w, h, fill)
                painter.setPen(pen)
                painter.drawRect(x, y, w, h)

        painter.end()
        debug_path = Path(debug_dir) / "ocr_debug_words.png"
        canvas.save(str(debug_path), "PNG")
        logger.debug(f"Saved OCR word-box debug image to: {debug_path}")
    except Exception as exc:
        logger.warning(f"Failed to save OCR word-box debug image: {exc}")


def _save_debug_line_boxes(
    image: QtGui.QImage,
    recognition,
    debug_dir: str | Path | None,
) -> None:
    """Draw post-clustering line boxes on the preprocessed image.
    Green rects with L0/L1/… badges — shows the final lines after greedy clustering.
    Failures are logged but non-fatal."""
    if not debug_dir or not recognition or not recognition.lines:
        return
    try:
        canvas = QtGui.QImage(image)
        painter = QtGui.QPainter(canvas)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)

        font = painter.font()
        font.setPixelSize(max(10, min(16, int(canvas.height() * 0.012))))
        painter.setFont(font)

        pen = QtGui.QPen(QtGui.QColor(40, 200, 40), 2)
        painter.setPen(pen)

        for i, line in enumerate(recognition.lines):
            b = line.bounding_box
            x, y, w, h = int(b.x), int(b.y), int(b.width), int(b.height)
            if w <= 0 or h <= 0:
                continue
            painter.drawRect(x, y, w, h)

            # Line index badge — top-right corner
            badge = f"L{i}"
            fm = painter.fontMetrics()
            bw = fm.horizontalAdvance(badge) + 6
            bh = fm.height() + 2
            bx = x + w - bw if x + w - bw > 0 else x
            by = y
            painter.fillRect(bx, by, bw, bh, QtGui.QColor(40, 200, 40, 180))
            painter.setPen(QtGui.QColor(255, 255, 255))
            painter.drawText(bx + 3, by + fm.ascent() + 1, badge)
            painter.setPen(pen)

        painter.end()
        debug_path = Path(debug_dir) / "ocr_debug_lines.png"
        canvas.save(str(debug_path), "PNG")
        logger.debug(f"Saved OCR line-box debug image to: {debug_path}")
    except Exception as exc:
        logger.warning(f"Failed to save OCR line-box debug image: {exc}")


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
        self._shutdown = False
        self._worker_threads: set[threading.Thread] = set()

    def recognize(self, request: OcrRequest) -> OcrResponse:
        try:
            engine_id = request.engine or get_default_engine()
            recognize_fn = get_recognize_fn(engine_id)
            if recognize_fn is None:
                raise ValueError(f"Unknown OCR engine: {engine_id}")

            # Shared preprocessing pipeline
            preprocess_result = run_minimal_pipeline(request.pixmap)

            # Engine receives the preprocessed QImage directly
            logger.info("[OCR_CHAIN] recognize() engine call begin, engine=%s", engine_id)
            recognition = recognize_fn(
                preprocess_result.image,
                language_tag=request.language_tag,
            )
            logger.info("[OCR_CHAIN] recognize() engine call end, engine=%s", engine_id)
            if recognition:
                recognition.engine_type = engine_id

            _save_debug_word_boxes(preprocess_result.image, recognition, request.debug_dir)
            _save_debug_line_boxes(preprocess_result.image, recognition, request.debug_dir)

            return OcrResponse(
                text=recognition.text,
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

    def recognize_async(self, request: OcrRequest, done_callback, notify_if_dropped=False):
        """Run OCR in a worker thread.

        The asynchronous boundary accepts a ``QImage`` only.  ``QPixmap`` is
        a GUI-thread resource, so converting it here (or later in the worker)
        would make a future caller accidentally use it outside the GUI thread.
        Callers that start with a pixmap must convert it on the GUI thread
        before constructing the request.

        ``notify_if_dropped``: when True, ``done_callback`` is *guaranteed* to
        be called exactly once — with the real result, or with an
        ``OcrResponse`` whose ``error`` is ``OCR_SUPERSEDED_ERROR`` if the
        request lost the single worker slot to a newer request (either while
        still pending, or after the worker picked it up).  Defaults to False so
        GUI callers keep the historical silent-drop behaviour; the loopback OCR
        server passes True so a blocking client never hangs on a dropped
        request.
        """
        if not isinstance(request.pixmap, QtGui.QImage):
            raise TypeError(
                "recognize_async() requires OcrRequest.pixmap to be a QImage; "
                "convert QPixmap with toImage() on the GUI thread first"
            )

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
        superseded_pending = None
        with self._lock:
            if self._shutdown:
                # Service is stopping (app exit) — refuse new work so a
                # freshly-spawned worker never delivers a result to a UI
                # object that may already be tearing down.
                logger.debug("[OCR_CHAIN] recognize_async rejected during shutdown")
                return
            # The displaced pending request (if any) is dropped by this
            # overwrite.  If it asked for guaranteed delivery, notify it — but
            # never while holding the lock (the callback may emit Qt signals).
            prev = self._pending
            if prev is not None and prev[3]:
                superseded_pending = prev
            self._seq += 1
            seq = self._seq
            self._pending = (request, done_callback, seq, notify_if_dropped)
            spawn = not self._busy
            if spawn:
                self._busy = True
        if superseded_pending is not None:
            prev_request, prev_callback, prev_seq, _ = superseded_pending
            logger.info(
                "[OCR_CHAIN] pending request superseded before run, seq=%d", prev_seq,
            )
            prev_callback(self._dropped_response(prev_request))
        logger.debug("[OCR_CHAIN] recognize_async, seq=%d, spawned_worker=%s", seq, spawn)
        if spawn:
            worker = threading.Thread(target=self._worker, daemon=True)
            with self._lock:
                self._worker_threads.add(worker)
            worker.start()

    def _worker(self):
        self_ident = threading.current_thread()
        try:
            while True:
                with self._lock:
                    if self._shutdown:
                        self._busy = False
                        return
                    if self._pending is None:
                        self._busy = False
                        return
                    request, callback, seq, notify = self._pending
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
                    notify_drop = False
                    with self._lock:
                        if not self._shutdown and seq == self._seq:
                            deliver = True
                        elif notify and not self._shutdown:
                            notify_drop = True
                            logger.info("[OCR_CHAIN] worker result superseded (notify), seq=%d", seq)
                        else:
                            logger.info("[OCR_CHAIN] worker result superseded or dropped (shutdown), seq=%d", seq)
                    if deliver:
                        callback(response)
                        logger.info("[OCR_CHAIN] worker callback emitted, seq=%d", seq)
                    elif notify_drop:
                        callback(self._dropped_response(request))
                        logger.info("[OCR_CHAIN] worker callback emitted (superseded), seq=%d", seq)
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
                    notify_err = False
                    with self._lock:
                        if not self._shutdown and seq == self._seq:
                            deliver_err = True
                        elif notify and not self._shutdown:
                            notify_err = True
                    if deliver_err:
                        callback(response)
                    elif notify_err:
                        callback(self._dropped_response(request))
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
                    del notify
        finally:
            with self._lock:
                self._worker_threads.discard(self_ident)

    def _dropped_response(self, request: OcrRequest) -> OcrResponse:
        """Build the response delivered to a caller that asked to be notified
        when its request loses the single worker slot to a newer one."""
        return OcrResponse(
            text="",
            error=OCR_SUPERSEDED_ERROR,
            pixmap=request.pixmap,
            recognition=None,
        )

    def shutdown(self, timeout: float = 3.0):
        """Stop OCR workers and wait for them to finish.

        Sets a shutdown flag so running/in-flight workers stop taking new
        requests and exit.  Blocks until each worker thread terminates or
        *timeout* seconds elapse.  Called on app exit (aboutToQuit) so a
        worker can never deliver a result to a UI object that is already
        being torn down (which would crash on a stale QObject pointer).

        Idempotent and safe to call from any thread.
        """
        with self._lock:
            self._shutdown = True
            workers = list(self._worker_threads)
        for worker in workers:
            try:
                worker.join(timeout=timeout)
            except RuntimeError:
                logger.debug("[OCR_CHAIN] worker join on non-started thread", exc_info=True)
            logger.debug("[OCR_CHAIN] worker joined after shutdown (alive=%s)", worker.is_alive())
