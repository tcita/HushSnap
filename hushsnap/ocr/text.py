import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

from .models import OcrLine, OcrRecognition

NO_SPACE_SCRIPT_CHAR_CLASS = r"\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"

NUMBERS_TO_LETTERS = {
    # Heuristic correction table for OCR errors (digit -> letter).
    # Intentionally empty: keeping interface but removing heuristics for now.
}
LETTERS_TO_NUMBERS = {
    # Heuristic correction table for OCR errors (letter -> digit).
    # Intentionally empty: keeping interface but removing heuristics for now.
}


@dataclass(frozen=True)
class OcrTextAdapter:
    """Language-specific text composition rules kept separate from OCR IO."""

    name: str
    matches_language: Callable[[str], bool]
    compose_line: Callable[[OcrLine], str]
    finalize_text: Callable[[str], str]


def is_space_joining_word(token: str) -> bool:
    r"""
    Text-Grab style SpaceJoiningWordRegex: (^[\p{L}-[\p{Lo}]]|\p{Nd}$)|.{2,}
    Matches words that should trigger a space-joining behavior.
    """
    if not token:
        return False

    # CJK tokens should never trigger space joining logic, regardless of length
    if re.search(f"[{NO_SPACE_SCRIPT_CHAR_CLASS}]", token):
        return False

    if len(token) >= 2:
        return True

    char = token[0]
    category = unicodedata.category(char)
    if category == "Nd":
        return True
    if category.startswith("L") and category != "Lo":
        return True
    return False


def cleanup_ocr_text_line(text: str) -> str:
    """Normalize spacing around punctuation.

    CJK space-stripping is intentionally NOT done here — the layout engine
    (ppocr._build_lines_from_ordered_blocks + _apply_cjk_spacing) is the
    authority on inter-word spacing via word_separator() and pangu-style
    CJK↔Latin regexes.  Stripping spaces here would undo that work.
    """
    text = re.sub(r"\s+([,;:.!?])", r"\1", text)
    text = re.sub(r"([,;:.!?])(?=[A-Za-z0-9])", r"\1 ", text)
    return text


def normalize_token_text(token: str) -> str:
    # Preserve original spacing inside tokens but remove trailing junk
    return unicodedata.normalize("NFKC", (token or "").rstrip())


def compose_default_line_text(line: OcrLine) -> str:
    return cleanup_ocr_text_line((line.text or "").rstrip())


def compose_spaced_line_text(line: OcrLine) -> str:
    return compose_default_line_text(line)


def compose_cjk_line_text(line: OcrLine) -> str:
    # If it's already a pre-composed line with indentation (e.g. from PP-OCR layout)
    # we just return it with trailing spaces removed.
    return (line.text or "").rstrip()


def normalize_ocr_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        # Keep leading spaces (indentation), only strip trailing
        cleaned_line = line.rstrip()
        if not cleaned_line.strip():
            # If the line is only whitespace, we preserve a truly empty line 
            # for paragraph separation, but don't keep the spaces.
            cleaned_lines.append("")
            continue
        cleaned_lines.append(cleaned_line)
    return "\n".join(cleaned_lines).strip()


def replace_with_map(text: str, mapping: dict[str, str]) -> str:
    return "".join(mapping.get(char, char) for char in text)


def try_fix_number_letter_errors(token: str) -> str:
    if len(token) < 5:
        return token

    total_numbers = sum(1 for char in token if char.isdigit())
    total_letters = sum(1 for char in token if char.isalpha())
    if total_numbers / max(1, len(token)) >= 0.6:
        return replace_with_map(token, LETTERS_TO_NUMBERS)
    if total_letters / max(1, len(token)) >= 0.6:
        return replace_with_map(token, NUMBERS_TO_LETTERS)
    return token


def try_fix_every_word_letter_number_errors(text: str) -> str:
    words = text.split(" ")
    fixed_words = [try_fix_number_letter_errors(word) for word in words]
    joined = " ".join(fixed_words)
    joined = joined.replace("\t ", "\t").replace("\r ", "\r").replace("\n ", "\n")
    return joined.strip()


def matches_chinese(language_tag: str) -> bool:
    return (language_tag or "").lower().startswith("zh")


def matches_english(language_tag: str) -> bool:
    return (language_tag or "").lower().startswith("en")


def finalize_default_text(text: str) -> str:
    return normalize_ocr_text(text)


def finalize_english_text(text: str) -> str:
    return try_fix_every_word_letter_number_errors(normalize_ocr_text(text))


LANGUAGE_TEXT_ADAPTERS = (
    OcrTextAdapter(
        name="chinese",
        matches_language=matches_chinese,
        compose_line=compose_cjk_line_text,
        finalize_text=finalize_default_text,
    ),
    OcrTextAdapter(
        name="english",
        matches_language=matches_english,
        compose_line=compose_spaced_line_text,
        finalize_text=finalize_english_text,
    ),
    OcrTextAdapter(
        name="default",
        matches_language=lambda _language_tag: True,
        compose_line=compose_default_line_text,
        finalize_text=finalize_default_text,
    ),
)


def select_text_adapter(language_tag: str) -> OcrTextAdapter:
    for adapter in LANGUAGE_TEXT_ADAPTERS:
        if adapter.matches_language(language_tag):
            return adapter
    return LANGUAGE_TEXT_ADAPTERS[-1]


def _postprocess_layout_text(text: str) -> str:
    """Final text-level fixes after layout composition (spec ⑤)."""
    if not text:
        return text

    # Normalize excessive blank lines: max 2 consecutive newlines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Note: we use rstrip() here because leading whitespace 
    # might be intentional indentation from the layout engine.
    text = text.rstrip()

    # Ensure whitespace-only lines are truly empty for paragraph separation,
    # while preserving indentation on non-empty lines.
    lines = text.split("\n")
    result: list[str] = []
    for line in lines:
        cleaned = line.rstrip()
        if not cleaned.strip():
            result.append("")  # truly blank line
            continue
        result.append(cleaned)
    return "\n".join(result).strip()


def compose_text_from_result(result: OcrRecognition, language_tag: str = "") -> str:
    adapter = select_text_adapter(language_tag)

    if not result.lines:
        return adapter.finalize_text(result.text)

    built_lines: list[str] = []
    for line in result.lines:
        joined = adapter.compose_line(line)
        if joined:
            built_lines.append(joined)

    if not built_lines:
        return adapter.finalize_text(result.text)

    return adapter.finalize_text(
        _postprocess_layout_text("\n".join(built_lines))
    )
