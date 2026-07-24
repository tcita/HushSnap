"""A/B test: Det.use_dilation True (rapidocr default / HushSnap now) vs False
(PaddleOCR default).

Question: rapidocr defaults Det.use_dilation=True; PaddleOCR defaults False
(params.py:37).  use_dilation is a DB (Differentiable Binarization) post-step
that dilates the binary score map before shrinking/box-finding, helping thin
or broken strokes connect into text regions.  On screenshots - which have
clean, sharp, anti-aliased text - is dilation helping (connecting thin
strokes) or hurting (over-merging adjacent words/boxes, fusing separate text
regions)?  PaddleOCR's False suggests that for their training distribution
dilation isn't needed; is it needed for HushSnap's desktop-screenshot domain?

Variable isolation: ONLY Det.use_dilation varies.  Det.limit_side_len=64,
Det.mean/std=[0.5,0.5,0.5], use_cls=false, Global.max_side_len=1280 identical
in both -> any difference is attributable to the dilation toggle.

Evaluation:
  - Equal-weight CER (no char/punct/emoji weighting - that's a judgment with
    its own bias).  Raw CER recorded as-is.
  - PAIRED per image (same image under both configs) so the comparison is
    within-image, not confounded by content difficulty.
  - Reported overall AND bucketed by category, size_tier, short side, lang -
    dilation's effect on box-merging should show up differently in tightly-
    spaced categories (code/terminal) vs sparse ones (word/web).
  - Full per-image OCR outputs + concrete diff ops kept so worst cases can be
    eyeballed: is the error a misread char, a merged box (two words stuck
    together), a dropped region, or just punctuation/spacing?

Dataset: scratch/desktop_dataset (scripts/gen_normalize_dataset.py, dpr=1.5,
real font sizes, 6 cats x 3 size tiers x CJK/Latin, ~480 images).  This is
the reliable general dataset - not just small images.

Usage:
    python scripts/ab_det_use_dilation.py
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from pathlib import Path

# Silence rapidocr INFO logs (download/File-exists/Using) so output is just
# our progress + results.  Set BEFORE importing rapidocr.
logging.getLogger("RapidOCR").setLevel(logging.WARNING)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ab_det_normalize import _build_params, normalize_text, cer, levenshtein  # noqa: E402
from ab_det_limit_side_len import diff_ops  # noqa: E402

_project_root = Path(__file__).resolve().parent.parent

CONFIGS = [("True", True), ("False", False)]
BASELINE = "True"  # current HushSnap value

# Isolation constants = the RUNTIME det path (mean/std=ImageNet now, not 0.5;
# see memory rapidocr-det-normalize-not-imagenet).  Only use_dilation varies.
DET_MEAN = [0.485, 0.456, 0.406]
DET_STD = [0.229, 0.224, 0.225]

# Short-side bins (px) - actual min dimension of the cropped image.
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
    emit("A/B: Det.use_dilation  True (rapidocr default / HushSnap) vs False (PaddleOCR)")
    emit("  Only use_dilation varies; limit_side_len=64, mean/std=0.5, use_cls=false,")
    emit("  Global.max_side_len=1280 identical -> difference = dilation toggle.")
    emit("=" * 100)

    from rapidocr import RapidOCR
    engines = {}
    for name, val in CONFIGS:
        base = _build_params({"Det.mean": DET_MEAN, "Det.std": DET_STD})
        base["Det.limit_side_len"] = 64
        base["Det.use_dilation"] = val
        t0 = time.perf_counter()
        engines[name] = RapidOCR(params=base)
        emit(f"  engine use_dilation={name} ready ({(time.perf_counter()-t0)*1000:.0f}ms)")

    def getarr(res, attr):
        v = getattr(res, attr, None)
        return list(v) if v is not None else []

    # rows[name][i] = (truth, txt, cer, n_boxes, txts_list, diff_ops_list)
    rows = {name: [None] * n for name, _ in CONFIGS}
    t_start = time.perf_counter()
    for i, item in enumerate(manifest):
        png = ds / item["meta"]["png"]
        truth = normalize_text(item["truth"])
        for name, _ in CONFIGS:
            res = engines[name](str(png))
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
    emit(f"OVERALL  (baseline = use_dilation={BASELINE})")
    emit("=" * 100)
    means = {}
    for name, _ in CONFIGS:
        m = statistics.mean(r[2] for r in rows[name])
        means[name] = m
        emit(f"  use_dilation={name:5s}  meanCER={m:.4f}")
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
    # Wilcoxon-style sign summary (non-parametric, no normality assumption)
    deltas = [rows[other_name][i][2] - rows[BASELINE][i][2] for i in range(n)]
    nonzero = [d for d in deltas if abs(d) > 1e-9]
    pos = sum(1 for d in nonzero if d > 0)   # other worse than baseline
    neg = sum(1 for d in nonzero if d < 0)   # other better than baseline
    emit(f"  paired non-tie: {other_name} better={neg}  worse={pos}  "
         f"(sign test two-sided p ~ {2*min(pos,neg) if nonzero else 0} / {len(nonzero)})")
    if diff < -0.002:
        verdict = f"{other_name} (use_dilation=False) is better by {-diff:.4f}"
    elif diff > 0.002:
        verdict = f"{BASELINE} (use_dilation=True) is better by {diff:.4f}"
    else:
        verdict = f"within noise (|Δ|={abs(diff):.4f} < 0.002); keep {BASELINE} (rapidocr default)"
    emit(f"\n  >>> overall: {verdict}")

    # ── Failure-shape analysis: WHERE does dilation help/hurt ─────────────
    # CER alone hides the mechanism. Break the A!=B images into failure
    # shapes: box-count diff (dilation merging/splitting), empty output
    # (detection miss), and see which config wins each shape.
    emit("\n" + "-" * 100)
    emit("Failure-shape analysis (images where True != False, n=%d)" % n)
    emit("-" * 100)
    # paired box counts
    nb_t = [rows["True"][i][3] for i in range(n)]
    nb_f = [rows["False"][i][3] for i in range(n)]
    same_box = sum(1 for a, b in zip(nb_t, nb_f) if a == b)
    t_more = sum(1 for a, b in zip(nb_t, nb_f) if a > b)   # dilation -> MORE boxes (less merging)
    f_more = sum(1 for a, b in zip(nb_t, nb_f) if b > a)   # no dilation -> more boxes
    emit(f"  box count: True==False on {same_box}/{n}  "
         f"(True more boxes: {t_more} / False more boxes: {f_more})")
    emit(f"  total boxes  True={sum(nb_t)}  False={sum(nb_f)}")
    # empty-output (detection miss): CER=1.0 AND no text
    t_empty = [i for i in range(n) if rows["True"][i][2] >= 0.99 and not rows["True"][i][1].strip()]
    f_empty = [i for i in range(n) if rows["False"][i][2] >= 0.99 and not rows["False"][i][1].strip()]
    emit(f"  empty output (det miss): True={len(t_empty)}  False={len(f_empty)}")
    # cases where dilation changes box count a lot (merging/splitting effect)
    big_box_diff = sorted(
        [(abs(nb_t[i]-nb_f[i]), i) for i in range(n) if nb_t[i] != nb_f[i]],
        key=lambda x: -x[0])
    if big_box_diff:
        emit(f"\n  Top box-count divergences (dilation's merge/split effect):")
        for _, i in big_box_diff[:6]:
            item = manifest[i]
            rt = rows["True"][i]; rf = rows["False"][i]
            emit(f"    {item['id']} ({item['category']} short={min(item['meta']['size'])})  "
                 f"True:{rt[3]}boxes CER={rt[2]:.3f}  vs  False:{rf[3]}boxes CER={rf[2]:.3f}")
            emit(f"      truth: {rt[0][:85]}")
            emit(f"      True  boxes: {rt[4]}")
            emit(f"      False boxes: {rf[4]}")
    # cases where one config returns empty but the other reads text (the
    # clearest "dilation on/off flips detection" signal)
    flip = [(i, "True" if not rows["True"][i][1].strip() and rows["False"][i][1].strip() else "False")
            for i in range(n)
            if (not rows["True"][i][1].strip()) != (not rows["False"][i][1].strip())]
    if flip:
        emit(f"\n  Detection-flip cases (one empty, other reads text): {len(flip)}")
        for i, empty_side in flip[:6]:
            item = manifest[i]
            rt = rows["True"][i]; rf = rows["False"][i]
            full = "False" if empty_side == "True" else "True"
            emit(f"    {item['id']} ({item['category']} short={min(item['meta']['size'])})  "
                 f"EMPTY={empty_side}  READS={full}")
            emit(f"      truth: {rt[0][:85]}")
            emit(f"      {full:5s} OUT: {(rf[1] if empty_side=='True' else rt[1])[:85]}")

    # ── By category ───────────────────────────────────────────────────────
    emit("\n" + "-" * 100)
    emit("By category  (meanCER per config; Δ = False - True)")
    emit("-" * 100)
    cats = sorted(set(m["category"] for m in manifest))
    emit(f"  {'cat':9s} {'n':>3s} {'True':>9s} {'False':>9s} {'Δ':>9s}  {'False better':>12s}")
    for cat in cats:
        idxs = [i for i, m in enumerate(manifest) if m["category"] == cat]
        mt = statistics.mean(rows["True"][i][2] for i in idxs)
        mf = statistics.mean(rows["False"][i][2] for i in idxs)
        wb = sum(1 for i in idxs if rows["False"][i][2] < rows["True"][i][2] - 1e-9)
        emit(f"  {cat:9s} {len(idxs):3d} {mt:9.4f} {mf:9.4f} {mf-mt:+9.4f}  {wb:>5d}/{len(idxs)}")

    # ── By size tier ──────────────────────────────────────────────────────
    emit("\n" + "-" * 100)
    emit("By size_tier  (short=1 line, medium, long=multi-para)")
    emit("-" * 100)
    emit(f"  {'tier':8s} {'n':>3s} {'True':>9s} {'False':>9s} {'Δ':>9s}  {'False better':>12s}")
    for tier in _tiers(manifest):
        idxs = [i for i, m in enumerate(manifest) if m["meta"].get("size_tier") == tier]
        if not idxs: continue
        mt = statistics.mean(rows["True"][i][2] for i in idxs)
        mf = statistics.mean(rows["False"][i][2] for i in idxs)
        wb = sum(1 for i in idxs if rows["False"][i][2] < rows["True"][i][2] - 1e-9)
        emit(f"  {tier:8s} {len(idxs):3d} {mt:9.4f} {mf:9.4f} {mf-mt:+9.4f}  {wb:>5d}/{len(idxs)}")

    # ── By short-side bin ─────────────────────────────────────────────────
    emit("\n" + "-" * 100)
    emit("By ACTUAL short side  (px)")
    emit("-" * 100)
    emit(f"  {'bucket':>9s} {'n':>3s} {'True':>9s} {'False':>9s} {'Δ':>9s}  {'False better':>12s}")
    short_sides = [min(m["meta"]["size"]) for m in manifest]
    for lo, hi, lbl in BINS:
        idxs = [i for i, ss in enumerate(short_sides) if lo <= ss < hi]
        if not idxs: continue
        mt = statistics.mean(rows["True"][i][2] for i in idxs)
        mf = statistics.mean(rows["False"][i][2] for i in idxs)
        wb = sum(1 for i in idxs if rows["False"][i][2] < rows["True"][i][2] - 1e-9)
        emit(f"  {lbl:>9s} {len(idxs):3d} {mt:9.4f} {mf:9.4f} {mf-mt:+9.4f}  {wb:>5d}/{len(idxs)}")

    # ── By language ───────────────────────────────────────────────────────
    emit("\n" + "-" * 100)
    emit("By language")
    emit("-" * 100)
    emit(f"  {'lang':>5s} {'n':>3s} {'True':>9s} {'False':>9s} {'Δ':>9s}  {'False better':>12s}")
    for lang in sorted(set(m["meta"].get("lang", "") for m in manifest)):
        idxs = [i for i, m in enumerate(manifest) if m["meta"].get("lang") == lang]
        if not idxs: continue
        mt = statistics.mean(rows["True"][i][2] for i in idxs)
        mf = statistics.mean(rows["False"][i][2] for i in idxs)
        wb = sum(1 for i in idxs if rows["False"][i][2] < rows["True"][i][2] - 1e-9)
        emit(f"  {lang:>5s} {len(idxs):3d} {mt:9.4f} {mf:9.4f} {mf-mt:+9.4f}  {wb:>5d}/{len(idxs)}")

    # ── Save full per-image detail ────────────────────────────────────────
    detail = []
    for i in range(n):
        item = manifest[i]
        entry = {
            "id": item["id"], "category": item["category"],
            "short_side": min(item["meta"]["size"]),
            "size_tier": item["meta"].get("size_tier", ""),
            "lang": item["meta"].get("lang", ""),
            "truth": rows["True"][i][0],
            "configs": {},
        }
        for name, _ in CONFIGS:
            truth, txt, c, n_boxes, txts, ops = rows[name][i]
            entry["configs"][name] = {
                "text": txt, "cer": c, "n_boxes": n_boxes,
                "diff_vs_truth": ops,
            }
        detail.append(entry)
    detail_path = ds / "use_dilation_detail.json"
    detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    emit(f"\nFull per-image detail (all {n} images, both configs, with diff ops)")
    emit(f"saved to: {detail_path}")

    # ── Cases where False beats True (dilation hurts) ────────────────────
    _print_cases(emit, manifest, rows, "True", "False", "dilation HURTS (False better)",
                 lambda gt, gf: gf < gt - 1e-9, top=12, thresh=0.02)

    # ── Cases where True beats False (dilation helps) ────────────────────
    _print_cases(emit, manifest, rows, "False", "True", "dilation HELPS (True better)",
                 lambda gf, gt: gt < gf - 1e-9, top=12, thresh=0.02)

    emit("\n" + "=" * 100)
    emit(f"Overall verdict: {verdict}")
    emit("=" * 100)
    emit("Caveat: equal-weight CER (char/punct/emoji all count as 1).  Diff ops show")
    emit("  the error SHAPE (misread char vs merged box vs dropped region) so severity")
    emit("  can be judged by eye.  Full detail in use_dilation_detail.json.")
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
            emit(f"      dilation={name:5s} CER={c:.3f} n_boxes={n_boxes}  diff: "
                 f"{'  '.join(ops)[:170] if ops else '(exact)'}")
            emit(f"             OUT: {txt[:90]}")


if __name__ == "__main__":
    main()
