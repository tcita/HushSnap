"""Check if unclip=1.3 clips text edges (right / bottom) vs unclip=1.6.

Renders English samples with per-character span markers, measures the
rightmost char's pixel-accurate extent, and compares OCR box right edge
against it.  Also checks bottom edge for descender characters.

Usage:
    python scripts/check_edge_clip.py
"""
from __future__ import annotations

import sys, tempfile
from collections import defaultdict
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

FONT_SIZES = [16, 18, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80]
FAMILIES = ["Arial", "Times New Roman", "Segoe UI", "Consolas"]
TEXT = "The quick brown fox jumps over the lazy dog"
DPR = 1.5
UNCLIP = [1.3, 1.6]
PAD = 8


def main():
    import logging
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)

    # ── Build samples ────────────────────────────────────────────────────
    samples = []
    for fs in FONT_SIZES:
        for fam in FAMILIES:
            samples.append({"id": f"{fam.replace(' ','_')}_{fs}",
                            "fs": fs, "fam": fam})

    html = "<!DOCTYPE html><html><head><meta charset=UTF-8><style>"
    html += "*{margin:0;padding:0}body{background:#fff}"
    html += ".c{padding:12px 16px}span.char{display:inline-block;line-height:1.2}"
    html += "</style></head><body>"
    for s in samples:
        chars = "".join(
            f'<span class="char" data-pos="{i}" '
            f'data-last="{"1" if i >= len(TEXT)-3 else "0"}">'
            f'{c}</span>'
            for i, c in enumerate(TEXT)
        )
        html += (f'<div class="c" style="font-size:{s["fs"]}px;'
                 f'font-family:\'{s["fam"]}\',sans-serif" '
                 f'data-id="{s["id"]}">{chars}</div>')
    html += "</body></html>"

    # ── Render & OCR ─────────────────────────────────────────────────────
    from playwright.sync_api import sync_playwright
    import cv2, numpy as np

    tmp = tempfile.mkdtemp(prefix="eclip_")
    hp = Path(tmp) / "s.html"; hp.write_text(html, encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(device_scale_factor=DPR)
        page = ctx.new_page()
        page.goto(f"file:///{hp.as_posix()}")
        page.wait_for_load_state("networkidle")
        page.set_viewport_size({"width": 2400, "height": 6000})

        # Measure case rects + last-3-char extents
        truth = page.evaluate("""() => {
            const cs = document.querySelectorAll('.c');
            const r = {};
            cs.forEach(el => {
                const id = el.getAttribute('data-id');
                const cr = el.getBoundingClientRect();
                const chars = [];
                el.querySelectorAll('span.char[data-last="1"]').forEach(s => {
                    const rr = s.getBoundingClientRect();
                    chars.push({right: rr.right, bottom: rr.bottom,
                                x: rr.x, w: rr.width, h: rr.height});
                });
                r[id] = {x: cr.x, y: cr.y, w: cr.width, h: cr.height,
                         last_chars: chars};
            });
            return r;
        }""")

        # Screenshot each case
        for s in samples:
            t = truth.get(s["id"]);
            if not t: continue
            p = Path(tmp) / f'{s["id"]}.png'
            cw, ch = t["w"] + PAD*2, t["h"] + PAD*2
            cx, cy = max(0, t["x"] - PAD), max(0, t["y"] - PAD)
            vp = page.viewport_size
            if vp:
                cw = min(cw, vp["width"] - cx)
                ch = min(ch, vp["height"] - cy)
            if cw <= 0 or ch <= 0: continue
            page.screenshot(path=str(p), clip={"x": cx, "y": cy,
                            "width": cw, "height": ch})
            s["png"] = str(p)
            s["truth"] = t

        ctx.close(); browser.close()

    # OCR
    from rapidocr import RapidOCR, OCRVersion, ModelType
    engine = RapidOCR(params={
        "Det.ocr_version": OCRVersion.PPOCRV6, "Det.model_type": ModelType.SMALL,
        "Global.use_cls": False, "Det.limit_side_len": 32,
        "Det.use_dilation": False,
        "Det.mean": [0.485,0.456,0.406], "Det.std": [0.229,0.224,0.225],
    })

    # Collect per-sample results
    results: dict[float, list[dict]] = defaultdict(list)

    for uval in UNCLIP:
        for s in samples:
            if "png" not in s: continue
            res = engine(s["png"], unclip_ratio=uval)
            txts = getattr(res, "txts", None)
            txts = list(txts) if txts is not None else []
            boxes_raw = getattr(res, "boxes", None)
            boxes_raw = list(boxes_raw) if boxes_raw is not None else []
            for box, txt in zip(boxes_raw, txts):
                if not txt or not txt.strip(): continue
                xs = [p[0] for p in box]; ys = [p[1] for p in box]
                l, r, t, b = min(xs), max(xs), min(ys), max(ys)
                if r-l <= 0 or b-t <= 0: continue

                t = s["truth"]
                ox, oy = t["x"] - PAD, t["y"] - PAD  # clip origin CSS px

                # Right edge of rightmost last-3 char (device px)
                if t["last_chars"]:
                    rightmost = max(c["right"] for c in t["last_chars"])
                    r_truth = (rightmost - ox) * DPR
                else:
                    r_truth = None

                # Bottom edge of lowest last-3 char (device px)
                if t["last_chars"]:
                    lowest = max(c["bottom"] for c in t["last_chars"])
                    b_truth = (lowest - oy) * DPR
                else:
                    b_truth = None

                results[uval].append({
                    "id": s["id"], "fs": s["fs"], "fam": s["fam"],
                    "box_right": r, "box_bottom": b,
                    "truth_right": r_truth, "truth_bottom": b_truth,
                    "r_gap": (r - r_truth) if r_truth is not None else None,
                    "b_gap": (b - b_truth) if b_truth is not None else None,
                })

    # ── Report ────────────────────────────────────────────────────────────
    import statistics

    print("=" * 85)
    print("RIGHT-EDGE CHECK  (box_right - truth_right, device px)")
    print(f"  truth = rightmost pixel of last 3 chars of '{TEXT}'")
    print("=" * 85)

    for uval in UNCLIP:
        gaps = [r["r_gap"] for r in results[uval] if r["r_gap"] is not None]
        if not gaps: continue
        n = len(gaps)
        clipped = [g for g in gaps if g < 0]
        sg = sorted(gaps)
        print(f"\n  unclip={uval}:  {'★ SAFE' if not clipped else f'⚠ {len(clipped)}/{n} CLIPPED'}")
        print(f"    mean={statistics.mean(gaps):+.1f}  median={statistics.median(gaps):+.1f}"
              f"  p5={sg[n//20]:+.1f}  min={sg[0]:+.1f}")

        if clipped:
            print(f"    Clipped samples:")
            for r in results[uval]:
                if r["r_gap"] is not None and r["r_gap"] < 0:
                    print(f"      {r['id']:35s}  gap={r['r_gap']:+.1f}px  "
                          f"box_right={r['box_right']:.1f}  truth={r['truth_right']:.1f}")

    print(f"\n{'='*85}")
    print("BOTTOM-EDGE CHECK  (box_bottom - truth_bottom, device px)")
    print(f"  truth = lowest pixel of last 3 chars (catches descenders: g, j, p, q, y)")
    print("=" * 85)

    for uval in UNCLIP:
        gaps = [r["b_gap"] for r in results[uval] if r["b_gap"] is not None]
        if not gaps: continue
        n = len(gaps)
        clipped = [g for g in gaps if g < 0]
        sg = sorted(gaps)
        print(f"\n  unclip={uval}:  {'★ SAFE' if not clipped else f'⚠ {len(clipped)}/{n} CLIPPED'}")
        print(f"    mean={statistics.mean(gaps):+.1f}  median={statistics.median(gaps):+.1f}"
              f"  p5={sg[n//20]:+.1f}  min={sg[0]:+.1f}")

        if clipped:
            print(f"    Clipped samples:")
            for r in results[uval]:
                if r["b_gap"] is not None and r["b_gap"] < 0:
                    print(f"      {r['id']:35s}  gap={r['b_gap']:+.1f}px  "
                          f"box_bottom={r['box_bottom']:.1f}  truth={r['truth_bottom']:.1f}")


if __name__ == "__main__":
    main()
