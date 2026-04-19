from pathlib import Path

from ..system.windows_ocr import run_windows_ocr_json
from .parsing import parse_ocr_payload


class WindowsOcrEngine:
    def recognize(self, image_path: Path, language_tag: str):
        payload = run_windows_ocr_json(image_path, language_tag)
        return parse_ocr_payload(payload)
