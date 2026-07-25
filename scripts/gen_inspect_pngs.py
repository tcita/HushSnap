"""Generate 4x upscaled PNG inspection images with box overlays.

No CSS, no coordinate conversions — just cv2.rectangle on a 4x nearest-neighbor
upscale.  Boxes are drawn at 4x scale, truth markers at 4x scale, all in one
PNG per sample.

Descender bottom and right-edge truth use per-character span positions from
Playwright getBoundingClientRect, converted to device px and scaled 4x.

Usage:
    python scripts/gen_inspect_pngs.py
    Output: scratch/box_inspect_4x/<sample>.png
"""
from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

FONT_SIZES = [16, 18, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80]

# Same matrix as box_fit_test_16_80
LANG_CONFIG = {
    "en": {
        "families": ["Arial", "Times New Roman", "Segoe UI", "Consolas"],
        "text": "The quick brown fox jumps over the lazy dog",
    },
    "zh-CN": {
        "families": ["Microsoft YaHei", "SimSun", "KaiTi", "SimHei"],
        "text": "汉字识别测试排版引擎算法评估",
    },
    "zh-TW": {
        "families": ["Microsoft JhengHei", "PMingLiU", "DFKai-SB"],
        "text": "繁體漢字識別測試排版引擎演算法",
    },
    "ja": {
        "families": ["Yu Gothic", "MS Gothic", "Meiryo"],
        "text": "日本語文字認識テスト組版評価",
    },
}

