import logging
import threading

from ..constants import OCR_ENGINE_RAPID
from .models import OcrRequest, OcrResponse
from .rapidocr import recognize_rapidocr_result_from_pixmap
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
            if request.engine == OCR_ENGINE_RAPID:
                recognition = recognize_rapidocr_result_from_pixmap(
                    request.pixmap,
                    language_tag=request.language_tag,
                )
                if recognition:
                    recognition.engine_type = OCR_ENGINE_RAPID
            else:
                recognition = recognize_result_from_pixmap(
                    request.pixmap,
                    language_tag=request.language_tag,
                    debug_dir=request.debug_dir,
                    preprocess_settings=request.preprocess_settings,
                )
                if recognition:
                    recognition.engine_type = OCR_ENGINE_WINDOWS
            
            # Ensure engine_type is set even for empty results
            if recognition and not recognition.engine_type:
                recognition.engine_type = request.engine or OCR_ENGINE_WINDOWS

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
        def worker():
            try:
                response = self.recognize(request)
                done_callback(response)
            except Exception as exc:
                logger.exception(f"Unexpected error in OCR worker thread: {exc}")
                done_callback(OcrResponse(
                    text="",
                    error=str(exc),
                    pixmap=request.pixmap,
                    recognition=None,
                ))

        threading.Thread(target=worker, daemon=True).start()
