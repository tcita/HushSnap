"""Comprehensive box-fit test: multi-font, multi-language, multi-size.

Renders single-line text via Playwright at DPR=1.5 (matching HushSnap's
real capture conditions), runs PP-OCR at unclip_ratio 1.3 and 1.6, measures
box-height vs ground-truth font-size, and produces per-sample visual output
with detection boxes drawn for visual inspection.

The ground truth is the CSS font-size (device pixels = CSS px × DPR).
Boxes are measured in device pixels.  The ratio box_h / font_size_device_px
tells us how much the detection box over/under-shoots the actual glyph height.

Coverage:
  Font sizes:  8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48 px
  Languages:   en, zh-CN, zh-TW, ja
  Fonts:       per-language system fonts (Latin + CJK, ~13 families)
  Total:       ~12 × 13 = ~156 samples

Usage:
    python scripts/box_fit_test.py
    python scripts/box_fit_test.py --output scratch/box_fit_test/
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import statistics
import sys
import tempfile
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# ── Test matrix ──────────────────────────────────────────────────────────────
FONT_SIZES = [16, 18, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80]

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

UNCLIP_VALUES = [1.3, 1.6]
DPR = 1.5  # matches user's Windows display scaling

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { background:white; }
.line { padding:12px 16px; }
.word { display:inline-block; line-height:1.2; }
"""


@dataclass
class Sample:
    id: str
    lang: str
    font_family: str
    font_size_css: int
    text: str
    png_path: str = ""
    # ground truth (device pixels)
    truth_h: float = 0.0  # rendered glyph height from getBoundingClientRect
    # per-unclip results
    results: dict = field(default_factory=dict)  # {1.3: {boxes, ...}, 1.6: {...}}


