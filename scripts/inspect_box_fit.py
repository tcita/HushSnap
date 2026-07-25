"""High-zoom visual inspection: HTML overlay with precise box lines.

Renders descender samples and right-edge samples just like the clip test,
then produces an HTML page where each sample is shown as the PNG background
with CSS-positioned box overlays (0.5px dashed lines) and truth markers.
Zoom in the browser — lines stay crisp at any magnification.

Usage:
    python scripts/inspect_box_fit.py
    Then open scratch/box_inspect/inspect.html in a browser.
"""
from __future__ import annotations

import json, os, sys, tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# ── Test params ─────────────────────────────────────────────────────────────
FONT_SIZES = [16, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80]
LATIN_FAMILIES = ["Segoe UI", "Arial", "Times New Roman", "Consolas"]

# Descender words + right-edge sentences
DESCENDER_WORDS = [
    # (text, descender_char, 0-based position in the string)
    ("hanging",  "g", 3),   # h(0)a(1)n(2)g(3)i(4)n(5)g(6)
    ("jumping",  "j", 0),   # j(0)u(1)m(2)p(3)i(4)n(5)g(6)
    ("helping",  "p", 3),   # h(0)e(1)l(2)p(3)i(4)n(5)g(6)
    ("quickly",  "q", 0),   # q(0)u(1)i(2)c(3)k(4)l(5)y(6)
    ("playing",  "y", 3),   # p(0)l(1)a(2)y(3)i(4)n(5)g(6)
]
EDGE_TEXT = "The quick brown fox jumps over"

