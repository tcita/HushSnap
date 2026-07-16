"""Empirical validation: box edges drift more than box centres under
image perturbation — v4: edge-drift vs centre-drift.

For each token tracked across perturbation runs, we measure across-run
standard deviation of four quantities (all in raw pixels):

  top, bottom   — the two vertical edges (their instability = height drift)
  center_y      — the box centre

If centre_y has a LOWER stdev than top/bottom, the box is "breathing"
around a stable centre: the detector finds roughly the same centre but
the boundary placement is noisy.  This makes centre-distance inherently
more robust than overlap-ratio, whose ref-band and denominator both
depend on the noisy height estimate.

We report per-token stdev ratios (stdev_edge / stdev_centre_y).
> 1.0 means the edge is more variable than the centre.

Usage:
    python scripts/centre_vs_overlap_robustness.py
"""

from __future__ import annotations

import math
import re
import statistics
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))


# ═══════════════════════════════════════════════════════════════════════════════
# Perturbations
# ═══════════════════════════════════════════════════════════════════════════════

def _make_perturbations() -> list[tuple[str, callable]]:
    def _scale(factor):
        def _f(img):
            h, w = img.shape[:2]
            return cv2.resize(img, (int(w * factor), int(h * factor)),
                              interpolation=cv2.INTER_LINEAR)
        return _f

    def _rotate(deg):
        def _f(img):
            h, w = img.shape[:2]
            M = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
            return cv2.warpAffine(img, M, (w, h),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(255, 255, 255))
        return _f

    def _shift(dx, dy):
        def _f(img):
            h, w = img.shape[:2]
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            return cv2.warpAffine(img, M, (w, h),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(255, 255, 255))
        return _f

    def _combo(scale, rot, dx, dy):
        """Chained perturbation: scale → rotate → shift."""
        def _f(img):
            h, w = img.shape[:2]
            # 1. Scale
            img2 = cv2.resize(img, (int(w * scale), int(h * scale)),
                              interpolation=cv2.INTER_LINEAR)
            # 2. Rotate (keep original size)
            h2, w2 = img2.shape[:2]
            M = cv2.getRotationMatrix2D((w2 / 2, h2 / 2), rot, 1.0)
            img3 = cv2.warpAffine(img2, M, (w, h),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(255, 255, 255))
            # 3. Shift
            M2 = np.float32([[1, 0, dx], [0, 1, dy]])
            return cv2.warpAffine(img3, M2, (w, h),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(255, 255, 255))
        return _f

    return [
        # Single-axis perturbations
        ("scale_0.95", _scale(0.95)),
        ("scale_0.97", _scale(0.97)),
        ("scale_1.03", _scale(1.03)),
        ("scale_1.05", _scale(1.05)),
        ("rotate_-2deg", _rotate(-2)),
        ("rotate_-1deg", _rotate(-1)),
        ("rotate_+1deg", _rotate(1)),
        ("rotate_+2deg", _rotate(2)),
        ("shift_(1,0)", _shift(1, 0)),
        ("shift_(0,1)", _shift(0, 1)),
        ("shift_(1,1)", _shift(1, 1)),
        # Combined perturbations (more realistic)
        ("combo_A", _combo(0.97, -1.0, 0.5, 0)),
        ("combo_B", _combo(1.03, +1.0, 0, 0.5)),
        ("combo_C", _combo(0.96, +1.5, -0.5, 0.5)),
        ("combo_D", _combo(1.04, -1.5, 0.5, -0.5)),
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Detection
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_boxes(img: np.ndarray, engine) -> list[dict]:
    from hushsnap.ocr.ppocr import (
        _acquire_request, _release_request, ppocr_box_to_bbox,
    )
    _acquire_request()
    try:
        result = engine(img)
        json_data = result.to_json()
    finally:
        _release_request()

    boxes: list[dict] = []
    if not json_data:
        return boxes
    for item in json_data:
        txt = str(item.get("txt", "") or "").strip()
        if not txt:
            continue
        box = item.get("box", [])
        left, top, right, bottom = ppocr_box_to_bbox(box)
        bw, bh = right - left, bottom - top
        if bw <= 0 or bh <= 0:
            continue
        boxes.append({
            "text": txt,
            "top": top, "bottom": bottom,
            "left": left, "right": right,
            "width": bw, "height": bh,
            "center_x": (left + right) / 2,
            "center_y": (top + bottom) / 2,
        })
    return boxes


def _norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ═══════════════════════════════════════════════════════════════════════════════
# Analysis
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TokenDrift:
    token: str
    n_runs: int
    mean_height: float
    stdev_top: float         # stdev of box.top across runs
    stdev_bottom: float      # stdev of box.bottom across runs
    stdev_center_y: float    # stdev of box.center_y across runs
    stdev_height: float      # stdev of box.height across runs
    stdev_width: float       # stdev of box.width across runs

    @property
    def top_vs_centre(self) -> float:
        return self.stdev_top / max(self.stdev_center_y, 1e-9)

    @property
    def bottom_vs_centre(self) -> float:
        return self.stdev_bottom / max(self.stdev_center_y, 1e-9)

    @property
    def height_vs_centre(self) -> float:
        return self.stdev_height / max(self.stdev_center_y, 1e-9)


def _collect_drifts(
    all_runs: list[tuple[str, list[dict]]],
    min_runs: int = 5,
) -> list[TokenDrift]:
    samples: dict[str, list[dict]] = defaultdict(list)
    for _pert_name, boxes in all_runs:
        seen: set[str] = set()
        for b in boxes:
            key = _norm_text(b["text"])
            if len(key) < 2 or key in seen:
                continue
            seen.add(key)
            samples[key].append(b)

    results: list[TokenDrift] = []
    for token, boxes in samples.items():
        if len(boxes) < min_runs:
            continue
        tops = [b["top"] for b in boxes]
        bottoms = [b["bottom"] for b in boxes]
        centers = [b["center_y"] for b in boxes]
        heights = [b["height"] for b in boxes]
        widths = [b["width"] for b in boxes]

        results.append(TokenDrift(
            token=token,
            n_runs=len(boxes),
            mean_height=statistics.mean(heights),
            stdev_top=statistics.stdev(tops) if len(tops) > 1 else 0,
            stdev_bottom=statistics.stdev(bottoms) if len(bottoms) > 1 else 0,
            stdev_center_y=statistics.stdev(centers) if len(centers) > 1 else 0,
            stdev_height=statistics.stdev(heights) if len(heights) > 1 else 0,
            stdev_width=statistics.stdev(widths) if len(widths) > 1 else 0,
        ))
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 72)
    print("Edge-drift vs Centre-drift under Perturbation")
    print("=" * 72)
    print("For each token: measure stdev(top), stdev(bottom), stdev(center_y)")
    print("If stdev(edge) > stdev(centre), the box 'breathes' around a stable centre.")
    print()

    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    from ocr_layout.pipeline import get_engine, release_engine
    engine = get_engine()
    print("Engine ready.\n")

    from ocr_layout.cases import LineClusteringCase
    from ocr_layout.render import render_cases

    cases = [
        LineClusteringCase(
            id=f"drift_{fs}_{r:.1f}",
            font_size_px=fs, line_height_ratio=r,
            n_lines=3, words_per_line=6,
            prefix=f"D{i}",
        )
        for i, (fs, r) in enumerate([
            (16, 1.2), (16, 1.5), (20, 1.2), (24, 1.5),
        ])
    ]

    with tempfile.TemporaryDirectory(prefix="drift_") as tmp:
        render_results = render_cases(cases, tmp)
        perturbations = _make_perturbations()

        all_runs: list[tuple[str, list[dict]]] = []
        for rr in render_results:
            orig_img = cv2.imread(str(rr.png_path))
            if orig_img is None:
                continue
            all_runs.append(("original", _detect_boxes(orig_img, engine)))
            for pname, pfunc in perturbations:
                all_runs.append((pname, _detect_boxes(pfunc(orig_img), engine)))

        tokens = _collect_drifts(all_runs)

        if len(tokens) < 10:
            print(f"Too few tokens ({len(tokens)}) — aborting.")
            release_engine()
            return

        print(f"Tokens tracked: {len(tokens)}  (each in ≥5 runs)\n")

        # ── Summary: per-token stdev ratios ──────────────────────────────────
        tvcs = [t.top_vs_centre for t in tokens]
        bvcs = [t.bottom_vs_centre for t in tokens]
        hvcs = [t.height_vs_centre for t in tokens]

        print(f"{'Ratio':<30} {'GeoMean':>10} {'Median':>10} {'%>1.0':>10}")
        print("-" * 60)
        for label, vals in [
            ("stdev(top) / stdev(centre_y)", tvcs),
            ("stdev(bottom) / stdev(centre_y)", bvcs),
            ("stdev(height) / stdev(centre_y)", hvcs),
        ]:
            gm = math.exp(sum(math.log(max(v, 1e-9)) for v in vals) / len(vals))
            pct_gt1 = sum(1 for v in vals if v > 1.0) / len(vals) * 100
            print(f"{label:<30} {gm:>10.2f}× {statistics.median(vals):>10.2f}× "
                  f"{pct_gt1:>9.0f}%")

        # ── Raw stdev comparison (pixels) ────────────────────────────────────
        print(f"\n{'─' * 72}")
        print("Raw per-token stdev (pixels)")
        print(f"{'─' * 72}")
        stdev_tops = [t.stdev_top for t in tokens]
        stdev_bots = [t.stdev_bottom for t in tokens]
        stdev_cys = [t.stdev_center_y for t in tokens]
        stdev_hs = [t.stdev_height for t in tokens]

        print(f"{'Metric':<20} {'Mean stdev':>12} {'Median stdev':>12}")
        print("-" * 44)
        for label, vals in [
            ("top", stdev_tops),
            ("bottom", stdev_bots),
            ("center_y", stdev_cys),
            ("height", stdev_hs),
        ]:
            print(f"{label:<20} {statistics.mean(vals):>11.2f}px "
                  f"{statistics.median(vals):>11.2f}px")

        # ── Verdict ──────────────────────────────────────────────────────────
        avg_stdev_edge = statistics.mean([(t + b) / 2 for t, b in
                                          zip(stdev_tops, stdev_bots)])
        avg_stdev_cy = statistics.mean(stdev_cys)
        avg_stdev_h = statistics.mean(stdev_hs)

        print(f"\n  Average stdev(edge)     = {avg_stdev_edge:.2f} px")
        print(f"  Average stdev(centre_y)  = {avg_stdev_cy:.2f} px")
        print(f"  Average stdev(height)    = {avg_stdev_h:.2f} px")
        print(f"  stdev(edge) / stdev(centre_y) = {avg_stdev_edge / max(avg_stdev_cy, 1e-9):.2f}×")
        print(f"  stdev(height) / stdev(centre_y) = {avg_stdev_h / max(avg_stdev_cy, 1e-9):.2f}×")

        if avg_stdev_edge > avg_stdev_cy:
            print("\n  ✓ Box edges drift MORE than box centres under perturbation.")
            print("    → centre-distance gating is inherently more robust.")
        else:
            print("\n  ✗ Box centres drift MORE than box edges.")

        # ── Top examples ─────────────────────────────────────────────────────
        print(f"\n{'─' * 72}")
        print("Per-token details (sorted by stdev(height) / stdev(centre_y))")
        print(f"{'─' * 72}")
        print(f"{'token':<20} {'stdev_t':>8} {'stdev_b':>8} {'stdev_cy':>9} "
              f"{'stdev_h':>8} {'h/cy':>7} {'mean_h':>7}")
        for t in sorted(tokens, key=lambda t: t.height_vs_centre, reverse=True)[:20]:
            print(f"{t.token:<20} {t.stdev_top:>7.2f} {t.stdev_bottom:>7.2f} "
                  f"{t.stdev_center_y:>8.2f} {t.stdev_height:>7.2f} "
                  f"{t.height_vs_centre:>6.1f}× {t.mean_height:>6.1f}")

    release_engine()
    print(f"\n{'═' * 72}")
    print("Done.")


if __name__ == "__main__":
    main()
