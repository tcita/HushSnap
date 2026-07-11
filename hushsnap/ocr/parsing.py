from typing import Any

from .models import OcrBox, OcrLine, OcrWord


def parse_box(obj: Any) -> OcrBox:
    """Parse a JSON object into OcrBox, accepting PascalCase and camelCase keys."""
    if not isinstance(obj, dict):
        return OcrBox()
    return OcrBox(
        x=float(obj.get("X", obj.get("x", 0.0)) or 0.0),
        y=float(obj.get("Y", obj.get("y", 0.0)) or 0.0),
        width=float(obj.get("Width", obj.get("width", 0.0)) or 0.0),
        height=float(obj.get("Height", obj.get("height", 0.0)) or 0.0),
    )


def compute_line_box(words: list[OcrWord]) -> OcrBox:
    """Compute a line box from word-level boxes when line box is missing."""
    if not words:
        return OcrBox()
    left = min(word.bounding_box.x for word in words)
    top = min(word.bounding_box.y for word in words)
    right = max(word.bounding_box.x + word.bounding_box.width for word in words)
    bottom = max(word.bounding_box.y + word.bounding_box.height for word in words)
    return OcrBox(x=left, y=top, width=max(0.0, right - left), height=max(0.0, bottom - top))


def parse_word(word_obj: Any) -> OcrWord | None:
    """Parse one OCR word node."""
    if not isinstance(word_obj, dict):
        return None
    return OcrWord(
        text=str(word_obj.get("Text", "") or ""),
        bounding_box=parse_box(word_obj.get("BoundingBox")),
    )


def parse_line(line_obj: Any) -> OcrLine | None:
    """Parse one OCR line node and backfill line box from words if needed."""
    if not isinstance(line_obj, dict):
        return None

    words: list[OcrWord] = []
    for word_obj in line_obj.get("Words", []) or []:
        parsed_word = parse_word(word_obj)
        if parsed_word is not None:
            words.append(parsed_word)

    line_box = parse_box(line_obj.get("BoundingBox"))
    if line_box.width <= 0.0 or line_box.height <= 0.0:
        line_box = compute_line_box(words)

    return OcrLine(
        text=str(line_obj.get("Text", "") or ""),
        words=words,
        bounding_box=line_box,
    )