UNCLIP = [1.3, 1.6]
DPR = 1.5  # production capture DPR

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { background:white; }
.case { padding:12px 16px; display:flex; gap:24px; }
.char { display:inline-block; line-height:1.2; }
"""


@dataclass
class Sample:
    id: str
    font_family: str
    font_size_css: int
    text: str
    descender_char: str = ""
    descender_pos: int = -1
    is_edge: bool = False

    # Filled after render
    png_path: str = ""
    line_x: float = 0  # viewport coords
    line_y: float = 0
    line_w: float = 0
    line_h: float = 0
    # Per-char truth (viewport coords)
    char_rects: list[dict] = field(default_factory=list)
    # OCR results {unclip: [boxes]}
    ocr: dict = field(default_factory=dict)


def main():
    import logging
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)

    out_dir = _project_root / "scratch" / "box_inspect"
    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir = out_dir / "pngs"
    img_dir.mkdir(exist_ok=True)

    # ── Build samples ──────────────────────────────────────────────────────
    samples: list[Sample] = []

    # Descender samples
    for fs in FONT_SIZES:
        for fam in LATIN_FAMILIES:
            for word, dchar, dpos in DESCENDER_WORDS:
                samples.append(Sample(
                    id=f"d_{fam.replace(' ','_')}_{fs}_{dchar}",
                    font_family=fam, font_size_css=fs,
                    text=word, descender_char=dchar, descender_pos=dpos,
                ))

    # Right-edge samples
    for fs in FONT_SIZES:
        for fam in LATIN_FAMILIES:
            samples.append(Sample(
                id=f"e_{fam.replace(' ','_')}_{fs}",
                font_family=fam, font_size_css=fs,
                text=EDGE_TEXT, is_edge=True,
            ))

    n = len(samples)
    pad = 8
    print(f"Samples: {n}")

    # ── Build HTML ─────────────────────────────────────────────────────────
    html_parts = ["<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><style>",
                   CSS, "</style></head><body>"]
    for s in samples:
        char_spans = []
        for ci, c in enumerate(s.text):
            is_target = (ci == s.descender_pos) if s.descender_char else (ci >= len(s.text) - 3)
            char_spans.append(
                f'<span class="char" data-target="{"1" if is_target else "0"}" '
                f'data-pos="{ci}">{c}</span>'
            )
        html_parts.append(
            f'<div class="case" style="font-size:{s.font_size_css}px;'
            f'font-family:\'{s.font_family}\',sans-serif;" '
            f'data-id="{s.id}" data-fs="{s.font_size_css}" '
            f'data-family="{s.font_family}">'
            f'{"".join(char_spans)}</div>'
        )
    html_parts.append("</body></html>")
    html = "".join(html_parts)

    # ── Render ─────────────────────────────────────────────────────────────
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="inspect_") as tmp:
        html_path = Path(tmp) / "samples.html"
        html_path.write_text(html, encoding="utf-8")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(device_scale_factor=DPR)
            page = context.new_page()
            page.goto(f"file:///{html_path.as_posix()}")
            page.wait_for_load_state("networkidle")
            page.set_viewport_size({"width": 2400, "height": n * 120})

            # Measure char rects + case rects
            data = page.evaluate("""() => {
                const cases = document.querySelectorAll('.case');
                const result = {};
                cases.forEach(el => {
                    const id = el.getAttribute('data-id');
                    const cr = el.getBoundingClientRect();
                    const chars = [];
                    el.querySelectorAll('span.char').forEach(s => {
                        const r = s.getBoundingClientRect();
                        chars.push({
                            pos: parseInt(s.getAttribute('data-pos')),
                            target: s.getAttribute('data-target') === '1',
                            x: r.x, y: r.y, w: r.width, h: r.height,
                            bottom: r.bottom, right: r.right,
                        });
                    });
                    result[id] = {
                        x: cr.x, y: cr.y, w: cr.width, h: cr.height,
                        chars: chars,
                    };
                });
                return result;
            }""")

            # Screenshot each case
            case_rects = page.evaluate("""() => {
                const cases = document.querySelectorAll('.case');
                return Array.from(cases).map(el => {
                    const r = el.getBoundingClientRect();
                    return {
                        id: el.getAttribute('data-id'),
                        x: r.x, y: r.y, w: r.width, h: r.height,
                    };
                });
            }""")
            cr_by_id = {c["id"]: c for c in case_rects}

            for s in samples:
                cr = cr_by_id.get(s.id)
                if not cr: continue
                p = img_dir / f"{s.id}.png"
                clip_w = cr["w"] + pad * 2
                clip_h = cr["h"] + pad * 2
                clip_x = max(0, cr["x"] - pad)
                clip_y = max(0, cr["y"] - pad)
                vp = page.viewport_size
                if vp:
                    clip_w = min(clip_w, vp["width"] - clip_x)
                    clip_h = min(clip_h, vp["height"] - clip_y)
                if clip_w <= 0 or clip_h <= 0: continue
                page.screenshot(path=str(p), clip={
                    "x": clip_x, "y": clip_y,
                    "width": clip_w, "height": clip_h,
                })

                # Store truth data
                d = data.get(s.id, {})
                s.png_path = str(p)
                s.line_x = cr["x"]
                s.line_y = cr["y"]
                s.line_w = cr["w"]
                s.line_h = cr["h"]
                s.char_rects = d.get("chars", [])

            context.close()
            browser.close()

    print(f"Rendered: {sum(1 for s in samples if s.png_path)}/{n}")

    # ── OCR ────────────────────────────────────────────────────────────────
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

    for uval in UNCLIP:
        print(f"OCR unclip={uval}...", flush=True, end=" ")
        n_boxes = 0
        for s in samples:
            if not s.png_path: continue
            res = engine(str(s.png_path), unclip_ratio=uval)
            txts = getattr(res, "txts", None)
            txts = list(txts) if txts is not None else []
            boxes_raw = getattr(res, "boxes", None)
            boxes_raw = list(boxes_raw) if boxes_raw is not None else []
            boxes = []
            for box, txt in zip(boxes_raw, txts):
                if not txt or not txt.strip(): continue
                xs = [pt[0] for pt in box]; ys = [pt[1] for pt in box]
                l, r, t, b = min(xs), max(xs), min(ys), max(ys)
                w, h = r - l, b - t
                if w <= 0 or h <= 0: continue
                boxes.append({"left": l, "right": r, "top": t, "bottom": b,
                              "width": w, "height": h, "text": txt})
            s.ocr[uval] = boxes
            n_boxes += len(boxes)
        print(f"{n_boxes} boxes")

    # ── Build HTML inspector ───────────────────────────────────────────────
    # IMPORTANT: getBoundingClientRect returns CSS px.  The PNG is rendered at
    # DPR=1.5 → device px = CSS px × DPR.  OCR boxes are in device px (from
    # the PNG file).  Everything in the HTML must be in device px to align.
    import cv2 as _cv2

    inspect_parts = ["""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>Box Fit Inspector</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:#222; color:#ddd; font:14px/1.4 system-ui,sans-serif; padding:20px; }
  h1 { margin-bottom:4px; }
  .legend { margin-bottom:20px; color:#aaa; font-size:13px; }
  .legend span { margin-right:20px; }
  .legend .c13 { color:#4af; } .legend .c16 { color:#f55; }
  .legend .ctruth { color:#0f0; }
  .sticky { position:sticky; top:0; background:#222; padding:8px 0; z-index:10;
            border-bottom:1px solid #444; margin-bottom:16px; }
  .sticky button { margin-right:8px; padding:4px 12px; cursor:pointer;
                   background:#444; color:#ddd; border:1px solid #555; border-radius:3px; }
  .sticky button.active { background:#268; border-color:#4af; }
  .sample { position:relative; display:inline-block; margin:8px;
            border:1px solid #333; background:#000; vertical-align:top; }
  .sample .info { position:absolute; top:0; left:0; right:0;
                  background:rgba(0,0,0,0.8); color:#aaa; font-size:10px;
                  padding:2px 4px; white-space:nowrap; overflow:hidden; }
  .sample img { display:block; }
  /* Box overlays — 0.5px via scale(0.5) trick for crisp sub-pixel lines */
  .box-overlay { position:absolute; pointer-events:none; }
  .box-overlay .b13 { position:absolute; outline:1px solid rgba(68,170,255,0.9);
                      outline-offset:-0.5px; }
  .box-overlay .b16 { position:absolute; outline:1px solid rgba(255,85,85,0.9);
                      outline-offset:-0.5px; }
  /* Truth corner markers */
  .truth-marker { position:absolute; pointer-events:none; }
  .truth-marker .tm { position:absolute; width:4px; height:4px;
                      background:#0f0; }
  /* Descender target highlight */
  .d-target { position:absolute; pointer-events:none;
              border-bottom:1px dashed #ff0; }
  /* Right-edge target */
  .e-target { position:absolute; pointer-events:none;
              border-right:1px dashed #ff0; }
  .sample.hidden-sample { display:none; }
</style>
</head><body>
<h1>Box Fit Inspector — unclip 1.3 vs 1.6</h1>
<div class="legend">
  <span class="ctruth">▊▊ green</span> = truth corners (getBoundingClientRect on each char)
  <span class="c13">▊▊ blue outline</span> = unclip 1.3 detection box
  <span class="c16">▊▊ red outline</span> = unclip 1.6 detection box
  <span style="color:#ff0">▊▊ yellow dash</span> = descender bottom / right edge truth
</div>
<div class="sticky">
  <button onclick="filter('all')" id="btn_all" class="active">All</button>
  <button onclick="filter('desc')" id="btn_desc">Descender only</button>
  <button onclick="filter('edge')" id="btn_edge">Right-edge only</button>
  <button onclick="filter('small')" id="btn_small">Small (≤24px)</button>
  <button onclick="filter('large')" id="btn_large">Large (≥56px)</button>
  <span style="margin-left:20px;color:#888">"刷" 缩放页面即可, Ctrl+滚轮</span>
</div>
<div id="grid">
"""]
    inspect_parts.append("")

    for s in samples:
        if not s.png_path: continue
        rel_png = f"pngs/{s.id}.png"

        # Read actual PNG dimensions (device pixels)
        png = _cv2.imread(s.png_path)
        if png is None: continue
        dp_h, dp_w = png.shape[:2]  # device pixels

        # Build box overlays (OCR coords already in device px = image px)
        overlay_divs = []
        colors = {1.3: "b13", 1.6: "b16"}
        for uval in UNCLIP:
            cls = colors[uval]
            for box in s.ocr.get(uval, []):
                l, t, r, b = box["left"], box["top"], box["right"], box["bottom"]
                overlay_divs.append(
                    f'<div class="{cls}" style="left:{l:.1f}px;top:{t:.1f}px;'
                    f'width:{r-l:.1f}px;height:{b-t:.1f}px"></div>'
                )

        # Truth and descender marker
        # Use the case element rect as the overall truth box, and the target
        # descender span for the precise bottom reference.
        # All coords: CSS px → device px (× DPR).
        # Clip origin in viewport CSS px: (s.line_x - pad, s.line_y - pad)
        ox = s.line_x - pad  # CSS px
        oy = s.line_y - pad

        # Overall text truth box (from case element getBoundingClientRect)
        tx = (s.line_x - ox) * DPR
        tt = (s.line_y - oy) * DPR
        tw = s.line_w * DPR
        th = s.line_h * DPR
        truth_markers = (
            f'<div class="tm" style="left:{tx:.1f}px;top:{tt:.1f}px"></div>'
            f'<div class="tm" style="left:{tx+tw-4:.1f}px;top:{tt:.1f}px"></div>'
            f'<div class="tm" style="left:{tx:.1f}px;top:{tt+th-4:.1f}px"></div>'
            f'<div class="tm" style="left:{tx+tw-4:.1f}px;top:{tt+th-4:.1f}px"></div>'
        )

        # Descender target: measure the specific char span bottom
        d_target = ""
        if s.descender_char and s.char_rects:
            targets = [c for c in s.char_rects if c.get("target")]
            if targets:
                tc = targets[0]
                dl = (tc["x"] - ox) * DPR
                dr = (tc["x"] + tc["w"] - ox) * DPR
                db = (tc["bottom"] - oy) * DPR
                d_target = (f'<div class="d-target" style="left:{dl:.1f}px;'
                            f'top:{db-0.5:.1f}px;width:{dr-dl:.1f}px;height:0"></div>')

        # Right-edge target: last 3 chars rightmost edge
        e_target = ""
        if s.is_edge and s.char_rects:
            targets = [c for c in s.char_rects if c.get("target")]
            if targets:
                rightmost = max(targets, key=lambda c: c["right"])
                rx = (rightmost["right"] - ox) * DPR
                ry = (rightmost["y"] - oy) * DPR
                rh = rightmost["h"] * DPR
                e_target = (f'<div class="e-target" style="left:{rx-0.5:.1f}px;'
                            f'top:{ry:.1f}px;width:0;height:{rh:.1f}px"></div>')

        w_img = dp_w
        h_img = dp_h
        is_desc = "desc" if s.descender_char else "edge"
        is_size_cls = ("small" if s.font_size_css <= 24
                       else "large" if s.font_size_css >= 56
                       else "")

        inspect_parts.append(
            f'<div class="sample" data-type="{is_desc} {is_size_cls}" '
            f'style="width:{w_img:.0f}px;height:{h_img:.0f}px">'
            f'<div class="info">{s.id}</div>'
            f'<img src="{rel_png}" width="{w_img:.0f}" height="{h_img:.0f}">'
            f'<div class="truth-marker" '
            f'style="left:0;top:0;width:{w_img:.0f}px;height:{h_img:.0f}px">'
            f'{"".join(truth_markers)}</div>'
            f'<div class="box-overlay" '
            f'style="left:0;top:0;width:{w_img:.0f}px;height:{h_img:.0f}px">'
            f'{"".join(overlay_divs)}</div>'
            f'{d_target}{e_target}'
            f'</div>\n'
        )

    inspect_parts.append("""
</div>
<script>
function filter(type) {
  document.querySelectorAll('.sticky button').forEach(b => b.classList.remove('active'));
  document.getElementById('btn_'+type).classList.add('active');
  document.querySelectorAll('.sample').forEach(el => {
    if (type === 'all') { el.classList.remove('hidden-sample'); return; }
    const types = el.getAttribute('data-type').split(' ');
    el.classList.toggle('hidden-sample', !types.includes(type));
  });
}
</script>
</body></html>
""")

    inspect_path = out_dir / "inspect.html"
    inspect_path.write_text("".join(inspect_parts), encoding="utf-8")
    print(f"\nOpen: {inspect_path}")
    print("  Ctrl+滚轮 缩放, 无限放大不模糊 (CSS outline, 非像素图)")


if __name__ == "__main__":
    main()