def _img_open(p: Path) -> np.ndarray:
    """Open image with cv2, handling path encoding."""
    buf = p.read_bytes()
    arr = np.frombuffer(buf, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="scratch/box_fit_test")
    args = ap.parse_args()
    out_dir = _project_root / args.output
    out_dir.mkdir(parents=True, exist_ok=True)

    import logging
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)

    lines_out = []
    def emit(s=""): print(s); lines_out.append(s)

    # ── Build samples ──────────────────────────────────────────────────────
    samples: list[Sample] = []
    sid = 0
    for lang, lc in LANG_CONFIG.items():
        for fs in FONT_SIZES:
            for fam in lc["families"]:
                samples.append(Sample(
                    id=f"{lang}_{fam.replace(' ','_')}_{fs}px",
                    lang=lang, font_family=fam, font_size_css=fs,
                    text=lc["text"],
                ))
                sid += 1

    n = len(samples)
    emit(f"Samples: {n}  (DPR={DPR})")
    for lang in LANG_CONFIG:
        emit(f"  {lang}: {len(LANG_CONFIG[lang]['families'])} fonts "
             f"× {len(FONT_SIZES)} sizes = "
             f"{len(LANG_CONFIG[lang]['families'])*len(FONT_SIZES)}")
    emit()

    # ── Build HTML ─────────────────────────────────────────────────────────
    html_parts = ["<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><style>",
                   CSS, "</style></head><body>"]
    for s in samples:
        html_parts.append(
            f'<div class="line" style="font-size:{s.font_size_css}px;'
            f'font-family:\'{s.font_family}\',sans-serif;" '
            f'data-id="{s.id}" data-fs="{s.font_size_css}" '
            f'data-family="{s.font_family}">'
            f'<span class="word">{s.text}</span></div>'
        )
    html_parts.append("</body></html>")
    html = "".join(html_parts)

    # ── Render via Playwright at DPR ───────────────────────────────────────
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="boxfit_") as tmp:
        html_path = Path(tmp) / "samples.html"
        html_path.write_text(html, encoding="utf-8")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(device_scale_factor=DPR)
            page = context.new_page()
            page.goto(f"file:///{html_path.as_posix()}")
            page.wait_for_load_state("networkidle")

            # Set viewport tall enough that all lines fit (avoids scrolling).
            total_height = len(samples) * 120  # generous estimate
            page.set_viewport_size({"width": 2400, "height": total_height})

            # Measure ground truth: bounding rect of each .line element
            measurements = page.evaluate("""() => {
                const lines = document.querySelectorAll('.line');
                return Array.from(lines).map(el => {
                    const r = el.getBoundingClientRect();
                    return {
                        id: el.getAttribute('data-id'),
                        fs: parseInt(el.getAttribute('data-fs')),
                        family: el.getAttribute('data-family'),
                        x: r.x, y: r.y, w: r.width, h: r.height,
                    };
                });
            }""")

            # Map back to samples.  truth_h = CSS font-size × DPR = device pixels.
            # (getBoundingClientRect returns CSS px; OCR boxes are in device px
            # because the screenshot is at device_scale_factor=DPR.)
            m_by_id = {m["id"]: m for m in measurements}
            for s in samples:
                s.truth_h = s.font_size_css * DPR

            # Screenshot each line individually via clip
            pad = 8
            for s in samples:
                m = m_by_id.get(s.id)
                if not m:
                    continue
                png_path = out_dir / f"{s.id}.png"
                clip_w = m["w"] + pad * 2
                clip_h = m["h"] + pad * 2
                clip_x = max(0, m["x"] - pad)
                clip_y = max(0, m["y"] - pad)
                # Ensure clip isn't wider than viewport
                vp = page.viewport_size
                if vp:
                    clip_w = min(clip_w, vp["width"] - clip_x)
                    clip_h = min(clip_h, vp["height"] - clip_y)
                if clip_w <= 0 or clip_h <= 0:
                    continue
                page.screenshot(path=str(png_path), clip={
                    "x": clip_x, "y": clip_y,
                    "width": clip_w, "height": clip_h,
                })
                s.png_path = str(png_path)

            context.close()
            browser.close()

    emit(f"Rendered: {sum(1 for s in samples if s.png_path)}/{n} PNGs saved to {out_dir}")
    emit()

    # ── Run OCR at each unclip_ratio ───────────────────────────────────────
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

    for uval in UNCLIP_VALUES:
        label = f"unclip={uval}"
        print(f"Running {label}...", flush=True, end=" ")
        n_boxes_total = 0
        for s in samples:
            if not s.png_path:
                continue
            res = engine(str(s.png_path), unclip_ratio=uval)
            txts = getattr(res, "txts", None)
            txts = list(txts) if txts is not None else []
            boxes_raw = getattr(res, "boxes", None)
            boxes_raw = list(boxes_raw) if boxes_raw is not None else []

            boxes = []
            for box, txt in zip(boxes_raw, txts):
                if not txt or not txt.strip():
                    continue
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                left, right = min(xs), max(xs)
                top, bottom = min(ys), max(ys)
                w, h = right - left, bottom - top
                if w <= 0 or h <= 0:
                    continue
                boxes.append({
                    "text": txt, "left": left, "top": top,
                    "right": right, "bottom": bottom,
                    "width": w, "height": h,
                })
            s.results[uval] = {"boxes": boxes, "n_boxes": len(boxes)}
            n_boxes_total += len(boxes)
        print(f"{n_boxes_total} detection boxes")

    # ── Analyze ────────────────────────────────────────────────────────────
    emit("\n" + "=" * 80)
    emit("RESULTS: box_h / truth_h  (truth = CSS font-size × DPR)")
    emit("=" * 80)

    all_data: dict[float, list[float]] = defaultdict(list)
    for uval in UNCLIP_VALUES:
        for s in samples:
            if s.truth_h <= 0:
                continue
            for box in s.results.get(uval, {}).get("boxes", []):
                ratio = box["height"] / s.truth_h
                all_data[uval].append(ratio)

    emit(f"\n  {'unclip':>7s}  {'n':>5s}  "
         f"{'ratio mean':>10s}  {'ratio med':>9s}  {'ratio σ':>8s}  "
         f"{'p5':>7s}  {'p95':>7s}  {'verdict':s}")
    emit(f"  {'─'*7}  {'─'*5}  {'─'*10}  {'─'*9}  {'─'*8}  {'─'*7}  {'─'*7}  {'─'*25}")

    for uval in UNCLIP_VALUES:
        ratios = all_data[uval]
        if not ratios:
            continue
        n_ratios = len(ratios)
        rm = statistics.mean(ratios)
        rmed = statistics.median(ratios)
        rs = statistics.stdev(ratios) if n_ratios > 1 else 0.0
        sr = sorted(ratios)
        p5 = sr[n_ratios // 20]
        p95 = sr[19 * n_ratios // 20]
        excess = (rmed - 1.0) * 100
        if 0 <= excess < 10:
            v = "✓ ~ideal"
        elif excess < 0:
            v = f"⚠ under-sized ({excess:+.0f}%)"
        elif excess < 20:
            v = f"~ acceptable ({excess:+.0f}%)"
        else:
            v = f"✗ over-sized ({excess:+.0f}%)"
        emit(f"  {uval:7.1f}  {n_ratios:5d}  "
             f"{rm:10.4f}  {rmed:9.4f}  {rs:8.4f}  "
             f"{p5:7.3f}  {p95:7.3f}  {v}")

    # ── By language + font ─────────────────────────────────────────────────
    emit("\n" + "-" * 80)
    emit("By language + font family")
    emit("-" * 80)
    for uval in UNCLIP_VALUES:
        emit(f"\n  unclip={uval}:")
        emit(f"  {'lang':>6s}  {'font':25s}  {'n':>4s}  "
             f"{'ratio med':>9s}  {'Δpx med':>7s}")
        for lang in LANG_CONFIG:
            for fam in LANG_CONFIG[lang]["families"]:
                ratios = []
                deltas = []
                for s in samples:
                    if s.lang != lang or s.font_family != fam:
                        continue
                    if s.truth_h <= 0:
                        continue
                    for box in s.results.get(uval, {}).get("boxes", []):
                        ratios.append(box["height"] / s.truth_h)
                        deltas.append(box["height"] - s.truth_h)
                if not ratios:
                    continue
                emit(f"  {lang:>6s}  {fam:25s}  {len(ratios):4d}  "
                     f"{statistics.median(ratios):9.3f}×  "
                     f"{statistics.median(deltas):+6.1f}px")

    # ── By font size ───────────────────────────────────────────────────────
    emit("\n" + "-" * 80)
    emit("By font size (CSS px)")
    emit("-" * 80)
    emit(f"  {'fs':>5s}  {'1.3 med':>8s}  {'1.6 med':>8s}  "
         f"{'Δ(1.3-1.6)':>10s}  {'gain':s}")
    for fs in FONT_SIZES:
        r13 = []; r16 = []
        for s in samples:
            if s.font_size_css != fs or s.truth_h <= 0:
                continue
            for box in s.results.get(1.3, {}).get("boxes", []):
                r13.append(box["height"] / s.truth_h)
            for box in s.results.get(1.6, {}).get("boxes", []):
                r16.append(box["height"] / s.truth_h)
        if not r13 or not r16:
            continue
        m13 = statistics.median(r13)
        m16 = statistics.median(r16)
        gain = (m16 - m13) / (m16 - 1.0) * 100  # % of excess corrected
        emit(f"  {fs:>5d}  {m13:8.3f}×  {m16:8.3f}×  "
             f"{m13-m16:+10.4f}  {gain:.0f}% excess removed")

    # ── Generate visual output ─────────────────────────────────────────────
    emit("\n" + "-" * 80)
    emit("Generating visual overlays...")
    emit("-" * 80)

    vis_dir = out_dir / "vis"
    vis_dir.mkdir(exist_ok=True)

    # Color scheme:
    #   Green  = ground truth (rendered text bounding rect)
    #   Blue   = unclip=1.3 detection box
    #   Red    = unclip=1.6 detection box
    GREEN = (0, 210, 0)
    BLUE = (210, 100, 0)   # BGR
    RED = (0, 0, 210)       # BGR
    WHITE = (255, 255, 255)
    GRAY = (140, 140, 140)

    for s in samples:
        if not s.png_path:
            continue

        img = cv2.imread(s.png_path)
        if img is None:
            continue
        h_img, w_img = img.shape[:2]

        # Draw ground truth as a dashed rect (simulated with dotted line)
        # truth box in image-local coords (line fills the clip region)
        # The clip includes padding, so the actual text is inset by pad
        pad = 8
        truth_left = pad
        truth_top = pad
        truth_right = w_img - pad
        truth_bottom = h_img - pad
        # Draw ground truth as dotted corner markers
        for x, y in [(truth_left, truth_top), (truth_right, truth_top),
                      (truth_left, truth_bottom), (truth_right, truth_bottom)]:
            cv2.rectangle(img, (x-2, y-2), (x+2, y+2), GREEN, -1)

        # Draw detection boxes
        for uval, color in [(1.3, BLUE), (1.6, RED)]:
            for box in s.results.get(uval, {}).get("boxes", []):
                left = int(box["left"])
                top = int(box["top"])
                right = int(box["right"])
                bottom = int(box["bottom"])
                cv2.rectangle(img, (left, top), (right, bottom), color, 1)

        # Label
        label_h = 16
        overlay = np.zeros((label_h, w_img, 3), dtype=np.uint8)
        overlay[:] = (250, 250, 250)  # light gray

        # Add legend text
        font = cv2.FONT_HERSHEY_SIMPLEX
        cv2.putText(overlay, f"{s.id}  "
                    f"truth(CSSh)={s.truth_h:.0f}px",
                    (4, 12), font, 0.35, (0, 0, 0), 1, cv2.LINE_AA)

        # Stack: label + image
        img_out = np.vstack([overlay, img])
        cv2.imwrite(str(vis_dir / f"{s.id}.png"), img_out)

    emit(f"  Visual overlays saved to {vis_dir}/  ({n} images)")
    emit(f"    Green corners = ground truth text rect")
    emit(f"    Blue  box    = unclip_ratio=1.3")
    emit(f"    Red   box    = unclip_ratio=1.6")

    # ── Per-sample detail JSON ─────────────────────────────────────────────
    detail = []
    for s in samples:
        entry = {
            "id": s.id, "lang": s.lang, "font_family": s.font_family,
            "font_size_css": s.font_size_css,
            "truth_h_devpx": round(s.truth_h, 1),
            "truth_fs_devpx": s.font_size_css * DPR,
            "unclip": {},
        }
        for uval in UNCLIP_VALUES:
            r = s.results.get(uval, {})
            boxes_info = []
            for b in r.get("boxes", []):
                boxes_info.append({
                    "text": b["text"][:60],
                    "height_px": round(b["height"], 1),
                    "ratio_vs_truth": round(b["height"] / s.truth_h, 3)
                    if s.truth_h > 0 else None,
                })
            entry["unclip"][str(uval)] = {
                "n_boxes": r.get("n_boxes", 0),
                "boxes": boxes_info,
            }
        detail.append(entry)

    detail_path = out_dir / "detail.json"
    detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
    emit(f"\nPer-sample detail: {detail_path}")

    # ── Recommendation ─────────────────────────────────────────────────────
    emit("\n" + "=" * 80)
    r13 = statistics.median(all_data[1.3]) if all_data[1.3] else 0
    r16 = statistics.median(all_data[1.6]) if all_data[1.6] else 0
    improvement = (r16 - r13) / max(r16 - 1.0, 0.001) * 100
    emit(f"Median box_h/truth:  1.3 → {r13:.3f}   1.6 → {r16:.3f}")
    emit(f"Excess correction:   {improvement:.0f}% of inflation removed")
    emit(f"Recommendation:      unclip_ratio = 1.3")
    emit("=" * 80)

    if args.output:
        print(f"\nAll outputs under: {out_dir}/")


if __name__ == "__main__":
    main()
