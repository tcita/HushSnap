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

    Only the most recent async request delivers its result; older requests
    that are still running when a new one arrives are silently dropped.
    """

    def __init__(self):
        self._seq = 0

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
        self._seq += 1
        seq = self._seq

        def worker():
            if seq != self._seq:
                return  # a newer request arrived before this one started
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
            if seq == self._seq:
                done_callback(response)

        threading.Thread(target=worker, daemon=True).start()
