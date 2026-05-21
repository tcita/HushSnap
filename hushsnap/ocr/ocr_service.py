import logging
import threading

from .engine import get_default_engine, get_recognize_fn
from .models import OcrRequest, OcrResponse
from .text import compose_text_from_result

logger = logging.getLogger(__name__)


class OcrService:
    """
    Async/sync OCR service abstraction.
    Keeps threading and error handling outside UI modules.

    Uses a single worker thread. When a new request arrives while one is
    processing, the in-flight result is dropped and the worker immediately
    picks up the latest request — no wasted concurrent processing.
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

            recognition = recognize_fn(
                request.pixmap,
                language_tag=request.language_tag,
                debug_dir=request.debug_dir,
                preprocess_settings=request.preprocess_settings,
            )
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
        with self._lock:
            self._seq += 1
            self._pending = (request, done_callback, self._seq)
            if not self._busy:
                self._busy = True
                threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            with self._lock:
                if self._pending is None:
                    self._busy = False
                    return
                request, callback, seq = self._pending
                self._pending = None

            try:
                response = self.recognize(request)
            except Exception as exc:
                logger.exception(f"Unexpected error in OCR worker thread: {exc}")
                response = OcrResponse(
                    text="",
                    error=str(exc),
                    pixmap=request.pixmap,
                    recognition=None,
                )

            with self._lock:
                if seq == self._seq:
                    callback(response)
