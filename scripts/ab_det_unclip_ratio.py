"""A/B test: Det.unclip_ratio 1.6 (rapidocr default) vs 2.0 (PaddleOCR default).

Question: rapidocr defaults unclip_ratio=1.6; PaddleOCR defaults 2.0
(db_postprocess.py:37).  unclip_ratio is the Vatti-clipping expansion factor in
DB post-processing — higher = boxes expand more, potentially capturing more of
the text region but risking over-expansion into adjacent lines/boxes.  On
desktop screenshots (clean, regular text spacing), which value performs better?

Variable isolation: ONLY unclip_ratio varies.  Det.limit_side_len=32,
Det.mean/std=ImageNet, Det.use_dilation=False, Global.use_cls=False identical
in both (= current production det path).  Same engine instance, unclip_ratio
toggled per-call via engine(img, unclip_ratio=...).

Evaluation:
  - Equal-weight CER (no char/punct/emoji weighting).
  - PAIRED per image (same image under both configs).
  - Bucketed by category, size_tier, short side, lang.
  - Box-count comparison (higher unclip may merge/fuse adjacent boxes).
  - Full per-image detail saved to unclip_ratio_detail.json.

Dataset: scratch/desktop_dataset (gen_normalize_dataset.py, dpr=1.5,
real font sizes, 6 cats x 3 size tiers x CJK/Latin, ~480 images).

Usage:
    python scripts/ab_det_unclip_ratio.py
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from pathlib import Path

# Silence rapidocr INFO logs BEFORE importing.
logging.getLogger("RapidOCR").setLevel(logging.WARNING)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ab_det_normalize import _build_params, normalize_text, cer, levenshtein  # noqa: E402
from ab_det_limit_side_len import diff_ops  # noqa: E402

_project_root = Path(__file__).resolve().parent.parent

CONFIGS = [("1.6", 1.6), ("2.0", 2.0)]
BASELINE = "1.6"  # current HushSnap production (rapidocr default)

BINS = [(0, 50, "<50"), (50, 100, "50-100"), (100, 200, "100-200"),
        (200, 400, "200-400"), (400, 10**9, ">400")]


def _bin(ss):
    for lo, hi, lbl in BINS:
        if lo <= ss < hi:
            return lbl
    return ">400"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="scratch/desktop_dataset")
    ap.add_argument("--report", default="", help="also write report to this file")
    args = ap.parse_args()

    ds = _project_root / args.dataset
    manifest = json.loads((ds / "manifest.json").read_text(encoding="utf-8"))
    n = len(manifest)
    print(f"Dataset: {ds}  ({n} images)")

    lines_out = []
    def emit(s=""):
        print(s); lines_out.append(s)

    emit("=" * 100)
    emit("A/B: unclip_ratio  1.6 (rapidocr default / HushSnap) vs 2.0 (PaddleOCR)")
    emit("  Only unclip_ratio varies; limit_side_len=32, mean/std=ImageNet,")
    emit("  use_dilation=False, use_cls=False identical -> difference = unclip_ratio.")
    emit("=" * 100)

    from rapidocr import RapidOCR
    base = _build_params({"Det.mean": [0.485, 0.456, 0.406],
                           "Det.std": [0.229, 0.224, 0.225]})
    base["Det.limit_side_len"] = 32
    base["Det.use_dilation"] = False
    t0 = time.perf_counter()
    engine = RapidOCR(params=base)
    emit(f"  engine ready ({(time.perf_counter()-t0)*1000:.0f}ms) — "
         f"unclip_ratio toggled per-call")

    def getarr(res, attr):
        v = getattr(res, attr, None)
        return list(v) if v is not None else []

    # rows[name][i] = (truth, txt, cer, n_boxes, txts_list, diff_ops_list)
    rows = {name: [None] * n for name, _ in CONFIGS}
    t_start = time.perf_counter()
    for i, item in enumerate(manifest):
        png = str(ds / item["meta"]["png"])
        truth = normalize_text(item["truth"])
        for name, val in CONFIGS:
            res = engine(png, unclip_ratio=val)
            txts = getarr(res, "txts")
            txt = normalize_text("".join(txts))
            ops = diff_ops(truth, txt)
            rows[name][i] = (truth, txt, cer(txt, truth), len(txts), txts, ops)
        if (i + 1) % 60 == 0:
            print(f"  ...{i+1}/{n}  ({time.perf_counter()-t_start:.0f}s)", flush=True)
    elapsed = time.perf_counter() - t_start
    emit(f"\nRan {n} images x {len(CONFIGS)} configs in {elapsed:.0f}s\n")

    # ── Overall (paired) ─────────────────────────────────────────────────
    emit("=" * 100)
    emit(f"OVERALL  (baseline = unclip_ratio={BASELINE})")
    emit("=" * 100)
    means = {}
    for name, _ in CONFIGS:
        m = statistics.mean(r[2] for r in rows[name])
        means[name] = m
        emit(f"  unclip_ratio={name}  meanCER={m:.4f}")
    base_m = means[BASELINE]
    other_name = [n for n, _ in CONFIGS if n != BASELINE][0]
    diff = means[other_name] - base_m
    # paired win/lose/tie
    w = l = t = 0
    for i in range(n):
        cb = rows[BASELINE][i][2]
        co = rows[other_name][i][2]
        if co < cb - 1e-9: w += 1
        elif co > cb + 1e-9: l += 1
        else: t += 1
    emit(f"  vs {other_name}: Δ={diff:+.4f}  ({other_name} better on {w}, "
         f"worse on {l}, tie {t})")
    # Wilcoxon-style sign summary
    deltas = [rows[other_name][i][2] - rows[BASELINE][i][2] for i in range(n)]
    nonzero = [d for d in deltas if abs(d) > 1e-9]
    pos = sum(1 for d in nonzero if d > 0)   # other worse than baseline
    neg = sum(1 for d in nonzero if d < 0)   # other better than baseline
    emit(f"  paired non-tie: {other_name} better={neg}  worse={pos}  "
         f"(sign test two-sided p ~ {2*min(pos,neg) if nonzero else 0} / {len(nonzero)})")
    base_val = dict(CONFIGS)[BASELINE]
    other_val = dict(CONFIGS)[other_name]
    if diff < -0.002:
        verdict = f"{other_name} (unclip_ratio={other_val}) is better by {-diff:.4f}"
    elif diff > 0.002:
        verdict = f"{BASELINE} (unclip_ratio={base_val}) is better by {diff:.4f}"
    else:
        verdict = f"within noise (|Δ|={abs(diff):.4f} < 0.002); keep {BASELINE} (production)"
    emit(f"\n  >>> overall: {verdict}")

    # ── Failure-shape analysis ───────────────────────────────────────────
    emit("\n" + "-" * 100)
    emit("Failure-shape analysis (images where 1.6 != 2.0, n=%d)" % n)
    emit("-" * 100)
    nb_16 = [rows["1.6"][i][3] for i in range(n)]
    nb_20 = [rows["2.0"][i][3] for i in range(n)]
    same_box = sum(1 for a, b in zip(nb_16, nb_20) if a == b)
    r16_more = sum(1 for a, b in zip(nb_16, nb_20) if a > b)
    r20_more = sum(1 for a, b in zip(nb_16, nb_20) if b > a)
    emit(f"  box count: 1.6==2.0 on {same_box}/{n}  "
         f"(1.6 more boxes: {r16_more} / 2.0 more boxes: {r20_more})")
    emit(f"  total boxes  1.6={sum(nb_16)}  2.0={sum(nb_20)}")
    # empty-output
    r16_empty = [i for i in range(n) if rows["1.6"][i][2] >= 0.99 and not rows["1.6"][i][1].strip()]
    r20_empty = [i for i in range(n) if rows["2.0"][i][2] >= 0.99 and not rows["2.0"][i][1].strip()]
    emit(f"  empty output (det miss): 1.6={len(r16_empty)}  2.0={len(r20_empty)}")
    # box-count divergence
    big_box_diff = sorted(
        [(abs(nb_16[i]-nb_20[i]), i) for i in range(n) if nb_16[i] != nb_20[i]],
        key=lambda x: -x[0])
    if big_box_diff:
        emit(f"\n  Top box-count divergences (unclip_ratio merge/split effect):")
        for _, i in big_box_diff[:6]:
            item = manifest[i]
            r16 = rows["1.6"][i]; r20 = rows["2.0"][i]
            emit(f"    {item['id']} ({item['category']} short={min(item['meta']['size'])})  "
                 f"1.6:{r16[3]}boxes CER={r16[2]:.3f}  vs  2.0:{r20[3]}boxes CER={r20[2]:.3f}")
            emit(f"      truth: {r16[0][:85]}")
            emit(f"      1.6 boxes: {r16[4]}")
            emit(f"      2.0 boxes: {r20[4]}")
    # detection-flip
    flip = [(i, "1.6" if not rows["1.6"][i][1].strip() and rows["2.0"][i][1].strip() else "2.0")
            for i in range(n)
            if (not rows["1.6"][i][1].strip()) != (not rows["2.0"][i][1].strip())]
    if flip:
        emit(f"\n  Detection-flip cases (one empty, other reads text): {len(flip)}")
        for i, empty_side in flip[:6]:
            item = manifest[i]
            r16 = rows["1.6"][i]; r20 = rows["2.0"][i]
            full = "2.0" if empty_side == "1.6" else "1.6"
            emit(f"    {item['id']} ({item['category']} short={min(item['meta']['size'])})  "
                 f"EMPTY={empty_side}  READS={full}")
            emit(f"      truth: {r16[0][:85]}")
            out = r20[1] if empty_side == "1.6" else r16[1]
            emit(f"      {full:5s} OUT: {out[:85]}")

    # ── By category ───────────────────────────────────────────────────────
    emit("\n" + "-" * 100)
    emit("By category  (meanCER per config; Δ = 2.0 - 1.6)")
    emit("-" * 100)
    cats = sorted(set(m["category"] for m in manifest))
    emit(f"  {'cat':9s} {'n':>3s} {'1.6':>9s} {'2.0':>9s} {'Δ':>9s}  {'2.0 better':>12s}")
    for cat in cats:
        idxs = [i for i, m in enumerate(manifest) if m["category"] == cat]
        m16 = statistics.mean(rows["1.6"][i][2] for i in idxs)
        m20 = statistics.mean(rows["2.0"][i][2] for i in idxs)
        wb = sum(1 for i in idxs if rows["2.0"][i][2] < rows["1.6"][i][2] - 1e-9)
        emit(f"  {cat:9s} {len(idxs):3d} {m16:9.4f} {m20:9.4f} {m20-m16:+9.4f}  {wb:>5d}/{len(idxs)}")

    # ── By size tier ──────────────────────────────────────────────────────
    emit("\n" + "-" * 100)
    emit("By size_tier  (short=1 line, medium, long=multi-para)")
    emit("-" * 100)
    emit(f"  {'tier':8s} {'n':>3s} {'1.6':>9s} {'2.0':>9s} {'Δ':>9s}  {'2.0 better':>12s}")
    for tier in _tiers(manifest):
        idxs = [i for i, m in enumerate(manifest) if m["meta"].get("size_tier") == tier]
        if not idxs: continue
        m16 = statistics.mean(rows["1.6"][i][2] for i in idxs)
        m20 = statistics.mean(rows["2.0"][i][2] for i in idxs)
        wb = sum(1 for i in idxs if rows["2.0"][i][2] < rows["1.6"][i][2] - 1e-9)
        emit(f"  {tier:8s} {len(idxs):3d} {m16:9.4f} {m20:9.4f} {m20-m16:+9.4f}  {wb:>5d}/{len(idxs)}")

    # ── By short-side bin ─────────────────────────────────────────────────
    emit("\n" + "-" * 100)
    emit("By ACTUAL short side  (px)")
    emit("-" * 100)
    emit(f"  {'bucket':>9s} {'n':>3s} {'1.6':>9s} {'2.0':>9s} {'Δ':>9s}  {'2.0 better':>12s}")
    short_sides = [min(m["meta"]["size"]) for m in manifest]
    for lo, hi, lbl in BINS:
        idxs = [i for i, ss in enumerate(short_sides) if lo <= ss < hi]
        if not idxs: continue
        m16 = statistics.mean(rows["1.6"][i][2] for i in idxs)
        m20 = statistics.mean(rows["2.0"][i][2] for i in idxs)
        wb = sum(1 for i in idxs if rows["2.0"][i][2] < rows["1.6"][i][2] - 1e-9)
        emit(f"  {lbl:>9s} {len(idxs):3d} {m16:9.4f} {m20:9.4f} {m20-m16:+9.4f}  {wb:>5d}/{len(idxs)}")

    # ── By language ───────────────────────────────────────────────────────
    emit("\n" + "-" * 100)
    emit("By language")
    emit("-" * 100)
    emit(f"  {'lang':>5s} {'n':>3s} {'1.6':>9s} {'2.0':>9s} {'Δ':>9s}  {'2.0 better':>12s}")
    for lang in sorted(set(m["meta"].get("lang", "") for m in manifest)):
        idxs = [i for i, m in enumerate(manifest) if m["meta"].get("lang") == lang]
        if not idxs: continue
        m16 = statistics.mean(rows["1.6"][i][2] for i in idxs)
        m20 = statistics.mean(rows["2.0"][i][2] for i in idxs)
        wb = sum(1 for i in idxs if rows["2.0"][i][2] < rows["1.6"][i][2] - 1e-9)
        emit(f"  {lang:>5s} {len(idxs):3d} {m16:9.4f} {m20:9.4f} {m20-m16:+9.4f}  {wb:>5d}/{len(idxs)}")

    # ── Save full per-image detail ────────────────────────────────────────
    detail = []
    for i in range(n):
        item = manifest[i]
        entry = {
            "id": item["id"], "category": item["category"],
            "short_side": min(item["meta"]["size"]),
            "size_tier": item["meta"].get("size_tier", ""),
            "lang": item["meta"].get("lang", ""),
            "truth": rows["1.6"][i][0],
            "configs": {},
        }
        for name, _ in CONFIGS:
            truth, txt, c, n_boxes, txts, ops = rows[name][i]
            entry["configs"][name] = {
                "text": txt, "cer": c, "n_boxes": n_boxes,
                "diff_vs_truth": ops,
            }
        detail.append(entry)
    detail_path = ds / "unclip_ratio_detail.json"
    detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    emit(f"\nFull per-image detail (all {n} images, both configs, with diff ops)")
    emit(f"saved to: {detail_path}")

    # ── Cases where 2.0 beats 1.6 ────────────────────────────────────────
    _print_cases(emit, manifest, rows, "1.6", "2.0",
                 "2.0 BETTER (higher unclip wins)", lambda g16, g20: g20 < g16 - 1e-9,
                 top=12, thresh=0.02)

    # ── Cases where 1.6 beats 2.0 ────────────────────────────────────────
    _print_cases(emit, manifest, rows, "2.0", "1.6",
                 "1.6 BETTER (lower unclip wins)", lambda g20, g16: g16 < g20 - 1e-9,
                 top=12, thresh=0.02)

    emit("\n" + "=" * 100)
    emit(f"Overall verdict: {verdict}")
    emit("=" * 100)
    emit("Caveat: equal-weight CER.  Diff ops show error SHAPE (misread vs merged")
    emit("  box vs dropped region).  Full detail in unclip_ratio_detail.json.")
    emit(f"  dpr=1.5 Chromium pixels, {n} images, <0.2% = noise.")

    if args.report:
        rp = _project_root / args.report
        rp.write_text("\n".join(lines_out), encoding="utf-8")
        print(f"\nReport written to {rp}")


def _tiers(manifest):
    seen = []
    for m in manifest:
        t = m["meta"].get("size_tier", "")
        if t and t not in seen:
            seen.append(t)
    return seen


def _print_cases(emit, manifest, rows, better_name, worse_name, title,
                 pred, top=12, thresh=0.0):
    """Print top cases where one config beats the other by > thresh."""
    emit("\n" + "-" * 100)
    emit(f"Cases where {title}  (top {top}, ΔCER > {thresh})")
    emit("-" * 100)
    cases = []
    for i in range(len(manifest)):
        cb = rows[better_name][i][2]
        cw = rows[worse_name][i][2]
        gain = cw - cb
        if gain > thresh:
            cases.append((gain, i))
    cases.sort(key=lambda x: -x[0])
    if not cases:
        emit(f"  (none with ΔCER > {thresh})")
        return
    for gain, i in cases[:top]:
        item = manifest[i]
        emit(f"\n  {better_name} better by {gain:.3f}  {item['id']} "
             f"cat={item['category']} tier={item['meta'].get('size_tier','')} "
             f"short={min(item['meta']['size'])} lang={item['meta'].get('lang','')}")
        emit(f"      truth: {rows[better_name][i][0][:90]}")
        for name in [better_name, worse_name]:
            truth, txt, c, n_boxes, txts, ops = rows[name][i]
            emit(f"      unclip={name:3s} CER={c:.3f} n_boxes={n_boxes}  diff: "
                 f"{'  '.join(ops)[:170] if ops else '(exact)'}")
            emit(f"             OUT: {txt[:90]}")


if __name__ == "__main__":
    main()
