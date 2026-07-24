"""A/B: Det.mean/std = [0.5,0.5,0.5] (rapidocr default) vs ImageNet
([0.485,0.456,0.406]/[0.229,0.224,0.225]) on the browser-rendered desktop-text
dataset (scratch/desktop_dataset, dpr=1.5).

Question: which det normalization should HushSnap (PP-OCRv6 + desktop
screenshots) use?  Prior evidence is conflicting:
  - rapidocr author (v4, 2024, natural photos, detection hmean): 0.5 wins 0.73%
  - PP-OCRv6_small_det.yml trains with ImageNet mean/std
  - HushSnap's old A/B was deleted; the numbers in memory are now unanchored

This generates fresh evidence: PP-OCRv6 + realistic desktop-text screenshots +
end-to-end recognition (CER vs ground truth).

Variable isolation: ONLY Det.mean/Det.std differ between A and B.
limit_side_len=32, use_dilation=false, use_cls=false are identical in both
(= the current production det path, see hushsnap/ocr/ppocr.py
_DEFAULT_ENGINE_PARAMS), so any A/B difference is attributable to
normalization.  The mean/std conclusion is unaffected by the resize choice
since normalization acts on global pixel statistics.

Dataset: scratch/desktop_dataset (gen_normalize_dataset.py, dpr=1.5, real font
sizes, 6 cats x 3 size tiers x CJK/Latin).  Earlier runs used
scratch/ab_normalize_dataset (dpr=1, glyphs ~1/3 too small); the dpr=1.5 set
is the reliable one.
Manifest: manifest.json with per-image {category, truth, meta{font_size,...}}.

Usage:
    python scripts/ab_det_normalize.py
    python scripts/ab_det_normalize.py --dataset scratch/desktop_dataset --report scratch/ab_normalize_report.txt
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
import unicodedata

# Silence rapidocr's chatty INFO logs (download/File-exists/Using) so the A/B
# output is just our progress + results.  Set BEFORE importing rapidocr.
logging.getLogger("RapidOCR").setLevel(logging.WARNING)
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent

# ── A/B engine configs ──────────────────────────────────────────────────────
# Both pin ocr_version/model_type/use_cls + limit_side_len/use_dilation to the
# RUNTIME values (32 / false, see hushsnap/ocr/ppocr.py _DEFAULT_ENGINE_PARAMS),
# so ONLY mean/std varies and the A/B reflects the real production det path.
_BASE_PARAMS = None  # built lazily after importing OCRVersion/ModelType

def _build_params(mean_std: dict) -> dict:
    from rapidocr import OCRVersion, ModelType
    base = {
        "Det.ocr_version": OCRVersion.PPOCRV6,
        "Det.model_type": ModelType.SMALL,
        "Global.use_cls": False,
        "Det.limit_side_len": 32,
        "Det.use_dilation": False,
    }
    base.update(mean_std)
    return base

CONFIG_A = lambda: _build_params({"Det.mean": [0.5, 0.5, 0.5], "Det.std": [0.5, 0.5, 0.5]})
CONFIG_B = lambda: _build_params({"Det.mean": [0.485, 0.456, 0.406], "Det.std": [0.229, 0.224, 0.225]})


# ── Text normalization ──────────────────────────────────────────────────────
def normalize_text(s: str) -> str:
    """NFKC + collapse internal whitespace + strip.  Matches truth and OCR the
    same way, so cosmetic spacing differences (e.g. extra spaces the rec inserts)
    don't count as errors."""
    s = unicodedata.normalize("NFKC", s)
    # collapse runs of whitespace (incl. newlines) to single space
    out = []
    prev_space = False
    for ch in s:
        if ch.isspace():
            if not prev_space:
                out.append(" ")
            prev_space = True
        else:
            out.append(ch)
            prev_space = False
    return "".join(out).strip()


