"""Automated descender-clipping check: does unclip=1.3 clip Latin descenders?

Renders Latin words containing descender characters (g, j, p, q, y) at
various font sizes/families via Playwright at DPR=1.5, measures the actual
glyph bottom via getBoundingClientRect on individual <span> elements, runs
OCR at unclip=1.3 and 1.6, and flags any case where the detection box
bottom is above (clips) the ground-truth glyph bottom.

Also checks right-edge clipping: short sentences where the last character
might be clipped by the detection box right edge.

Usage:
    python scripts/check_descender_clip.py
"""
from __future__ import annotations

import io, json, os, statistics, sys, tempfile, textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# ── Test matrix ──────────────────────────────────────────────────────────────
FONT_SIZES = [16, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80]
LATIN_FAMILIES = ["Arial", "Times New Roman", "Segoe UI", "Consolas"]

# Words with descenders at known positions.  The prefix ("xx") is a unique
# marker so OCR text can be matched back to the ground truth.
# Each word has the descender character at a specific offset from the start
# (used to compute the expected glyph bottom position).
DESCENDER_WORDS = [
    # (text, descender_char, position_in_text)
    ("xxhanging",  "g", 5),
    ("xxjumping",  "j", 3),
    ("xxhelping",  "p", 6),
    ("xxquickly",  "q", 3),
    ("xxplaying",  "y", 7),
]

# Short sentences for right-edge check
RIGHT_EDGE_TEXT = "The quick brown fox jumps"

