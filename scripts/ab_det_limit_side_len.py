"""Sweep: Det.limit_side_len 64 / 320 / 480 / 736 (baseline) on the
desktop-text dataset.

Question: rapidocr's default 736 (limit_type=min) resizes the SHORT side to
736px.  For desktop screenshots - which are already crisp, pixel-exact, and
often small (a UI label might be 200x30) - 736 can upscale a 30px short side
~24x, blurring crisp pixels via interpolation.  PaddleOCR's v6 pipeline uses
limit_side_len=64 (effectively "don't force-upscale").  Is 736 over-processing
desktop screenshots?  Does a smaller value do better?

Variable isolation: ONLY Det.limit_side_len varies.  mean/std=[0.5,0.5,0.5],
use_dilation=true, use_cls=false, Global.max_side_len=1280 identical in all
(so any difference is attributable to the short-side resize threshold).

Evaluation:
  - Equal-weight CER (no weighting: weighting char vs punctuation vs emoji is
    a judgment that can introduce its own bias).  Raw CER is recorded as-is.
  - Reported BOTH overall AND bucketed by the image's ACTUAL short side
    (<150 / 150-400 / >400), because the hypothesis is specifically that 736
    over-processes SMALL images.  An overall mean would hide a small-image
    regression behind large-image stability (whack-a-mole blind spot).
  - Full per-image OCR outputs are kept so worst cases can be sampled and
    eyeballed (is the error a misread character, or just punctuation/spacing?).

Dataset + eval identical to ab_det_normalize.py (same 246 images, fixed seed).

Usage:
    python scripts/ab_det_limit_side_len.py
"""

from __future__ import annotations

import argparse
import difflib
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ab_det_normalize import (  # noqa: E402
    _build_params, normalize_text, cer, levenshtein,
)

_project_root = Path(__file__).resolve().parent.parent


def diff_ops(truth: str, pred: str) -> list[str]:
    """Human-readable edit ops between truth and prediction.

    Uses difflib.SequenceMatcher on the character sequences.  Returns a list
    of short strings like:
        '识别->识別'      (substitution: truth had '识别', pred has '识別')
        '漏"。'           (deletion: truth had it, pred dropped it)
        '多"r"'           (insertion: pred added it, truth didn't have it)
    Groups consecutive single-char ops into runs so the output reads as
    words/segments, not one char per line.
    """
    if truth == pred:
        return []
    sm = difflib.SequenceMatcher(a=truth, b=pred, autojunk=False)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        t_seg = truth[i1:i2]
        p_seg = pred[j1:j2]
        if tag == "equal":
            continue
        if tag == "replace":
            ops.append(f'"{t_seg}"->"{p_seg}"')
        elif tag == "delete":
            ops.append(f'漏"{t_seg}"')
        elif tag == "insert":
            ops.append(f'多"{p_seg}"')
    return ops

CANDIDATES = [64, 320, 480, 736]
BASELINE = 736

# Short-side buckets (in pixels) - the hypothesis is about over-processing
# small images, so bucket by the image's actual min dimension.
def _bucket(short_side: int) -> str:
    if short_side < 150:
        return "<150"
    if short_side <= 400:
        return "150-400"
    return ">400"


