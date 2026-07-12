import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

from .models import OcrLine, OcrRecognition

NO_SPACE_SCRIPT_CHAR_CLASS = r"\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"


# \u2500\u2500 URL detection (shared by the OCR popup highlighter & click handling) \u2500\u2500\u2500\u2500\u2500\u2500
# Matches http(s) URLs.  The body stops at whitespace and closing brackets so a
# trailing "\u3002"/")" that the OCR picked up (or that sits in the source text)
# doesn't get swallowed into the link.  Remaining trailing punctuation \u2014
# ASCII and CJK \u2014 is trimmed in _iter_url_spans so the coloured span and the
# clickable region stay in sync.
#
# The scheme separator accepts ONE or TWO slashes: OCR not infrequently drops a
# slash and yields "https:/host" (see normalize_url \u2014 QUrl needs the second
# slash restored before opening, but matching it here keeps it highlighted and
# protects it from the punctuation-cleanup spacers).
URL_REGEX = re.compile(r"https?://?[^\s<>\]\)\}]+", re.IGNORECASE)

# Trailing characters stripped from a matched URL.  CJK fullwidth punctuation
# is included because OCR of Chinese/Japanese text frequently appends \u3002 or \uff09
# immediately after a URL.
_URL_TRAILING_CHARS = set(".,;:!?\"')]}\u3002\uff0c\uff1b\uff1a\uff01\uff1f\u300d\u300f\uff09\u3011\u3001'")


def _iter_url_spans(text: str):
    """Yield (start, end, url) for each URL in *text*, trailing junk trimmed.

    The returned end offset (and the slice text[start:end]) exclude trailing
    punctuation/brackets, so callers (highlighter, hit-testing) see the same
    span.  URLs never span newlines \u2014 callers should pass a single line/block.
    """
    for m in URL_REGEX.finditer(text):
        url = m.group(0)
        end = m.end()
        while end > m.start() and url[-1] in _URL_TRAILING_CHARS:
            url = url[:-1]
            end -= 1
        if end > m.start():
            yield m.start(), end, text[m.start():end]


def extract_urls(text: str) -> list[str]:
    """Return deduplicated URLs found in *text*, in order of first appearance."""
    seen: set[str] = set()
    urls: list[str] = []
    for _, _, url in _iter_url_spans(text):
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def find_url_at_position(text: str, pos: int) -> str | None:
    """Return the URL covering character index *pos* in *text*, else None."""
    if pos < 0:
        return None
    for start, end, _ in _iter_url_spans(text):
        if start <= pos < end:
            return text[start:end]
    return None


def normalize_url(url: str) -> str:
    """Restore the second slash OCR sometimes drops: ``https:/host`` → ``https://host``.

    Browsers tolerate a single slash after the scheme, but ``QUrl`` parses
    ``https:/host`` with an empty host and ``QDesktopServices.openUrl`` then
    fails to navigate.  The matched/highlighted span keeps the OCR text as-is
    (so the user sees what was recognised); this is applied only at open time.
    """
    return re.sub(r"^(https?:)/(?!/)", r"\1//", url, flags=re.IGNORECASE)


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


def _apply_punct_spacing(text: str) -> str:
    """Tighten/insert spacing around ASCII punctuation for natural-language text."""
    text = re.sub(r"\s+([,;:.!?])", r"\1", text)
    text = re.sub(r"([,;:.!?])(?=[A-Za-z0-9])", r"\1 ", text)
    return text


def apply_outside_urls(text: str, transform: Callable[[str], str]) -> str:
    """Apply *transform* to the non-URL runs of *text*, leaving URL spans intact.

    URLs (per :data:`URL_REGEX`) are matched on the raw, pre-spacing text —
    which is exactly when OCR output is cleanest, before CJK↔Latin spacers and
    punctuation cleanup insert spaces inside them.  Keeping the matched spans
    untouched here means the link highlighter and Ctrl+Click handler later see
    the same complete URL.  Empty string flows through (``transform("")`` may
    still be called on a tail run); ``None`` is not handled — callers pass str.
    """
    if text == "":
        return text
    parts: list[str] = []
    last_end = 0
    for m in URL_REGEX.finditer(text):
        if m.start() > last_end:
            parts.append(transform(text[last_end:m.start()]))
        parts.append(m.group(0))
        last_end = m.end()
    parts.append(transform(text[last_end:]))
    return "".join(parts)


def cleanup_ocr_text_line(text: str) -> str:
    """Normalize spacing around punctuation.

    CJK space-stripping is intentionally NOT done here — the layout engine
    (ppocr._greedy_line_cluster + _apply_cjk_spacing) is the
    authority on inter-word spacing via word_separator() and pangu-style
    CJK↔Latin regexes.  Stripping spaces here would undo that work.

    URLs are protected from the punctuation rules: the dot/colon spacers would
    otherwise rewrite ``https://www.deepseek.com`` to ``https://www. deepseek.
    com`` (a space after every dot), and the link highlighter — which stops at
    whitespace — would then only colour ``https://www.``.  Only the non-URL
    runs are reformatted, via :func:`apply_outside_urls`.
    """
    return apply_outside_urls(text, _apply_punct_spacing)


def normalize_token_text(token: str) -> str:
    # Preserve original spacing inside tokens but remove trailing junk
    return unicodedata.normalize("NFKC", (token or "").rstrip())


def compose_default_line_text(line: OcrLine) -> str:
    # Strip trailing spaces/tabs (OCR noise) but preserve \n line separators.
    # normalize_ocr_text (finalize_text) handles full line-by-line cleanup after join.
    return cleanup_ocr_text_line((line.text or "").rstrip(" \t"))


def compose_spaced_line_text(line: OcrLine) -> str:
    return compose_default_line_text(line)


def compose_cjk_line_text(line: OcrLine) -> str:
    # If it's already a pre-composed line with indentation (e.g. from PP-OCR layout),
    # return with only spaces/tabs stripped — \n paragraph markers must survive.
    return (line.text or "").rstrip(" \t")


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
    
    # Trim leading/trailing blank lines but preserve indentation of the first non-empty line
    start = 0
    while start < len(cleaned_lines) and not cleaned_lines[start]:
        start += 1
    end = len(cleaned_lines)
    while end > start and not cleaned_lines[end - 1]:
        end -= 1
    return "\n".join(cleaned_lines[start:end])


def matches_chinese(language_tag: str) -> bool:
    return (language_tag or "").lower().startswith("zh")


def matches_english(language_tag: str) -> bool:
    return (language_tag or "").lower().startswith("en")


def finalize_default_text(text: str) -> str:
    return normalize_ocr_text(text)


def finalize_english_text(text: str) -> str:
    return normalize_ocr_text(text)


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

    # Trim leading/trailing blank lines but preserve indentation of the first non-empty line
    start = 0
    while start < len(result) and not result[start]:
        start += 1
    end = len(result)
    while end > start and not result[end - 1]:
        end -= 1
    return "\n".join(result[start:end])


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
