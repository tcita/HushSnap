"""Measure left-edge x jitter across left-aligned CJK lines (one detection).

Question
--------
``_apply_indentation``'s sub-threshold column fallback clusters left edges with
a single-linkage tolerance of ``0.4 * median_h`` (reusing the jitter tolerance
``_greedy_line_cluster`` applies on the perpendicular axis).  That tolerance
must absorb the per-line left-edge jitter that exists even among lines that
*should* be perfectly left-aligned - the ``min(bounding_box.x)`` the fallback
clusters on.  This script measures how big that jitter is.

Three rendering modes span the detector's difficulty, controlled only by the
case's ``prefix`` and ``word_spacing_px`` (no production code touched):

  mixed            2-letter Latin prefix + 16px gap before every CJK word.
                   The Latin anchor gives the detector a sharp left boundary, so
                   this is the OPTIMISTIC baseline (suspected least jitter).
  pure_spaced      no prefix, 16px gap; every box is one 2-char CJK word.
                   Probes "recognizing a word" jitter - the case suspected to be
                   worse than mixed because there is no Latin anchor.
  pure_continuous  no prefix, 0px gap; a continuous CJK line the OCR segments
                   itself (~1 box/line).  Closest to production (e.g. a novel
                   passage): no Latin anchor, no forced gaps.

For every rendered line (one OCR cluster) it records::

    left_edge = min(box.left for box in cluster)   # = production min(bounding_box.x)
    median_h  = upper_median(box.heights)           # = the fallback's ruler

Across the lines of one image it computes the image-level spread::

    spread_px    = max(left_edge) - min(left_edge)   # conservative worst-case
    spread_ratio = spread_px / median_h               # gap the tolerance must bridge

plus the per-line deviation of each line's left edge from that image's median
left edge, pooled across images for higher-resolution statistics::

    dev_ratio = |left_edge - median_left_edge| / median_h

If spread_ratio stays well under 0.4, the tolerance comfortably absorbs real
per-line left-edge jitter.  If it approaches or exceeds 0.4, the tolerance is on
the edge and tightening (e.g. 0.25x) is worth discussing.

Re-running the same image is deterministic and measures nothing - jitter is
sampled across many left-aligned lines in ONE detection per image.

Usage:
    python scripts/measure_leftedge_drift.py
    python scripts/measure_leftedge_drift.py --modes pure_continuous
    python scripts/measure_leftedge_drift.py --sizes 16,20 --lines 10
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))


# ── Modes ────────────────────────────────────────────────────────────────────
# (mode_name, prefix, word_spacing_px, blurb).  prefix="" + spacing=0 yields a
# continuous CJK line (spacer spans render at width 0, words flush); prefix=""
# + spacing>0 yields pure-CJK word boxes; a Latin prefix is the mixed baseline.

_MODES = [
    ("mixed",           "ax", 16, "Latin prefix + gap (optimistic baseline)"),
    ("pure_spaced",     "",   16, "pure CJK, one 2-char word per box"),
    ("pure_continuous", "",    0, "pure CJK continuous, OCR self-segments (prod-like)"),
]

_CJK_FAMILIES = ["Microsoft YaHei", "SimSun", "KaiTi"]
_FONT_SIZES = [16, 20, 24, 28, 32]


def _build_case(mode: str, prefix: str, spacing: int,
                fs: int, fam: str, n_lines: int, words_per_line: int):
    from ocr_layout.cases import LineClusteringCase

    return LineClusteringCase(
        id=f"leftdrift_{mode}_{fs}_{fam.replace(' ', '')}",
        font_size_px=fs,
        line_height_ratio=1.5,
        font_family=fam,
        n_lines=n_lines,
        words_per_line=words_per_line,
        word_spacing_px=spacing,
        is_vertical=False,
        use_cjk=True,
        prefix=prefix,
        word_offset=0,
    )


# ── Per-line measurement ─────────────────────────────────────────────────────


def _upper_median(values: list[float]) -> float:
    """High median: sorted[n // 2], matching _apply_indentation's ruler."""
    if not values:
        return 0.0
    s = sorted(values)
    return s[len(s) // 2]


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _measure_image(png_path: Path, engine) -> dict | None:
    """One detection on a multi-line PNG; return per-line left-edge record.

    Each rendered line becomes one OCR cluster.  For every cluster record the
    line's left edge (min box.left) and median height.  Single-box lines have
    no internal left-edge to min meaningfully but are still valid left edges -
    include them.
    """
    from ocr_layout.pipeline import run_pipeline

    pr = run_pipeline(png_path, engine)
    if not pr.boxes:
        return None

    by_cluster: dict[int, list] = {}
    for b in pr.boxes:
        by_cluster.setdefault(b.cluster_id, []).append(b)

    lines = []
    for cid in sorted(by_cluster):
        boxes = by_cluster[cid]
        lefts = [b.left for b in boxes]
        heights = [b.height for b in boxes]
        lines.append({
            "cluster_id": cid,
            "n_boxes": len(boxes),
            "left_edge": min(lefts),
            "median_h": _upper_median(heights),
        })
    if len(lines) < 2:
        return None  # need >=2 lines to measure across-line spread

    lefts = [ln["left_edge"] for ln in lines]
    median_h_overall = statistics.median(ln["median_h"] for ln in lines)
    if median_h_overall <= 0:
        return None
    median_left = statistics.median(lefts)
    spread_px = max(lefts) - min(lefts)
    # per-line |deviation| from this image's median left edge
    devs = [abs(l - median_left) for l in lefts]
    return {
        "png": png_path.name,
        "n_lines": len(lines),
        "left_edges": lefts,
        "median_h": median_h_overall,
        "spread_px": spread_px,
        "spread_ratio": spread_px / median_h_overall,
        "devs_px": devs,
        "devs_ratio": [d / median_h_overall for d in devs],
        "lines": lines,
    }


# ── Reporting ────────────────────────────────────────────────────────────────


def _report(label: str, records: list[dict], tol_ratio: float) -> None:
    if not records:
        print(f"\n{label}: no multi-line cases")
        return

    spreads_px = sorted(r["spread_px"] for r in records)
    spreads_ratio = sorted(r["spread_ratio"] for r in records)
    all_devs_ratio = sorted(d for r in records for d in r["devs_ratio"])
    all_devs_px = sorted(d for r in records for d in r["devs_px"])
    n = len(records)
    n_dev = len(all_devs_ratio)

    print(f"\n{'═' * 78}")
    print(f"{label}  (n={n} images, {n_dev} lines)")
    print(f"{'═' * 78}")
    print(f"  image-level spread  (max(left) - min(left))   [conservative worst-case]")
    print(f"  px:    median={statistics.median(spreads_px):.2f}  "
          f"p75={_pct(spreads_px,75):.2f}  p95={_pct(spreads_px,95):.2f}  "
          f"max={max(spreads_px):.2f}")
    print(f"  ratio: median={statistics.median(spreads_ratio)*1000:.1f}e-3  "
          f"p75={_pct(spreads_ratio,75)*1000:.1f}e-3  "
          f"p95={_pct(spreads_ratio,95)*1000:.1f}e-3  "
          f"max={max(spreads_ratio)*1000:.1f}e-3")
    print(f"  per-line |dev| from image-median left edge    [high-resolution]")
    print(f"  px:    median={statistics.median(all_devs_px):.2f}  "
          f"p75={_pct(all_devs_px,75):.2f}  p95={_pct(all_devs_px,95):.2f}  "
          f"max={max(all_devs_px):.2f}")
    print(f"  ratio: median={statistics.median(all_devs_ratio)*1000:.1f}e-3  "
          f"p75={_pct(all_devs_ratio,75)*1000:.1f}e-3  "
          f"p95={_pct(all_devs_ratio,95)*1000:.1f}e-3  "
          f"max={max(all_devs_ratio)*1000:.1f}e-3")
    print(f"  (ratio = value / median_h; tolerance = {tol_ratio:.2f} x median_h)")

    over = sum(1 for s in spreads_ratio if s > tol_ratio)
    half = sum(1 for s in spreads_ratio if s > tol_ratio / 2)
    over_dev = sum(1 for d in all_devs_ratio if d > tol_ratio / 2)
    print(f"  images with spread_ratio > {tol_ratio:.2f}:              {over}/{n}")
    print(f"  images with spread_ratio > {tol_ratio/2:.2f} (half tol):  {half}/{n}")
    print(f"  lines  with |dev|_ratio  > {tol_ratio/2:.2f} (half tol):  {over_dev}/{n_dev}")

    worst = sorted(records, key=lambda r: -r["spread_ratio"])[:6]
    print(f"\n  worst images (spread_ratio desc):")
    print(f"    {'spread_px':>10} {'spread_x':>9} {'median_h':>9} {'n_lines':>8}  image")
    for r in worst:
        print(f"    {r['spread_px']:>10.2f} {r['spread_ratio']*1000:>8.1f}e-3 "
              f"{r['median_h']:>9.1f} {r['n_lines']:>8}  {r['png']}")


def _decision(records: list[dict], tol_ratio: float) -> None:
    if not records:
        return
    spreads_ratio = sorted(r["spread_ratio"] for r in records)
    spreads_px = sorted(r["spread_px"] for r in records)
    all_devs_ratio = sorted(d for r in records for d in r["devs_ratio"])
    p95 = _pct(spreads_ratio, 95)
    mx = max(spreads_ratio)
    med = statistics.median(spreads_ratio)
    med_px = statistics.median(spreads_px)
    dev_p95 = _pct(all_devs_ratio, 95)
    dev_max = max(all_devs_ratio)

    print(f"\n{'━' * 78}")
    print("DECISION SUMMARY")
    print(f"{'━' * 78}")
    print(f"  per-line left-edge jitter (one detection per image):")
    print(f"  image spread  median={med*1000:.1f}e-3 ({med_px:.2f}px)  "
          f"p95={p95*1000:.1f}e-3 ({_pct(spreads_px,95):.2f}px)  "
          f"max={mx*1000:.1f}e-3 ({max(spreads_px):.2f}px)")
    print(f"  line |dev|    p95={dev_p95*1000:.1f}e-3  "
          f"max={dev_max*1000:.1f}e-3  (2x max = {dev_max*2*1000:.1f}e-3)")
    print(f"  tolerance under test: {tol_ratio:.2f} x median_h")
    print()
    # Single-linkage splits a column when a consecutive gap > tol.  Image spread
    # is an upper bound on that gap; a per-line |dev| > tol/2 means two lines
    # straddling the median could be tol apart.  Judge on the worst of the two.
    worst = max(mx, dev_max * 2)
    if worst < tol_ratio / 2:
        print(f"  -> worst-case (spread, 2x line-dev) is under HALF the tolerance.")
        print(f"     {tol_ratio}x comfortably absorbs real per-line left-edge jitter;")
        print("     the slack is ample.")
        print("     Consider tightening toward 0.25x only if adjacent columns are")
        print("     seen merging in practice.")
    elif worst < tol_ratio:
        print(f"  -> worst-case is below but within 2x of the tolerance.  {tol_ratio}x")
        print("     absorbs the bulk of jitter; worst cases sit near the edge but")
        print("     inside.  Stands as-is.")
    else:
        print(f"  -> worst-case reaches or exceeds the tolerance.  {tol_ratio}x may be")
        print("     too TIGHT for the worst cases - a real column whose lines jitter")
        print("     this much could split across the tolerance and be missed.")
        print("     Consider widening, or clustering on a per-image measured jitter")
        print("     instead of a fixed ratio.")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--modes", type=str, default="",
                    help="comma-separated modes: mixed,pure_spaced,pure_continuous")
    ap.add_argument("--lines", type=int, default=8,
                    help="lines per image (default 8)")
    ap.add_argument("--words", type=int, default=6,
                    help="CJK words per line (default 6)")
    ap.add_argument("--sizes", type=str, default="",
                    help="comma-separated font sizes px (default 16,20,24,28,32)")
    ap.add_argument("--families", type=str, default="",
                    help="comma-separated CJK font families")
    ap.add_argument("--tol", type=float, default=0.4,
                    help="tolerance ratio to compare against (default 0.4)")
    args = ap.parse_args()

    mode_map = {m[0]: m for m in _MODES}
    sel_modes = ([m for m in args.modes.split(",") if m.strip()] if args.modes
                 else [m[0] for m in _MODES])
    for m in sel_modes:
        if m not in mode_map:
            ap.error(f"unknown mode {m!r}; choices: {list(mode_map)}")
    sizes = ([int(s) for s in args.sizes.split(",") if s.strip()] if args.sizes
             else list(_FONT_SIZES))
    families = ([f.strip() for f in args.families.split(",") if f.strip()]
                if args.families else list(_CJK_FAMILIES))

    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    from ocr_layout.pipeline import get_engine, release_engine
    from ocr_layout.render import render_cases

    out_dir = Path(_project_root) / "scratch" / "leftdrift_samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    # clear this script's own stale artifacts (regenerable synthetic PNGs only)
    for old in out_dir.glob("leftdrift_*.png"):
        old.unlink()

    # Build all cases across selected modes (one Chromium launch, one engine init).
    tagged: list[tuple[str, object]] = []
    for mode in sel_modes:
        _, prefix, spacing, _ = mode_map[mode]
        for fs in sizes:
            for fam in families:
                tagged.append((mode, _build_case(
                    mode, prefix, spacing, fs, fam, args.lines, args.words)))

    print(f"Rendering {len(tagged)} images across modes {sel_modes} "
          f"({args.lines} lines x {args.words} words, {len(sizes)} sizes x "
          f"{len(families)} families) -> {out_dir}")
    rr_list = render_cases([c for _, c in tagged], out_dir, channel="msedge")
    print("Render done.  Initialising OCR engine.\n")

    engine = get_engine()
    print("Engine ready.  Running one OCR detection per image.\n")

    all_records: list[dict] = []
    for (mode, case), rr in zip(tagged, rr_list):
        rec = _measure_image(rr.png_path, engine)
        if rec is None:
            print(f"  skip (no multi-line result): {case.id}")
            continue
        rec["mode"] = mode
        all_records.append(rec)
        print(f"  {case.id:46} lines={rec['n_lines']}  "
              f"spread={rec['spread_px']:5.2f}px "
              f"({rec['spread_ratio']*1000:6.1f}e-3 x h)  h={rec['median_h']:5.1f}")
    release_engine()

    for mode in sel_modes:
        _report(f"MODE: {mode}", [r for r in all_records if r["mode"] == mode],
                args.tol)
    # per-size / per-family breakdown across pure modes only (the realistic ones)
    pure = [r for r in all_records if r["mode"] in ("pure_spaced", "pure_continuous")]
    if pure:
        by_size: dict[str, list[dict]] = {}
        by_fam: dict[str, list[dict]] = {}
        for r in pure:
            stem = r["png"].split(".")[0]          # leftdrift_pure_spaced_16_SimSun
            segs = stem.split("_")
            fs = next((s for s in segs if s.isdigit()), "?")
            fam = stem.split(f"_{fs}_")[-1]
            by_size.setdefault(fs, []).append(r)
            by_fam.setdefault(fam, []).append(r)
        for fs in sorted(by_size, key=lambda x: int(x) if x.isdigit() else 0):
            _report(f"PURE modes, size: {fs}px", by_size[fs], args.tol)
        for fam in sorted(by_fam):
            _report(f"PURE modes, family: {fam}", by_fam[fam], args.tol)
    if len(sel_modes) > 1:
        _report("ALL MODES COMBINED", all_records, args.tol)
    _decision(all_records, args.tol)
    print("\nDone.")


if __name__ == "__main__":
    main()