UNCLIP_VALUES = [1.3, 1.6]
DPR = 1.5

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { background:white; }
.line { padding:12px 16px; display:flex; gap:24px; }
.char { display:inline-block; line-height:1.2; }
"""


@dataclass
class DescenderSample:
    id: str
    font_family: str
    font_size_css: int
    word: str
    descender_char: str
    descender_pos: int


def main():
    import logging
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)

    out_dir = _project_root / "scratch" / "descender_clip_test"
    out_dir.mkdir(parents=True, exist_ok=True)

    lines_out = []
    def emit(s=""): print(s); lines_out.append(s)

    # ── Build samples ──────────────────────────────────────────────────────
    samples: list[DescenderSample] = []
    sid = 0
    for fs in FONT_SIZES:
        for fam in LATIN_FAMILIES:
            for word, dchar, dpos in DESCENDER_WORDS:
                samples.append(DescenderSample(
                    id=f"d_{fam.replace(' ','_')}_{fs}_{dchar}",
                    font_family=fam, font_size_css=fs,
                    word=word, descender_char=dchar, descender_pos=dpos,
                ))
                sid += 1

    n_desc = len(samples)
    emit(f"Descender samples: {n_desc}  ({len(DESCENDER_WORDS)} words × "
         f"{len(FONT_SIZES)} sizes × {len(LATIN_FAMILIES)} fonts)")

    # Right-edge samples: one per (font, size)
    right_samples: list[DescenderSample] = []
    for fs in FONT_SIZES:
        for fam in LATIN_FAMILIES:
            right_samples.append(DescenderSample(
                id=f"r_{fam.replace(' ','_')}_{fs}",
                font_family=fam, font_size_css=fs,
                word=RIGHT_EDGE_TEXT, descender_char="", descender_pos=0,
            ))

    n_right = len(right_samples)
    emit(f"Right-edge samples: {n_right}")

    all_samples = samples + right_samples
    n_total = len(all_samples)
    emit(f"Total: {n_total}\n")

    # ── Build HTML ─────────────────────────────────────────────────────────
    html_parts = ["<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><style>",
                   CSS, "</style></head><body>"]
    for s in all_samples:
        # Highlight the descender character with a data attr so JS can measure
        # its exact glyph bottom.  For right-edge samples, highlight the last
        # 3 chars.
        if s.descender_char:
            parts = list(s.word)
            # Build spans: each char in its own <span> for precise measurement
            char_spans = []
            for ci, c in enumerate(s.word):
                is_target = (ci == s.descender_pos)
                char_spans.append(
                    f'<span class="char" data-target="{"1" if is_target else "0"}" '
                    f'data-pos="{ci}">{c}</span>'
                )
            chars_html = "".join(char_spans)
        else:
            # Right-edge: highlight last 3 chars
            chars = list(s.word)
            char_spans = []
            for ci, c in enumerate(s.word):
                is_target = (ci >= len(s.word) - 3)
                char_spans.append(
                    f'<span class="char" data-target="{"1" if is_target else "0"}" '
                    f'data-pos="{ci}">{c}</span>'
                )
            chars_html = "".join(char_spans)

        html_parts.append(
            f'<div class="line" style="font-size:{s.font_size_css}px;'
            f'font-family:\'{s.font_family}\',sans-serif;" '
            f'data-id="{s.id}" data-fs="{s.font_size_css}" '
            f'data-family="{s.font_family}">'
            f'<span class="word">{chars_html}</span></div>'
        )
    html_parts.append("</body></html>")
    html = "".join(html_parts)

    # ── Render via Playwright at DPR ───────────────────────────────────────
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="dclip_") as tmp:
        html_path = Path(tmp) / "samples.html"
        html_path.write_text(html, encoding="utf-8")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(device_scale_factor=DPR)
            page = context.new_page()
            page.goto(f"file:///{html_path.as_posix()}")
            page.wait_for_load_state("networkidle")

            total_height = n_total * 120
            page.set_viewport_size({"width": 2400, "height": total_height})

            # Measure per-character bounding rects (device pixels = CSS × DPR)
            # Returns: {sample_id: [{pos, x, y, w, h, bottom, target}, ...]}
            char_data = page.evaluate("""() => {
                const lines = document.querySelectorAll('.line');
                const result = {};
                lines.forEach(el => {
                    const id = el.getAttribute('data-id');
                    const spans = el.querySelectorAll('span.char');
                    const chars = [];
                    spans.forEach(s => {
                        const r = s.getBoundingClientRect();
                        chars.push({
                            pos: parseInt(s.getAttribute('data-pos')),
                            target: s.getAttribute('data-target') === '1',
                            x: r.x, y: r.y, w: r.width, h: r.height,
                            bottom: r.bottom,
                            right: r.right,
                        });
                    });
                    result[id] = chars;
                });
                return result;
            }""")

            # Screenshot each line
            measurements = page.evaluate("""() => {
                const lines = document.querySelectorAll('.line');
                return Array.from(lines).map(el => {
                    const r = el.getBoundingClientRect();
                    return {
                        id: el.getAttribute('data-id'),
                        x: r.x, y: r.y, w: r.width, h: r.height,
                    };
                });
            }""")

            m_by_id = {m["id"]: m for m in measurements}
            png_paths: dict[str, str] = {}
            pad = 8
            for s in all_samples:
                m = m_by_id.get(s.id)
                if not m: continue
                p = out_dir / f"{s.id}.png"
                clip_w = m["w"] + pad * 2
                clip_h = m["h"] + pad * 2
                clip_x = max(0, m["x"] - pad)
                clip_y = max(0, m["y"] - pad)
                vp = page.viewport_size
                if vp:
                    clip_w = min(clip_w, vp["width"] - clip_x)
                    clip_h = min(clip_h, vp["height"] - clip_y)
                if clip_w <= 0 or clip_h <= 0: continue
                page.screenshot(path=str(p), clip={
                    "x": clip_x, "y": clip_y,
                    "width": clip_w, "height": clip_h,
                })
                png_paths[s.id] = str(p)

            context.close()
            browser.close()

    emit(f"Rendered: {len(png_paths)}/{n_total} PNGs")

    # ── Run OCR ────────────────────────────────────────────────────────────
    from rapidocr import RapidOCR, OCRVersion, ModelType

    base_params = {
        "Det.ocr_version": OCRVersion.PPOCRV6,
        "Det.model_type": ModelType.SMALL,
        "Global.use_cls": False,
        "Det.limit_side_len": 32,
        "Det.use_dilation": False,
        "Det.mean": [0.485, 0.456, 0.406],
        "Det.std": [0.229, 0.224, 0.225],
    }
    engine = RapidOCR(params=base_params)

    # ocr_results[unclip][sample_id] = [boxes]
    ocr_results: dict[float, dict[str, list[dict]]] = defaultdict(dict)

    for uval in UNCLIP_VALUES:
        print(f"Running unclip={uval}...", flush=True, end=" ")
        n_boxes = 0
        for s in all_samples:
            p = png_paths.get(s.id)
            if not p: continue
            res = engine(str(p), unclip_ratio=uval)
            boxes = []
            txts = getattr(res, "txts", None)
            txts = list(txts) if txts is not None else []
            boxes_raw = getattr(res, "boxes", None)
            boxes_raw = list(boxes_raw) if boxes_raw is not None else []
            for box, txt in zip(boxes_raw, txts):
                if not txt or not txt.strip(): continue
                xs = [pt[0] for pt in box]
                ys = [pt[1] for pt in box]
                left, right = min(xs), max(xs)
                top, bottom = min(ys), max(ys)
                w, h = right - left, bottom - top
                if w <= 0 or h <= 0: continue
                boxes.append({"left": left, "right": right, "top": top,
                              "bottom": bottom, "width": w, "height": h,
                              "text": txt})
            ocr_results[uval][s.id] = boxes
            n_boxes += len(boxes)
        print(f"{n_boxes} boxes")

    # ── Descender clipping check ───────────────────────────────────────────
    emit("\n" + "=" * 85)
    emit("DESCENDER CLIPPING CHECK")
    emit(f"  For each descender char (g,j,p,q,y), compare OCR box bottom")
    emit(f"  against the actual glyph bottom (getBoundingClientRect).")
    emit(f"  CLIP = box_bottom < glyph_bottom  (box cuts into descender)")
    emit("=" * 85)

    clip_records: dict[float, list[dict]] = defaultdict(list)

    for uval in UNCLIP_VALUES:
        n_clipped = 0
        n_total_desc = 0
        undershoots = []  # (undershoot_px, sample_id, char, ...)

        for s in samples:  # descender samples only
            cdata = char_data.get(s.id, [])
            # Find the target descender character
            target = [c for c in cdata if c["target"]]
            if not target:
                continue
            tc = target[0]
            glyph_bottom = tc["bottom"]  # CSS px — but we're at DPR=1.5

            boxes = ocr_results.get(uval, {}).get(s.id, [])
            n_total_desc += 1

            if not boxes:
                # No detection = effectively clipped
                n_clipped += 1
                undershoots.append((999, s.id, s.descender_char,
                                    glyph_bottom, None))
                continue

            # Use the first (usually only) box
            box = boxes[0]
            box_bottom = box["bottom"]

            # box coordinates are relative to the cropped image.
            # glyph_bottom is relative to the viewport.
            # To compare: box_bottom is in image-local coords, glyph is in viewport.
            # We need to normalize.  The line's bounding rect in viewport coords
            # is m_by_id[s.id]; the box is relative to the cropped image which
            # starts at (m.x - pad, m.y - pad).
            m = m_by_id.get(s.id)
            if not m:
                continue
            # Convert to device px: glyph is CSS px, box is device px.
            # Clip origin in CSS px: (m["x"] - pad, m["y"] - pad)
            glyph_bottom_local = (glyph_bottom - (m["y"] - pad)) * DPR

            undershoot = box_bottom - glyph_bottom_local
            if undershoot < 0:
                n_clipped += 1
                undershoots.append((-undershoot, s.id, s.descender_char,
                                    box_bottom, glyph_bottom_local))

        # Sort by severity
        undershoots.sort(key=lambda x: -x[0])

        emit(f"\n  unclip={uval}: {n_clipped}/{n_total_desc} descenders clipped")
        if undershoots:
            emit(f"  {'─'*70}")
            emit(f"  {'chars':30s} {'undershoot':>10s}  {'font':22s}  {'size':>5s}")
            for us, sid, dch, bb, gb in undershoots[:20]:
                if us >= 998:
                    emit(f"  {dch+' (no detection)':30s} {'∞':>10s}  "
                         f"{sid:22s}")
                else:
                    emit(f"  {'char='+dch:30s} {us:8.1f}px  "
                         f"{sid:22s}")

    # ── Right-edge clipping check ─────────────────────────────────────────
    emit("\n" + "=" * 85)
    emit("RIGHT-EDGE CLIPPING CHECK")
    emit(f"  For the last 3 chars of '{RIGHT_EDGE_TEXT}', compare OCR box")
    emit(f"  right edge against the actual glyph right edge.")
    emit(f"  CLIP = box_right < glyph_right")
    emit("=" * 85)

    for uval in UNCLIP_VALUES:
        n_clipped = 0
        n_total_edge = 0
        undershoots = []

        for s in right_samples:
            cdata = char_data.get(s.id, [])
            targets = [c for c in cdata if c["target"]]
            if not targets:
                continue
            # Rightmost target char
            rightmost = max(targets, key=lambda c: c["right"])
            glyph_right_edge = rightmost["right"]

            boxes = ocr_results.get(uval, {}).get(s.id, [])
            n_total_edge += 1

            if not boxes:
                n_clipped += 1
                undershoots.append((999, s.id, glyph_right_edge, None))
                continue

            box = boxes[0]
            box_right = box["right"]

            m = m_by_id.get(s.id)
            if not m: continue
            glyph_right_local = (glyph_right_edge - (m["x"] - pad)) * DPR

            undershoot = box_right - glyph_right_local
            if undershoot < 0:
                n_clipped += 1
                undershoots.append((-undershoot, s.id, box_right, glyph_right_local))

        undershoots.sort(key=lambda x: -x[0])

        emit(f"\n  unclip={uval}: {n_clipped}/{n_total_edge} right-edge clips")
        if undershoots:
            emit(f"  {'─'*60}")
            emit(f"  {'sample':35s} {'undershoot':>10s}")
            for us, sid, br, gr in undershoots[:12]:
                if us >= 998:
                    emit(f"  {sid:35s} {'∞ (no det)':>10s}")
                else:
                    emit(f"  {sid:35s} {us:8.1f}px")

    # ── Summary ────────────────────────────────────────────────────────────
    emit("\n" + "=" * 85)
    emit("SUMMARY")
    emit("=" * 85)

    for uval in UNCLIP_VALUES:
        n_desc_clip = len([x for x in clip_records.get(uval, [])
                           if x.get("type") == "descender"])
        n_edge_clip = len([x for x in clip_records.get(uval, [])
                           if x.get("type") == "right_edge"])

    emit(f"\nDescender check results:")
    for uval in UNCLIP_VALUES:
        us_list = []
        for s in samples:
            cdata = char_data.get(s.id, [])
            targets = [c for c in cdata if c["target"]]
            if not targets: continue
            tc = targets[0]
            glyph_bottom = tc["bottom"]  # CSS px
            boxes = ocr_results.get(uval, {}).get(s.id, [])
            m = m_by_id.get(s.id)
            if not m or not boxes: continue
            box = boxes[0]
            glyph_bottom_local = (glyph_bottom - (m["y"] - pad)) * DPR  # device px
            us_list.append(box["bottom"] - glyph_bottom_local)

        n = len(us_list)
        if n == 0: continue
        clipped = sum(1 for v in us_list if v < 0)
        mean_us = statistics.mean(us_list)
        median_us = statistics.median(us_list)
        p5_us = sorted(us_list)[max(0, n // 20)]

        emit(f"  unclip={uval}:  {clipped}/{n} clipped  "
             f"undershoot mean={mean_us:+.1f}px  median={median_us:+.1f}px  "
             f"p5={p5_us:+.1f}px  "
             f"{'★ SAFE' if clipped == 0 else f'⚠ {clipped} CLIPS'}")

    emit(f"\nRight-edge check results:")
    for uval in UNCLIP_VALUES:
        us_list = []
        for s in right_samples:
            cdata = char_data.get(s.id, [])
            targets = [c for c in cdata if c["target"]]
            if not targets: continue
            rightmost = max(targets, key=lambda c: c["right"])
            glyph_right = rightmost["right"]  # CSS px
            boxes = ocr_results.get(uval, {}).get(s.id, [])
            m = m_by_id.get(s.id)
            if not m or not boxes: continue
            box = boxes[0]
            glyph_right_local = (glyph_right - (m["x"] - pad)) * DPR  # device px
            us_list.append(box["right"] - glyph_right_local)

        n = len(us_list)
        if n == 0: continue
        clipped = sum(1 for v in us_list if v < 0)
        mean_us = statistics.mean(us_list)
        median_us = statistics.median(us_list)
        p5_us = sorted(us_list)[max(0, n // 20)]

        emit(f"  unclip={uval}:  {clipped}/{n} clipped  "
             f"undershoot mean={mean_us:+.1f}px  median={median_us:+.1f}px  "
             f"p5={p5_us:+.1f}px  "
             f"{'★ SAFE' if clipped == 0 else f'⚠ {clipped} CLIPS'}")

    emit(f"\nOutput directory: {out_dir}/")
    emit("Done.")


if __name__ == "__main__":
    main()
