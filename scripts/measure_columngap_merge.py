"""Measure at what column gap the 0.4x single-link tolerance false-merges.

Question
--------
``_apply_indentation``'s sub-threshold column fallback clusters left edges
with single-linkage tolerance ``0.4 * median_h``.  The companion script
``measure_leftedge_drift.py`` showed that real per-line jitter (<=3px, ~0.1x)
is far below 0.4x, so the tolerance does NOT split a true column.  This script
answers the OTHER half: does 0.4x wrongly MERGE two genuinely separate columns
that sit too close together?

We render two left-aligned columns in one image - a body column (left edge 0)
and an indent column (left edge = ``gap`` px) - then sweep ``gap`` from sub-
tolerance to ~1 line-height.  For each image we take every line's left edge
(``min(box.left)`` per cluster), run the *same* single-link clustering the
fallback uses, and count clusters.  Two clusters = columns kept separate
(correct); one cluster = false merge (the failure we are hunting).

Why this stays in the fallback's domain: the gap is swept only up to ~1x
median_h (sub-character indent).  At >=1x the strict ``>1.0`` ratio rule
already fires and the fallback never runs - so testing beyond that range is
irrelevant to the fallback's behaviour.

For every gap value we also report the *measured* left-edge gap (rendered gap
is not what OCR sees - first-glyph side-bearing and detection jitter shift it),
so the merge threshold is expressed in the real geometry, not the requested gap.

Usage:
    python scripts/measure_columngap_merge.py
    python scripts/measure_columngap_merge.py --sizes 24,32
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


# ── Case construction ────────────────────────────────────────────────────────
#
# Two columns in one .block: half the lines flush-left (body, margin-left 0),
# half indented by `gap` px.  Pure continuous CJK per line (no Latin anchor,
# no forced inter-word gaps) so the detector self-segments ~1 box/line - the
# production-like case where the fallback matters most.

_CJK_FAMILIES = ["Microsoft YaHei", "SimSun", "KaiTi"]
_FONT_SIZES = [24, 32]
# gap in px.  Spans sub-tolerance to ~1 line-height.  tol = 0.4 * median_h;
# at fs=24 median_h ~30 -> tol ~12; at fs=32 median_h ~40 -> tol ~16.
_GAPS = [4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32]

# Continuous CJK text (no spaces) so OCR segments it itself, one box per line.
_CJK_LINE = "我凯瑞甘发誓如果我爱过寒王这辈子不得好死"


def _build_html(gap: int, fs: int, fam: str, n_body: int, n_indent: int) -> str:
    lh = round(fs * 1.5, 1)
    rows = []
    for _ in range(n_body):
        rows.append(f'<div style="margin-left:0">{_CJK_LINE}</div>')
    for _ in range(n_indent):
        rows.append(f'<div style="margin-left:{gap}px">{_CJK_LINE}</div>')
    body = "".join(rows)
    css = (
        "* { margin:0; padding:0; box-sizing:border-box; }"
        "body { background:white; }"
        ".block { padding:4px 8px; }"
    )
    return (
        f'<!DOCTYPE html><html><head><meta charset="UTF-8">'
        f'<style>{css}</style></head><body>'
        f'<div class="block" style="font-size:{fs}px;line-height:{lh}px;'
        f"font-family:'{fam}',sans-serif;\">{body}</div></body></html>"
    )


def _render(cases: list[dict], out_dir: Path) -> list[Path]:
    """Render each case's HTML to a PNG via Playwright, return PNG paths in order."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    pngs: list[Path] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        ctx = browser.new_context(device_scale_factor=1)
        for c in cases:
            html_path = out_dir / f"_case_{c['id']}.html"
            png_path = out_dir / f"{c['id']}.png"
            html_path.write_text(c["html"], encoding="utf-8")
            page = ctx.new_page()
            page.goto(f"file:///{html_path.as_posix()}")
            page.wait_for_load_state("networkidle")
            rect = page.evaluate("""
            () => {
                const el = document.querySelector('.block');
                const r = el.getBoundingClientRect();
                return {x:r.x, y:r.y, w:r.width, h:r.height};
            }
            """)
            page.screenshot(path=str(png_path), clip={
                "x": rect["x"], "y": rect["y"],
                "width": rect["w"], "height": rect["h"],
            })
            page.close()
            html_path.unlink(missing_ok=True)
            pngs.append(png_path)
        ctx.close()
        browser.close()
    return pngs


