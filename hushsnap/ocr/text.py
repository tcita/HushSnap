import re
from typing import Callable


# ── URL detection (shared by the OCR popup highlighter & click handling) ──────
# Matches http(s) URLs.  The body stops at whitespace and closing brackets so a
# trailing "。"/")" that the OCR picked up (or that sits in the source text)
# doesn't get swallowed into the link.  Remaining trailing punctuation —
# ASCII and CJK — is trimmed in _iter_url_spans so the coloured span and the
# clickable region stay in sync.
#
# The scheme separator accepts ONE or TWO slashes: OCR not infrequently drops a
# slash and yields "https:/host" (see normalize_url — QUrl needs the second
# slash restored before opening, but matching it here keeps it highlighted and
# protects it from the punctuation-cleanup spacers).
URL_REGEX = re.compile(r"https?://?[^\s<>\]\)\}]+", re.IGNORECASE)

# Trailing characters stripped from a matched URL.  CJK fullwidth punctuation
# is included because OCR of Chinese/Japanese text frequently appends 。 or ）
# immediately after a URL.
_URL_TRAILING_CHARS = set(".,;:!?\"')]}。，；：！？」』）】、'")


def _iter_url_spans(text: str):
    """Yield (start, end, url) for each URL in *text*, trailing junk trimmed.

    The returned end offset (and the slice text[start:end]) exclude trailing
    punctuation/brackets, so callers (highlighter, hit-testing) see the same
    span.  URLs never span newlines — callers should pass a single line/block.
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
    """Restore the second slash OCR sometimes drops: ``https:/host`` -> ``https://host``.

    Browsers tolerate a single slash after the scheme, but ``QUrl`` parses
    ``https:/host`` with an empty host and ``QDesktopServices.openUrl`` then
    fails to navigate.  The matched/highlighted span keeps the OCR text as-is
    (so the user sees what was recognised); this is applied only at open time.
    """
    return re.sub(r"^(https?:)/(?!/)", r"\1//", url, flags=re.IGNORECASE)


def apply_outside_urls(text: str, transform: Callable[[str], str]) -> str:
    """Apply *transform* to the non-URL runs of *text*, leaving URL spans intact.

    URLs (per :data:`URL_REGEX`) are matched on the raw, pre-spacing text -
    which is exactly when OCR output is cleanest, before CJK<->Latin spacers insert
    spaces inside them.  Keeping the matched spans
    untouched here means the link highlighter and Ctrl+Click handler later see
    the same complete URL.  Empty string flows through (``transform("")`` may
    still be called on a tail run); ``None`` is not handled - callers pass str.
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