# ── Levenshtein (no external dep) ───────────────────────────────────────────
def levenshtein(a: str, b: str) -> int:
    """Standard edit distance.  a=pred, b=truth."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1,        # deletion
                         cur[j - 1] + 1,     # insertion
                         prev[j - 1] + cost)  # substitution
        prev = cur
    return prev[lb]


def cer(pred: str, truth: str) -> float:
    """Character error rate = edit_distance(pred, truth) / len(truth)."""
    t = truth
    if not t:
        return 0.0
    return levenshtein(pred, t) / len(t)


# ── Engine wrapper ──────────────────────────────────────────────────────────
class Engine:
    """A RapidOCR instance with a given det config.  Builds once, reused."""

    def __init__(self, params: dict, label: str):
        self.label = label
        from rapidocr import RapidOCR
        t0 = time.perf_counter()
        self.engine = RapidOCR(params=params)
        self.init_ms = (time.perf_counter() - t0) * 1000

    def recognize(self, png_path: Path) -> dict:
        """Run det+rec on a PNG, return RAW box-level output (no post-processing).

        Returns {boxes, txts, scores} straight from RapidOCR - NOT routed through
        any HushSnap layout/typography logic, so the A/B measures det/rec params
        themselves, not the downstream composer.  The caller joins txts as needed.
        boxes: list of 4x2 polygons; txts: per-box text; scores: per-box conf.
        """
        res = self.engine(str(png_path))
        # res.txts/boxes/scores are numpy arrays or lists; guard each (an empty
        # array is truthy-ambiguous, so check `is not None` not truthiness).
        txts = getattr(res, "txts", None)
        txts = list(txts) if txts is not None else []
        boxes = getattr(res, "boxes", None)
        boxes = ([b.tolist() if hasattr(b, "tolist") else b
                  for b in boxes] if boxes is not None else [])
        scores = getattr(res, "scores", None)
        scores = list(scores) if scores is not None else []
        return {"boxes": boxes, "txts": txts, "scores": scores}


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="A/B Det.mean/std: 0.5 vs ImageNet.")
    ap.add_argument("--dataset", default="scratch/desktop_dataset")
    ap.add_argument("--report", default="", help="also write report to this file")
    args = ap.parse_args()

    ds = _project_root / args.dataset
    manifest = json.loads((ds / "manifest.json").read_text(encoding="utf-8"))
    print(f"Dataset: {ds}  ({len(manifest)} images)")

    lines_out = []  # buffer for optional report file
    def emit(s=""):
        print(s)
        lines_out.append(s)

    emit("=" * 78)
    emit("A/B: Det.mean/std  [0.5,0.5,0.5]  vs  ImageNet")
    emit("  Only Det.mean/Det.std differ.  limit_side_len=32, use_dilation=false,")
    emit("  use_cls=false identical in both (= runtime det path) -> diff = normalize.")
    emit("  RAW RapidOCR output (res.boxes/txts/scores), NOT through any layout logic.")
    emit("=" * 78)

    # ── Single process, serial: config A over all images, then config B ──
    # No threads/subprocesses - simplest and most reliable (ONNXRuntime
    # concurrent sessions in one process contend on internal locks; parallel
    # variants hung in testing).  ~4 min for 480x2, acceptable.
    from rapidocr import RapidOCR
    eng_a = RapidOCR(params=CONFIG_A())
    eng_b = RapidOCR(params=CONFIG_B())
    emit("Engines ready (serial: A then B).\n")

    def run_one(engine, label):
        out = []
        t0 = time.perf_counter()
        for i, item in enumerate(manifest):
            res = engine(str(ds / item["meta"]["png"]))
            txts = getattr(res, "txts", None)
            txts = list(txts) if txts is not None else []
            scores = getattr(res, "scores", None)
            scores = list(scores) if scores is not None else []
            boxes = getattr(res, "boxes", None)
            boxes = ([b.tolist() if hasattr(b, "tolist") else b
                      for b in boxes] if boxes is not None else [])
            out.append({"txts": txts, "scores": scores, "boxes": boxes})
            if (i + 1) % 50 == 0:
                print(f"  [{label}] ...{i+1}/{len(manifest)}  "
                      f"({time.perf_counter()-t0:.0f}s)", flush=True)
        print(f"  [{label}] done {len(out)} imgs in {time.perf_counter()-t0:.0f}s",
              flush=True)
        return out

    t_start = time.perf_counter()
    raw_a_list = run_one(eng_a, "A (0.5)")
    raw_b_list = run_one(eng_b, "B (ImageNet)")
    elapsed = time.perf_counter() - t_start
    emit(f"\nRan {len(manifest)} images x 2 configs (serial) in {elapsed:.0f}s "
         f"({elapsed/len(manifest)/2*1000:.0f}ms/img/config)\n")

    # Build rows: pair by image id.  raw_a_list/raw_b_list are in manifest order.
    rows = []  # (id, cat, fs, truth, ta, tb, ca, cb, raw_a, raw_b)
    for item, raw_a, raw_b in zip(manifest, raw_a_list, raw_b_list):
        truth = normalize_text(item["truth"])
        fs = item["meta"].get("font_size", 0)
        cat = item["category"]
        # CER on RAW per-box text joined (no layout logic; normalize_text's
        # NFKC+collapse applied identically to truth and both configs).
        ta = normalize_text("".join(raw_a["txts"]))
        tb = normalize_text("".join(raw_b["txts"]))
        ca = cer(ta, truth)
        cb = cer(tb, truth)
        rows.append((item["id"], cat, fs, truth, ta, tb, ca, cb, raw_a, raw_b))

    # ── Summary ─────────────────────────────────────────────────────────────
    a_win = sum(1 for r in rows if r[6] < r[7] - 1e-9)   # A strictly better
    b_win = sum(1 for r in rows if r[7] < r[6] - 1e-9)   # B strictly better
    tie = len(rows) - a_win - b_win
    same_text = sum(1 for r in rows if r[4] == r[5])     # A==B output identical

    avg_a = statistics.mean(r[6] for r in rows)
    avg_b = statistics.mean(r[7] for r in rows)
    # paired: per-image (cer_b - cer_a); negative => B better
    diffs = [r[7] - r[6] for r in rows]
    mean_diff = statistics.mean(diffs)
    # total CER (pooled chars)
    tot_a = sum(levenshtein(r[4], r[3]) for r in rows)
    tot_b = sum(levenshtein(r[5], r[3]) for r in rows)
    tot_truth = sum(len(r[3]) for r in rows)

    emit("=" * 78)
    emit("SUMMARY")
    emit("=" * 78)
    emit(f"  Images: {len(rows)}   Truth chars: {tot_truth}")
    emit(f"  A==B text identical on: {same_text}/{len(rows)}  ({same_text/len(rows):.0%})")
    emit("")
    emit(f"  Per-image verdict (strict CER):")
    emit(f"    A (0.5) better: {a_win}    B (ImageNet) better: {b_win}    tie: {tie}")
    emit(f"  Mean CER:   A={avg_a:.4f}   B={avg_b:.4f}   (B-A={mean_diff:+.4f})")
    emit(f"  Pooled CER: A={tot_a/tot_truth:.4f}   B={tot_b/tot_truth:.4f}")
    if mean_diff < -0.002:
        verdict = "B (ImageNet) better"
    elif mean_diff > 0.002:
        verdict = "A (0.5) better"
    else:
        verdict = "NO meaningful difference (<0.2%)"
    emit(f"\n  >>> {verdict}")

    # ── By category ────────────────────────────────────────────────────────
    emit("\n" + "-" * 78)
    emit("By category")
    emit("-" * 78)
    cats = sorted(set(r[1] for r in rows))
    emit(f"  {'cat':9s} {'n':>4s}  {'meanA':>7s} {'meanB':>7s} {'B-A':>8s}  {'Awin':>4s} {'Bwin':>4s} {'tie':>4s}")
    for cat in cats:
        cr = [r for r in rows if r[1] == cat]
        ma = statistics.mean(r[6] for r in cr); mb = statistics.mean(r[7] for r in cr)
        aw = sum(1 for r in cr if r[6] < r[7] - 1e-9)
        bw = sum(1 for r in cr if r[7] < r[6] - 1e-9)
        tt = len(cr) - aw - bw
        emit(f"  {cat:9s} {len(cr):4d}  {ma:7.4f} {mb:7.4f} {mb-ma:+8.4f}  {aw:4d} {bw:4d} {tt:4d}")

    # ── By font size ───────────────────────────────────────────────────────
    emit("\n" + "-" * 78)
    emit("By font size")
    emit("-" * 78)
    fss = sorted(set(r[2] for r in rows))
    emit(f"  {'fs':>4s} {'n':>4s}  {'meanA':>7s} {'meanB':>7s} {'B-A':>8s}  {'Awin':>4s} {'Bwin':>4s}")
    for fs in fss:
        cr = [r for r in rows if r[2] == fs]
        ma = statistics.mean(r[6] for r in cr); mb = statistics.mean(r[7] for r in cr)
        aw = sum(1 for r in cr if r[6] < r[7] - 1e-9)
        bw = sum(1 for r in cr if r[7] < r[6] - 1e-9)
        emit(f"  {fs:4d} {len(cr):4d}  {ma:7.4f} {mb:7.4f} {mb-ma:+8.4f}  {aw:4d} {bw:4d}")

    # ── Box-count (det segmentation) difference ───────────────────────────
    # CER measures text; this measures whether the two configs CUT the image
    # into a different number of boxes - a direct det effect invisible to the
    # joined string (two small boxes vs one big box can join to the same text).
    nb_a = [len(r[8]["txts"]) for r in rows]
    nb_b = [len(r[9]["txts"]) for r in rows]
    same_nbox = sum(1 for a, b in zip(nb_a, nb_b) if a == b)
    a_more = sum(1 for a, b in zip(nb_a, nb_b) if a > b)
    b_more = sum(1 for a, b in zip(nb_a, nb_b) if b > a)
    emit("\n" + "-" * 78)
    emit("Box-count (det segmentation): does the config cut differently?")
    emit("-" * 78)
    emit(f"  A==B #boxes on: {same_nbox}/{len(rows)}  "
         f"(A more boxes: {a_more}, B more boxes: {b_more})")
    emit(f"  total boxes  A={sum(nb_a)}  B={sum(nb_b)}")
    nbox_diff = [(abs(a - b), r) for r, a, b in zip(rows, nb_a, nb_b) if a != b]
    nbox_diff.sort(key=lambda x: -x[0])
    for _, r in nbox_diff[:8]:
        rid, cat, fs, truth, ta, tb, ca, cb, raw_a, raw_b = r
        emit(f"\n  {rid} ({cat} fs={fs})  #boxes A={len(raw_a['txts'])} "
             f"B={len(raw_b['txts'])}  CER A={ca:.3f} B={cb:.3f}")
        emit(f"      A boxes: {raw_a['txts']}")
        emit(f"      B boxes: {raw_b['txts']}")

    # ── Detail: images where A != B ────────────────────────────────────────
    diff_rows = [r for r in rows if abs(r[6] - r[7]) > 1e-9]
    emit("\n" + "-" * 78)
    emit(f"Images where A != B (CER differs): {len(diff_rows)}/{len(rows)}")
    emit("-" * 78)
    for r in diff_rows[:40]:
        rid, cat, fs, truth, ta, tb, ca, cb = r[:8]
        winner = "A" if ca < cb - 1e-9 else ("B" if cb < ca - 1e-9 else "=")
        emit(f"\n  [{winner}] {rid} ({cat} fs={fs})  CER A={ca:.3f} B={cb:.3f}")
        emit(f"      truth: {truth[:90]}")
        if ta != tb:
            emit(f"      A:     {ta[:90]}")
            emit(f"      B:     {tb[:90]}")
        else:
            emit(f"      A==B:  {ta[:90]}  (same text, CER differs only via... "
                 f"check normalization)")
    if len(diff_rows) > 40:
        emit(f"\n  ... and {len(diff_rows)-40} more (A!=B)")

    emit("\n" + "=" * 78)
    emit(f"Verdict: {verdict}")
    emit("=" * 78)

    # ── Save full per-image RAW detail (boxes + per-box txts + scores) ──────
    # So any image can be audited at box level later - what det cut, what rec
    # read per box, the confidence.  No HushSnap layout logic touched this.
    detail = []
    for r, item in zip(rows, manifest):
        rid, cat, fs, truth, ta, tb, ca, cb, raw_a, raw_b = r
        detail.append({
            "id": rid, "category": cat, "font_size": fs,
            "short_side": min(item["meta"]["size"]) if "size" in item["meta"] else None,
            "truth": truth,
            "A_0.5": {"cer": ca, "n_boxes": len(raw_a["txts"]),
                      "txts": raw_a["txts"], "scores": raw_a["scores"]},
            "B_imagenet": {"cer": cb, "n_boxes": len(raw_b["txts"]),
                            "txts": raw_b["txts"], "scores": raw_b["scores"]},
        })
    detail_path = ds / "normalize_detail.json"
    detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    emit(f"\nFull per-image RAW detail (boxes + per-box txts + scores, no layout logic)")
    emit(f"saved to: {detail_path}")

    emit("\nCaveats:")
    emit("  - RAW RapidOCR output (res.boxes/txts/scores) - NOT routed through any")
    emit("    HushSnap layout/typography composer, so this measures det/rec params only.")
    emit("  - Chromium-rendered, not Windows DirectWrite pixels.  Normalization acts on")
    emit("    global pixel stats so this is acceptable for the mean/std question.")
    emit(f"  - {len(rows)} images per config.  Differences <0.2% are within noise.")
    emit("  - Per-image verdict counts images where CER differs at all (could be 1 char).")

    if args.report:
        rp = _project_root / args.report
        rp.write_text("\n".join(lines_out), encoding="utf-8")
        print(f"\nReport written to {rp}")


if __name__ == "__main__":
    main()
