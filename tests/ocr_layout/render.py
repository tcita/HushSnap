"""HTML generation → Playwright Chromium → PNG + ground-truth metadata.

Produces a :class:`RenderResult` that knows exactly what text was rendered
where and at what font-size, so downstream evaluators can compare OCR output
against the ground truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cases import LineClusteringCase, BoxHeightCase

# ═══════════════════════════════════════════════════════════════════════════
# Shared CSS
# ═══════════════════════════════════════════════════════════════════════════

_CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { background:white; font-family:Arial,sans-serif; }
.block { padding:4px 8px; }
.word { display:inline-block; line-height:1; }
.spacer { display:inline-block; }
"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class RenderedWord:
    """One word's ground-truth position and size in the PNG."""
    token: str          # the unique token string rendered (e.g. "axAbout")
    line_idx: int       # which ground-truth line (0-based)
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    font_size_px: float = 0.0
    font_family: str = ""


@dataclass
class RenderResult:
    """Output of rendering one or more test cases."""

    png_path: Path
    """Path to the rendered PNG."""

    words: list[RenderedWord] = field(default_factory=list)
    """Every rendered word with its measured position and font metrics."""

    # Per-case grouping
    case_ids: list[str] = field(default_factory=list)
    """Case IDs in render order."""

    # Metadata
    png_width: int = 0
    png_height: int = 0


# ═══════════════════════════════════════════════════════════════════════════
# HTML builders
# ═══════════════════════════════════════════════════════════════════════════


def _build_line_clustering_html(case: "LineClusteringCase") -> str:
    """Build a standalone HTML page for one multi-line clustering test case."""
    from .cases import _EN_WORDS, _CJK_WORDS

    word_bank = _CJK_WORDS if case.use_cjk else _EN_WORDS
    lh_px = round(case.font_size_px * case.line_height_ratio, 1)

    # Pick words
    words_flat: list[str] = []
    for i in range(case.n_lines * case.words_per_line):
        words_flat.append(word_bank[(case.word_offset + i) % len(word_bank)])

    # Build lines
    vert_style = ("writing-mode:vertical-rl;text-orientation:upright;"
                  if case.is_vertical else "")
    lines_html: list[str] = []
    for li in range(case.n_lines):
        start = li * case.words_per_line
        line_words = words_flat[start:start + case.words_per_line]
        parts: list[str] = []
        for wi, w in enumerate(line_words):
            token = f"{case.prefix}{w}"
            parts.append(
                f'<span class="word" data-line="{li}" '
                f'data-token="{_esc(token)}" '
                f'data-fs="{case.font_size_px}" '
                f'data-family="{_esc(case.font_family)}">'
                f'{_esc(token)}</span>'
            )
            if wi < len(line_words) - 1:
                parts.append(
                    f'<span class="spacer" '
                    f'style="width:{case.word_spacing_px}px"></span>'
                )
        lines_html.append("".join(parts))

    sep = "<br>" if not case.is_vertical else "<br>"
    body = sep.join(lines_html)

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>{_CSS}</style></head>
<body>
<div class="block" style="font-size:{case.font_size_px}px;
line-height:{lh_px}px;font-family:'{case.font_family}',sans-serif;{vert_style}">
{body}
</div>
</body></html>"""


def _build_box_height_html(case: "BoxHeightCase") -> str:
    """Build a standalone HTML page for box-height measurement."""
    vert_style = ("writing-mode:vertical-rl;text-orientation:upright;"
                  if case.is_vertical else "")
    spans: list[str] = []
    for wi, w in enumerate(case.words):
        spans.append(
            f'<span class="word" data-line="0" '
            f'data-token="{_esc(w)}" '
            f'data-fs="{case.font_size_px}" '
            f'data-family="{_esc(case.font_family)}">'
            f'{_esc(w)}</span>'
        )
        if wi < len(case.words) - 1:
            spans.append('<span class="spacer" style="width:48px"></span>')

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>{_CSS}</style></head>
<body>
<div class="block" style="font-size:{case.font_size_px}px;
font-family:'{case.font_family}',sans-serif;line-height:1.2;{vert_style}">
{"".join(spans)}
</div>
</body></html>"""


# ═══════════════════════════════════════════════════════════════════════════
# Rendering
# ═══════════════════════════════════════════════════════════════════════════


def render_cases(
    cases: list,
    out_dir: str | Path = "",
    *,
    headless: bool = True,
) -> list[RenderResult]:
    """Render one or more cases via Playwright Chromium.

    Each case gets its own HTML → PNG conversion.  The rendered words are
    measured (getBoundingClientRect) so the ground-truth position and size
    are known pixel-accurately.

    Args:
        cases: list of :class:`LineClusteringCase` or :class:`BoxHeightCase`.
        out_dir: directory for PNG files (default: system temp).
        headless: run Chromium headless.

    Returns:
        One :class:`RenderResult` per case, in the same order.
    """
    from playwright.sync_api import sync_playwright

    if not out_dir:
        import tempfile
        out_dir = Path(tempfile.mkdtemp(prefix="ocr_layout_"))
    else:
        out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[RenderResult] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(device_scale_factor=1)

        for case in cases:
            # Build HTML
            if hasattr(case, "line_height_ratio"):
                html = _build_line_clustering_html(case)
            else:
                html = _build_box_height_html(case)

            # Write temp HTML
            html_path = out_dir / f"_case_{case.id}.html"
            png_path = out_dir / f"{case.id}.png"
            html_path.write_text(html, encoding="utf-8")

            page = context.new_page()
            page.goto(f"file:///{html_path.as_posix()}")
            page.wait_for_load_state("networkidle")

            # Measure every .word span
            measurements = page.evaluate("""
            () => {
                const spans = document.querySelectorAll('span.word');
                return Array.from(spans).map(s => ({
                    token: s.getAttribute('data-token'),
                    line: parseInt(s.getAttribute('data-line') || '0'),
                    fs: parseFloat(s.getAttribute('data-fs') || '0'),
                    family: s.getAttribute('data-family') || '',
                    x: s.getBoundingClientRect().x,
                    y: s.getBoundingClientRect().y,
                    w: s.getBoundingClientRect().width,
                    h: s.getBoundingClientRect().height,
                }));
            }
            """)

            # Build RenderedWord list
            words = []
            for m in measurements:
                words.append(RenderedWord(
                    token=m["token"],
                    line_idx=m["line"],
                    x=m["x"], y=m["y"],
                    width=m["w"], height=m["h"],
                    font_size_px=m["fs"],
                    font_family=m["family"],
                ))

            # Screenshot clipped to the .block element
            rect = page.evaluate("""
            () => {
                const el = document.querySelector('.block');
                const r = el.getBoundingClientRect();
                return {x:r.x, y:r.y, w:r.width, h:r.height};
            }
            """)
            page.screenshot(path=str(png_path), clip={
                "x": rect["x"], "y": rect["y"],
                "width": rect["w"], "height": rect["h"],
            })
            page.close()
            html_path.unlink(missing_ok=True)

            results.append(RenderResult(
                png_path=png_path,
                words=words,
                case_ids=[case.id],
                png_width=int(rect["w"]),
                png_height=int(rect["h"]),
            ))

        context.close()
        browser.close()

    return results


def render_single_case(case, out_dir: str | Path = "") -> RenderResult:
    """Convenience: render exactly one case, return its RenderResult."""
    return render_cases([case], out_dir)[0]
