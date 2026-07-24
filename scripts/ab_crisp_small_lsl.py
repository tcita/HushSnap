"""Sweep Det.limit_side_len on the CRISP small-content dataset.

Tests the real HushSnap scenario: small AREA + NORMAL font (dpr=1.5, glyphs
18-24px, image short side 20-38px - e.g. "测试" at 9pt = ~36x22px, readable to
humans).  This is the valid probe for whether limit_side_len=64 (no upscale)
collapses on tiny-but-readable screenshots, and whether 736 over-upscale hurts
vs 320.

Buckets by the image's ACTUAL short side (min dimension), not by a target.
Runs configs [64,128,256,320,480,736]; mean/std=[0.5,0.5,0.5], use_dilation
=true, use_cls=false identical -> any difference is the short-side resize
threshold.

Prints per-bucket meanCER (* = best), overall, collapse cases (64 >> 736, with
concrete diff ops so the error shape - empty output vs dropped space - is
visible), and cases where 64 beats 736 (over-upscale hurts).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ab_det_normalize import _build_params, normalize_text, cer, levenshtein  # noqa: E402
from ab_det_limit_side_len import diff_ops  # noqa: E402

_project_root = Path(__file__).resolve().parent.parent

CANDIDATES = [64, 128, 256, 320, 480, 736]
BASELINE = 736

# Short-side bins (px) - actual min dimension of the cropped image.
BINS = [(0, 25, "<25"), (25, 35, "25-35"), (35, 50, "35-50"),
        (50, 70, "50-70"), (70, 100, "70-100"), (100, 150, "100-150"),
        (150, 10**9, ">150")]


def _bin(ss):
    for lo, hi, lbl in BINS:
        if lo <= ss < hi:
            return lbl
    return ">150"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="scratch/crisp_small_dataset")
    args = ap.parse_args()

    ds = _project_root / args.dataset
    manifest = json.loads((ds / "manifest.json").read_text(encoding="utf-8"))
    print(f"Dataset: {ds}  ({len(manifest)} images)")

    from rapidocr import RapidOCR
    engines = {}
    for lsl in CANDIDATES:
        base = _build_params({"Det.mean": [0.5, 0.5, 0.5], "Det.std": [0.5, 0.5, 0.5]})
        base["Det.limit_side_len"] = lsl
        engines[lsl] = RapidOCR(params=base)
        print(f"  engine lsl={lsl} ready")

    rows = {lsl: [None] * len(manifest) for lsl in CANDIDATES}
    t0 = time.perf_counter()
    for i, item in enumerate(manifest):
        png = ds / item["meta"]["png"]
        truth = normalize_text(item["truth"])
        for lsl in CANDIDATES:
            res = engines[lsl](str(png))
            txts = getattr(res, "txts", None) or ()
            txt = normalize_text("".join(txts))
            rows[lsl][i] = (truth, txt, cer(txt, truth))
        if (i + 1) % 40 == 0:
            print(f"  ...{i+1}/{len(manifest)}  ({time.perf_counter()-t0:.0f}s)")

    short_sides = [min(m["meta"]["size"]) for m in manifest]

    # ── per short-side bin ────────────────────────────────────────────────
    print("\n" + "=" * 116)
    print("By ACTUAL short side  (each config meanCER; * = best in row)")
    print("=" * 116)
    hdr = f"{'bucket':>9s} {'n':>3s} " + " ".join(f"{l:>9d}" for l in CANDIDATES) \
          + "   64vs736  320vs736"
    print(hdr)
    for lo, hi, lbl in BINS:
        idxs = [i for i, ss in enumerate(short_sides) if lo <= ss < hi]
        if not idxs:
            continue
        means = {lsl: statistics.mean(rows[lsl][i][2] for i in idxs)
                 for lsl in CANDIDATES}
        best = min(means, key=lambda k: means[k])
        cells = [f"{means[lsl]:8.4f}{'*' if lsl==best else ' '}" for lsl in CANDIDATES]
        d64 = means[64] - means[736]
        d320 = means[320] - means[736]
        print(f"{lbl:>9s} {len(idxs):3d} " + " ".join(cells) +
              f"   {d64:+.4f}   {d320:+.4f}")

    # ── overall ──────────────────────────────────────────────────────────
    print("\n" + "=" * 116)
    print("OVERALL")
    print("=" * 116)
    for lsl in CANDIDATES:
        m = statistics.mean(r[2] for r in rows[lsl])
        print(f"  lsl={lsl:4d}  meanCER={m:.4f}")

    # ── collapse cases: 64 much worse than 736 ───────────────────────────
    print("\n" + "=" * 116)
    print("Collapse cases (64 CER - 736 CER > 0.15; show error shape)")
    print("=" * 116)
    cases = sorted([(rows[64][i][2] - rows[736][i][2], i)
                    for i in range(len(manifest))], key=lambda x: -x[0])
    shown = [c for c in cases if c[0] > 0.15][:12]
    if not shown:
        print("  (none - 64 never worse than 736 by >0.15)")
    for gain, i in shown:
        item = manifest[i]
        print(f"\n  {item['id']}  type={item['meta'].get('type','')} "
              f"size={item['meta']['size']} fs={item['meta'].get('font_size')} "
              f"(64CER={rows[64][i][2]:.3f} 736CER={rows[736][i][2]:.3f})")
        print(f"      truth: {rows[64][i][0][:90]}")
        for lsl in [64, 320, 736]:
            truth, txt, c = rows[lsl][i]
            ops = diff_ops(truth, txt)
            print(f"      lsl={lsl:4d} CER={c:.3f}  diff: "
                  f"{'  '.join(ops)[:160] if ops else '(exact)'}")
            print(f"             OUT: {txt[:90]}")

    # ── 64 beats 736 (over-upscale hurts) ───────────────────────────────
    print("\n" + "=" * 116)
    print("Cases where 64 BEATS 736 (736 CER - 64 CER > 0.05)")
    print("=" * 116)
    cases2 = sorted([(rows[736][i][2] - rows[64][i][2], i)
                     for i in range(len(manifest))], key=lambda x: -x[0])
    shown2 = [c for c in cases2 if c[0] > 0.05][:10]
    if not shown2:
        print("  (none)")
    for gain, i in shown2:
        item = manifest[i]
        print(f"\n  {item['id']}  type={item['meta'].get('type','')} "
              f"size={item['meta']['size']} fs={item['meta'].get('font_size')}")
        print(f"      truth: {rows[64][i][0][:90]}")
        for lsl in [64, 736]:
            truth, txt, c = rows[lsl][i]
            ops = diff_ops(truth, txt)
            print(f"      lsl={lsl:4d} CER={c:.3f}  diff: "
                  f"{'  '.join(ops)[:160] if ops else '(exact)'}")
            print(f"             OUT: {txt[:90]}")


if __name__ == "__main__":
    main()