def main():
    ap = argparse.ArgumentParser(description="Sweep Det.limit_side_len.")
    ap.add_argument("--dataset", default="scratch/ab_normalize_dataset")
    ap.add_argument("--report", default="", help="also write report to this file")
    ap.add_argument("--sample-worst", type=int, default=10,
                    help="print this many images where 736 loses to a smaller value")
    args = ap.parse_args()

    ds = _project_root / args.dataset
    manifest = json.loads((ds / "manifest.json").read_text(encoding="utf-8"))
    print(f"Dataset: {ds}  ({len(manifest)} images)")

    lines_out = []
    def emit(s=""):
        print(s); lines_out.append(s)

    emit("=" * 78)
    emit(f"Sweep: Det.limit_side_len  {CANDIDATES}  (baseline {BASELINE})")
    emit("  Only limit_side_len varies; mean/std=0.5, use_dilation=true, use_cls=false,")
    emit("  Global.max_side_len=1280 identical -> difference = short-side resize threshold.")
    emit("  CER is equal-weight (no char/punct/emoji weighting); worst cases sampled for")
    emit("  eyeball review at the end.")
    emit("=" * 78)

    # Build engines
    from rapidocr import RapidOCR
    engines = {}
    for lsl in CANDIDATES:
        params = {
            "Det.ocr_version": None,  # placeholder, filled by _build_params
        }
        # use _build_params then override limit_side_len
        base = _build_params({"Det.mean": [0.5, 0.5, 0.5], "Det.std": [0.5, 0.5, 0.5]})
        base["Det.limit_side_len"] = lsl
        t0 = time.perf_counter()
        engines[lsl] = RapidOCR(params=base)
        emit(f"  engine lsl={lsl:4d} ready ({(time.perf_counter()-t0)*1000:.0f}ms)")

    # Run
    # rows[lsl] = list of (id, cat, short_side, bucket, truth, text, cer)
    rows = {lsl: [] for lsl in CANDIDATES}
    t_start = time.perf_counter()
    for i, item in enumerate(manifest, 1):
        png = ds / item["meta"]["png"]
        truth = normalize_text(item["truth"])
        w, h = item["meta"]["size"]
        short_side = min(w, h)
        bucket = _bucket(short_side)
        for lsl in CANDIDATES:
            res = engines[lsl](str(png))
            txts = getattr(res, "txts", None) or ()
            txt = normalize_text("".join(txts))
            rows[lsl].append((item["id"], item["category"], short_side, bucket,
                              truth, txt, cer(txt, truth)))
        if i % 50 == 0:
            print(f"  ...{i}/{len(manifest)}  ({time.perf_counter()-t_start:.0f}s)")

    elapsed = time.perf_counter() - t_start
    n = len(manifest)
    emit(f"\nRan {n} images x {len(CANDIDATES)} configs in {elapsed:.0f}s\n")

    # ── Overall ────────────────────────────────────────────────────────────
    emit("=" * 78)
    emit(f"OVERALL  (baseline = {BASELINE})")
    emit("=" * 78)
    emit(f"  {'lsl':>5s}  {'meanCER':>8s}  {'pooledCER':>9s}  {'vs base':>9s}  "
         f"{'win':>4s} {'lose':>4s} {'tie':>4s}")
    base_rows = rows[BASELINE]
    base_mean = statistics.mean(r[6] for r in base_rows)
    summary = []
    for lsl in CANDIDATES:
        rs = rows[lsl]
        m = statistics.mean(r[6] for r in rs)
        pooled = sum(levenshtein(r[5], r[3]) for r in rs)
        tot = sum(len(r[3]) for r in rs)
        pooled_cer = pooled / tot if tot else 0
        win = lose = tie = 0
        for rb, rt in zip(base_rows, rs):
            if rt[6] < rb[6] - 1e-9: win += 1
            elif rt[6] > rb[6] + 1e-9: lose += 1
            else: tie += 1
        diff = m - base_mean
        summary.append((lsl, m, pooled_cer, diff, win, lose, tie))
        emit(f"  {lsl:5d}  {m:8.4f}  {pooled_cer:9.4f}  {diff:+9.4f}  "
             f"{win:4d} {lose:4d} {tie:4d}")

    best = min(summary, key=lambda s: s[1])
    max_abs = max(abs(s[3]) for s in summary if s[0] != BASELINE)
    if best[0] == BASELINE:
        verdict = f"736 is best (max |Δ| vs others = {max_abs:.4f})"
    else:
        verdict = f"{best[0]} is best (Δ vs 736 = {best[3]:+.4f})"
    emit(f"\n  >>> overall: {verdict}")

    # ── By short-side bucket ───────────────────────────────────────────────
    emit("\n" + "-" * 78)
    emit("By ACTUAL short side  (the hypothesis: 736 over-processes small images)")
    emit("-" * 78)
    buckets = ["<150", "150-400", ">400"]
    hdr = f"  {'bucket':9s} {'n':>3s} " + " ".join(f"{lsl:>9d}" for lsl in CANDIDATES) \
          + "   (best vs 736)"
    emit(hdr)
    bucket_summary = {}
    for b in buckets:
        brs = {lsl: [r for r in rows[lsl] if r[3] == b] for lsl in CANDIDATES}
        if not brs[BASELINE]:
            continue
        cells = []
        means = {}
        for lsl in CANDIDATES:
            mm = statistics.mean(r[6] for r in brs[lsl]) if brs[lsl] else float('nan')
            means[lsl] = mm
            cells.append(f"{mm:9.4f}")
        base_m = means[BASELINE]
        best_lsl = min(means, key=lambda k: means[k])
        d = means[best_lsl] - base_m
        bucket_summary[b] = (means, best_lsl, d)
        n = len(brs[BASELINE])
        tag = f"{best_lsl} ({d:+.4f})" if best_lsl != BASELINE else "736 (baseline)"
        emit(f"  {b:9s} {n:3d} " + " ".join(cells) + f"   {tag}")

    # ── By category (for context) ──────────────────────────────────────────
    emit("\n" + "-" * 78)
    emit("By category  (meanCER per lsl)")
    emit("-" * 78)
    cats = sorted(set(r[1] for r in base_rows))
    emit(f"  {'cat':9s} {'n':>3s} " + " ".join(f"{lsl:>9d}" for lsl in CANDIDATES))
    for cat in cats:
        cells = []
        for lsl in CANDIDATES:
            rs = [r for r in rows[lsl] if r[1] == cat]
            cells.append(f"{statistics.mean(r[6] for r in rs):9.4f}")
        emit(f"  {cat:9s} {sum(1 for r in base_rows if r[1]==cat):3d} " + " ".join(cells))

    # ── Save full per-image detail (all images, all configs) ──────────────
    # No sampling: the dataset is small (246).  Save everything so any image
    # can be audited later.  Each record carries truth + each config's OCR
    # text + CER + the concrete diff ops (what was misread / dropped / added).
    detail = []
    n_img = len(base_rows)
    for idx in range(n_img):
        recs = {lsl: rows[lsl][idx] for lsl in CANDIDATES}
        rb = recs[BASELINE]
        entry = {
            "id": rb[0], "category": rb[1], "short_side": rb[2], "bucket": rb[3],
            "truth": rb[4],
            "configs": {},
        }
        for lsl in CANDIDATES:
            r = recs[lsl]
            entry["configs"][str(lsl)] = {
                "text": r[5], "cer": r[6],
                "diff_vs_truth": diff_ops(rb[4], r[5]),
            }
        detail.append(entry)
    detail_path = ds / "limit_side_len_detail.json"
    detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    emit(f"\nFull per-image detail (all {n_img} images, all configs, with diff ops)")
    emit(f"saved to: {detail_path}")

    # ── Print top cases where 736 is beaten by a smaller value ────────────
    # Ranked by how much worse 736 is vs the best alternative.  Printed WITH
    # the concrete diffs so the reader sees the error shape (misread char vs
    # dropped punctuation) without eyeballing raw strings.
    worst = []
    for idx in range(n_img):
        recs = {lsl: rows[lsl][idx] for lsl in CANDIDATES}
        base_c = recs[BASELINE][6]
        best_lsl = min(CANDIDATES, key=lambda l: recs[l][6])
        best_c = recs[best_lsl][6]
        gain = base_c - best_c
        if gain > 1e-9 and best_lsl != BASELINE:
            worst.append((gain, idx, best_lsl, recs))
    worst.sort(key=lambda x: -x[0])

    emit("\n" + "-" * 78)
    emit(f"Cases where 736 loses to a smaller value "
         f"(showing top {args.sample_worst} of {len(worst)})")
    emit("-" * 78)
    if not worst:
        emit("  (none - 736 was never strictly worse than every smaller value)")
    for gain, idx, best_lsl, recs in worst[:args.sample_worst]:
        rb = recs[BASELINE]
        emit(f"\n  736 worse by {gain:.3f} (best={best_lsl})  {rb[0]} "
             f"({rb[1]} short={rb[2]} bucket={rb[3]})")
        emit(f"      truth: {rb[4][:100]}")
        for lsl in CANDIDATES:
            r = recs[lsl]
            mark = " *" if lsl == best_lsl else ("  " if lsl != BASELINE else " (736)")
            ops = diff_ops(rb[4], r[5])
            ops_str = "  ".join(ops) if ops else "(exact)"
            emit(f"      lsl={lsl:4d}{mark} CER={r[6]:.3f}  diff: {ops_str}")

    # ── Conversely: cases where 736 BEATS every smaller value ─────────────
    # (shows what we'd lose by switching to a smaller value)
    better = []
    for idx in range(n_img):
        recs = {lsl: rows[lsl][idx] for lsl in CANDIDATES}
        base_c = recs[BASELINE][6]
        worst_alt = max(recs[l][6] for l in CANDIDATES if l != BASELINE)
        gain = worst_alt - base_c  # how much 736 is better than the worst alt
        if gain > 1e-9:
            worst_lsl = max((l for l in CANDIDATES if l != BASELINE),
                            key=lambda l: recs[l][6])
            better.append((gain, idx, worst_lsl, recs))
    better.sort(key=lambda x: -x[0])
    emit("\n" + "-" * 78)
    emit(f"Cases where 736 beats the smaller values "
         f"(top {args.sample_worst} of {len(better)})  - what we'd lose by shrinking")
    emit("-" * 78)
    if not better:
        emit("  (none)")
    for gain, idx, worst_lsl, recs in better[:args.sample_worst]:
        rb = recs[BASELINE]
        emit(f"\n  736 better by {gain:.3f} (worst alt={worst_lsl})  {rb[0]} "
             f"({rb[1]} short={rb[2]} bucket={rb[3]})")
        emit(f"      truth: {rb[4][:100]}")
        for lsl in CANDIDATES:
            r = recs[lsl]
            mark = " (736)" if lsl == BASELINE else (" !" if lsl == worst_lsl else "  ")
            ops = diff_ops(rb[4], r[5])
            ops_str = "  ".join(ops) if ops else "(exact)"
            emit(f"      lsl={lsl:4d}{mark} CER={r[6]:.3f}  diff: {ops_str}")

    emit("\n" + "=" * 78)
    emit(f"Overall verdict: {verdict}")
    if bucket_summary:
        small = bucket_summary.get("<150")
        if small:
            _, bl, dd = small
            emit(f"Small-image bucket (<150): best={bl}, Δ vs 736={dd:+.4f}")
    emit("=" * 78)
    emit("\nCaveat: equal-weight CER (char/punct/emoji all count as 1).  The printed")
    emit("  diffs show the error SHAPE (misread char vs dropped punctuation) so you can")
    emit("  judge severity by eye.  Full detail in limit_side_len_detail.json.")
    emit("  Chromium pixels, 246 images, <0.5% = noise.")

    if args.report:
        rp = _project_root / args.report
        rp.write_text("\n".join(lines_out), encoding="utf-8")
        print(f"\nReport written to {rp}")


if __name__ == "__main__":
    main()