# ── Single-link clustering (mirrors the fallback) ────────────────────────────


def _upper_median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[len(s) // 2]


def _single_link_clusters(values: list[float], tol: float) -> list[list[float]]:
    """Single-linkage clusters: sort, split where consecutive gap > tol."""
    if not values:
        return []
    s = sorted(values)
    clusters = [[s[0]]]
    for v in s[1:]:
        if v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return clusters


# ── Measurement ───────────────────────────────────────────────────────────────


def _measure(png_path: Path, engine, tol_ratio: float) -> dict | None:
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
        lines.append({
            "left_edge": min(b.left for b in boxes),
            "median_h": _upper_median([b.height for b in boxes]),
        })
    if len(lines) < 2:
        return None
    median_h = statistics.median(ln["median_h"] for ln in lines)
    if median_h <= 0:
        return None
    lefts = [ln["left_edge"] for ln in lines]
    tol = tol_ratio * median_h
    clusters = _single_link_clusters(lefts, tol)
    # measured gap between the two lowest and next cluster's min, if >=2 clusters
    measured_gap = (clusters[-1][0] - clusters[0][-1]) if len(clusters) >= 2 else 0.0
    return {
        "png": png_path.name,
        "n_lines": len(lines),
        "median_h": median_h,
        "tol": tol,
        "left_edges": lefts,
        "n_clusters": len(clusters),
        "merged": len(clusters) == 1,
        "measured_gap_px": measured_gap,
        "measured_gap_ratio": measured_gap / median_h,
    }


# ── Reporting ─────────────────────────────────────────────────────────────────


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _report_by_gap(records: list[dict], tol_ratio: float) -> None:
    print(f"\n{'═' * 78}")
    print(f"MERGE RATE BY REQUESTED GAP  (tolerance = {tol_ratio} x median_h)")
    print(f"{'═' * 78}")
    print(f"  {'gap_px':>7} {'n':>4} {'merged':>7} {'merge%':>7} "
          f"{'med_meas_gap':>13} {'p95_meas_gap':>13}")
    by_gap: dict[int, list[dict]] = {}
    for r in records:
        by_gap.setdefault(r["gap"], []).append(r)
    for gap in sorted(by_gap):
        rs = by_gap[gap]
        merged = sum(1 for r in rs if r["merged"])
        meas = sorted(r["measured_gap_ratio"] for r in rs if not r["merged"])
        med = statistics.median(meas) if meas else float("nan")
        p95 = _pct(meas, 95) if meas else float("nan")
        print(f"  {gap:>7} {len(rs):>4} {merged:>7} "
              f"{100*merged/len(rs):>6.0f}%  {med*1000:>11.0f}e-3  "
              f"{p95*1000:>11.0f}e-3")
    print("  (meas_gap = measured left-edge gap between the two clusters,")
    print("   in units of median_h; only over images that stayed separate)")


def _threshold_note(records: list[dict], tol_ratio: float) -> None:
    """Find the requested gap at which the merge rate crosses 50%."""
    by_gap: dict[int, list[dict]] = {}
    for r in records:
        by_gap.setdefault(r["gap"], []).append(r)
    rows = []
    for gap in sorted(by_gap):
        rs = by_gap[gap]
        merged = sum(1 for r in rs if r["merged"])
        rows.append((gap, 100 * merged / len(rs)))
    print(f"\n{'━' * 78}")
    print("MERGE THRESHOLD")
    print(f"{'━' * 78}")
    half_tol = tol_ratio / 2
    print(f"  tolerance = {tol_ratio} x median_h  (half = {half_tol} x)")
    print(f"  requested gap -> merge rate:")
    for gap, pct in rows:
        bar = "#" * int(pct / 5)
        flag = ""
        if pct >= 50:
            flag = "  <- majority merged"
        print(f"    {gap:>3}px  {pct:5.0f}%  {bar}{flag}")
    # smallest gap with <50% merge = safe lower bound for keeping columns apart
    safe = [g for g, p in rows if p < 50]
    unsafe = [g for g, p in rows if p >= 50]
    if safe and unsafe:
        print(f"\n  columns stay separate (merge<50%) down to gap ~{min(safe)}px")
        print(f"  columns merge (merge>=50%) at gap ~{max(unsafe)}px and below")
        print(f"  -> the 0.4x tolerance false-merges two real columns when their")
        print(f"     RENDERED gap is below ~{min(safe)}px "
              f"(measured-gap ratio near {half_tol} x median_h).")
    elif not unsafe:
        print("\n  no gap reached 50% merge - tolerance never false-merges in range")
    else:
        print("\n  all tested gaps merged - tolerance is too loose for this range")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", type=str, default="",
                    help="comma-separated font sizes px (default 24,32)")
    ap.add_argument("--gaps", type=str, default="",
                    help="comma-separated gap px (default 4..32)")
    ap.add_argument("--n-body", type=int, default=3, help="body lines per image")
    ap.add_argument("--n-indent", type=int, default=3, help="indent lines per image")
    ap.add_argument("--tol", type=float, default=0.4,
                    help="tolerance ratio (default 0.4)")
    args = ap.parse_args()

    sizes = ([int(s) for s in args.sizes.split(",") if s.strip()]
             if args.sizes else list(_FONT_SIZES))
    gaps = ([int(g) for g in args.gaps.split(",") if g.strip()]
            if args.gaps else list(_GAPS))

    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    out_dir = Path(_project_root) / "scratch" / "columngap_samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("columngap_*.png"):
        old.unlink()

    cases: list[dict] = []
    for gap in gaps:
        for fs in sizes:
            for fam in _CJK_FAMILIES:
                cid = f"columngap_{gap}_{fs}_{fam.replace(' ', '')}"
                cases.append({
                    "id": cid, "gap": gap, "fs": fs, "fam": fam,
                    "html": _build_html(gap, fs, fam, args.n_body, args.n_indent),
                })

    print(f"Rendering {len(cases)} two-column images "
          f"({len(gaps)} gaps x {len(sizes)} sizes x {len(_CJK_FAMILIES)} families) "
          f"-> {out_dir}")
    pngs = _render(cases, out_dir)
    print("Render done.  Initialising OCR engine.\n")

    from ocr_layout.pipeline import get_engine, release_engine
    engine = get_engine()
    print("Engine ready.  Running one OCR detection per image.\n")

    records: list[dict] = []
    for c, png in zip(cases, pngs):
        rec = _measure(png, engine, args.tol)
        if rec is None:
            print(f"  skip (no multi-line result): {c['id']}")
            continue
        rec["gap"] = c["gap"]
        rec["fs"] = c["fs"]
        rec["fam"] = c["fam"]
        records.append(rec)
        flag = "MERGE" if rec["merged"] else "ok   "
        print(f"  {c['id']:44} {flag}  n_clust={rec['n_clusters']} "
              f"meas_gap={rec['measured_gap_px']:5.1f}px "
              f"({rec['measured_gap_ratio']*1000:5.0f}e-3 x h)  h={rec['median_h']:5.1f}")
    release_engine()

    _report_by_gap(records, args.tol)
    _threshold_note(records, args.tol)
    print("\nDone.")


if __name__ == "__main__":
    main()
