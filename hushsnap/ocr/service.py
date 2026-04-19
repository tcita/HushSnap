import logging
import threading

from .models import OcrRequest, OcrResponse
from .recognition import recognize_result_from_pixmap
from .text import compose_text_from_result

logger = logging.getLogger(__name__)


class OcrService:
    """
    Async/sync OCR service abstraction.
    Keeps threading and error handling outside UI modules.
    """

    def recognize(self, request: OcrRequest) -> OcrResponse:
        try:
            recognition = recognize_result_from_pixmap(
                request.pixmap,
                language_tag=request.language_tag,
                debug_dir=request.debug_dir,
            )
            text = compose_text_from_result(recognition, language_tag=request.language_tag)
            return OcrResponse(
                text=text,
                error="",
                pixmap=request.pixmap,
                recognition=recognition,
            )
        except Exception as exc:
            logger.exception(f"OCR service failed: {exc}")
            return OcrResponse(
                text="",
                error=str(exc),
                pixmap=request.pixmap,
                recognition=None,
            )

    def recognize_async(self, request: OcrRequest, done_callback):
        def worker():
            done_callback(self.recognize(request))

        threading.Thread(target=worker, daemon=True).start()