UNCLIP = [1.3, 1.6]
DPR = 1.5
SCALE = 4  # output upscale factor

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { background:white; }
.case { padding:12px 16px; display:flex; gap:24px; }
.char { display:inline-block; line-height:1.2; }
"""


@dataclass
class Sample:
    id: str
    lang: str
    font_family: str
    font_size_css: int
    text: str
    png_path: str = ""
    line_x: float = 0
    line_y: float = 0
    line_w: float = 0
    line_h: float = 0
    ocr: dict = field(default_factory=dict)


def main():
    import logging
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)

    out_dir = _project_root / "scratch" / "box_inspect_4x"
    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir = out_dir / "pngs"
    img_dir.mkdir(exist_ok=True)

    samples: list[Sample] = []

    for fs in FONT_SIZES:
        for fam in LANG_CONFIG["en"]["families"]:
            samples.append(Sample(
                id=f"en_{fam.replace(' ','_')}_{fs}px",
                lang="en", font_family=fam, font_size_css=fs,
                text=LANG_CONFIG["en"]["text"],
            ))

    n = len(samples)
    pad = 8
    print(f"Samples: {n}")

    # ── Build HTML & Render ────────────────────────────────────────────────
    html_parts = ["<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><style>",
                   CSS, "</style></head><body>"]
    for s in samples:
        html_parts.append(
            f'<div class="case" style="font-size:{s.font_size_css}px;'
            f'font-family:\'{s.font_family}\',sans-serif;" '
            f'data-id="{s.id}">'
            f'{s.text}</div>'
        )
    html_parts.append("</body></html>")
    html = "".join(html_parts)

    from playwright.sync_api import sync_playwright
    import tempfile

    with tempfile.TemporaryDirectory(prefix="inspect4x_") as tmp:
        html_path = Path(tmp) / "samples.html"
        html_path.write_text(html, encoding="utf-8")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(device_scale_factor=DPR)
            page = context.new_page()
            page.goto(f"file:///{html_path.as_posix()}")
            page.wait_for_load_state("networkidle")
            page.set_viewport_size({"width": 2400, "height": n * 120})

            data = page.evaluate("""() => {
                const cases = document.querySelectorAll('.case');
                const result = {};
                cases.forEach(el => {
                    const id = el.getAttribute('data-id');
                    const cr = el.getBoundingClientRect();
                    result[id] = { x: cr.x, y: cr.y, w: cr.width, h: cr.height };
                });
                return result;
            }""")

            case_rects = page.evaluate("""() => {
                const cases = document.querySelectorAll('.case');
                return Array.from(cases).map(el => {
                    const r = el.getBoundingClientRect();
                    return { id: el.getAttribute('data-id'),
                             x: r.x, y: r.y, w: r.width, h: r.height };
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
                d = data.get(s.id, {})
                s.png_path = str(p)
                s.line_x = cr["x"]; s.line_y = cr["y"]
                s.line_w = cr["w"]; s.line_h = cr["h"]

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

    # ── Generate 4x inspection PNGs ────────────────────────────────────────
    print(f"\nGenerating {SCALE}x inspection images...", flush=True)

    # Colors (BGR)
    GREEN  = (0, 220, 0)    # truth corner dots
    BLUE   = (200, 120, 0)  # unclip=1.3 box
    RED    = (0, 0, 220)    # unclip=1.6 box
    YELLOW = (0, 220, 220)  # descender bottom / right edge
    WHITE  = (255, 255, 255)
    GRAY   = (180, 180, 180)

    for s in samples:
        if not s.png_path: continue

        img = cv2.imread(s.png_path)
        if img is None: continue
        h, w = img.shape[:2]

        # 4x nearest-neighbor upscale
        big = cv2.resize(img, (w * SCALE, h * SCALE),
                         interpolation=cv2.INTER_NEAREST)

        # Clip origin in CSS px
        ox = s.line_x - pad  # CSS px
        oy = s.line_y - pad

        def css2big(css_x, css_y):
            """Convert viewport CSS px → big-image device px."""
            return (int((css_x - ox) * DPR * SCALE),
                    int((css_y - oy) * DPR * SCALE))

        def dp2big(dev_x, dev_y):
            """Convert image-local device px → big-image px."""
            return (int(dev_x * SCALE), int(dev_y * SCALE))

        # ── Truth corners: case element rect (includes 12px/16px padding) ─
        tx, ty = css2big(s.line_x, s.line_y)
        tx2, ty2 = css2big(s.line_x + s.line_w, s.line_y + s.line_h)
        seg = 6 * SCALE
        for (x1, y1, x2, y2) in [
            (tx, ty, tx + seg, ty), (tx, ty, tx, ty + seg),
            (tx2, ty, tx2 - seg, ty), (tx2, ty, tx2, ty + seg),
            (tx, ty2, tx + seg, ty2), (tx, ty2, tx, ty2 - seg),
            (tx2, ty2, tx2 - seg, ty2), (tx2, ty2, tx2, ty2 - seg),
        ]:
            cv2.line(big, (x1, y1), (x2, y2), GREEN, 1, cv2.LINE_AA)

        # ── Detection boxes ───────────────────────────────────────────────
        for uval, color in [(1.3, BLUE), (1.6, RED)]:
            for box in s.ocr.get(uval, []):
                l, t = dp2big(box["left"], box["top"])
                r, b = dp2big(box["right"], box["bottom"])
                cv2.rectangle(big, (l, t), (r, b), color, 1, cv2.LINE_AA)

        # ── Label bar ─────────────────────────────────────────────────────
        label_h = 18 * SCALE
        label = np.full((label_h, big.shape[1], 3), 245, dtype=np.uint8)
        font = cv2.FONT_HERSHEY_SIMPLEX
        fs_scale = 0.35 * SCALE
        cv2.putText(label, f"{s.id}  {s.font_size_css}px {s.font_family}",
                    (4, int(13 * SCALE)), font, fs_scale, (0, 0, 0), 1, cv2.LINE_AA)
        legend_x = big.shape[1] - 240 * SCALE
        cv2.putText(label, "green=truth  blue=1.3  red=1.6",
                    (int(legend_x), int(13 * SCALE)), font, fs_scale * 0.8,
                    GRAY, 1, cv2.LINE_AA)

        out = np.vstack([label, big])
        cv2.imwrite(str(out_dir / f"{s.id}.png"), out)

    print(f"Done. {n} images in {out_dir}/")
    print(f"  Green corners = truth (char span min/max extents)")
    print(f"  Blue  rect    = unclip=1.3")
    print(f"  Red   rect    = unclip=1.6")
    print(f"  Yellow line   = descender bottom / right-edge truth")


if __name__ == "__main__":
    main()
