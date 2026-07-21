"""Measure within-line box-height drift: union bbox vs word-box median.

Question being answered
-----------------------
``_apply_paragraph_breaks`` computes each line's height as the
*upper-median* of its word-box heights, deliberately avoiding the
line's *union* bbox, whose height (max(bottom) - min(top)) is inflated
by any single drifting box on that line.  This script measures how
big that inflation actually is on realistic rendered text.

For every OCR cluster (one "line") it compares two height estimates:

    union_h       = max(bottom) - min(top)   # what line.bounding_box.height is
    word_median_h = sorted(word_heights)[n // 2]   # high-median, as in the code

If ``union_h - word_median_h`` is negligible across realistic mixed
content, the union bbox could replace the median and the paragraph-break
logic could treat ``line.bounding_box`` as the single line abstraction
(as it already does for the centre).  If the inflation is material,
the median is earning its keep.

The cases deliberately mix token shapes that produce different
detection-box heights on a shared baseline: punctuation, mixed case
(descenders 'g','y','p'), Latin+CJK, and a smaller-font inline run.
Uniform-height cases (BoxHeightCase) would show near-zero drift and
cannot answer the question.

Usage:
    python scripts/measure_within_line_drift.py
"""

from __future__ import annotations

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
# Each case is one rendered line whose tokens deliberately span box-height
# classes.  Every token is prefixed with a unique 2-letter tag so the OCR
# matcher can map detected boxes back to their token unambiguously.

_LATIN_FAMILIES = ["Arial", "Segoe UI", "Consolas", "Times New Roman"]
_CJK_FAMILIES = ["Microsoft YaHei", "SimSun", "KaiTi"]
_ALL_FAMILIES = _LATIN_FAMILIES + _CJK_FAMILIES

_FONT_SIZES = [14, 16, 20, 24, 32]

# 2-letter tags, one per token across all cases so matching is unambiguous.
_TAGS = [
    "ax", "by", "cz", "dw", "ev", "fu", "gr", "hs", "it", "jq",
    "kp", "lm", "nz", "oc", "pd", "qf", "rg", "si", "tj", "uk",
    "vl", "wm", "xn", "yo", "zp", "aq", "br", "cs", "dt", "eu",
    "fv", "gw", "hx", "iy", "jz", "ka", "lb", "mc", "nd", "oe",
    "pf", "qg", "rh", "sb", "tc", "ud", "ve", "wf", "xg", "yh",
]


def _tagged(tokens: list[str], start: int) -> list[str]:
    out = []
    for i, t in enumerate(tokens):
        out.append(f"{_TAGS[(start + i) % len(_TAGS)]}{t}")
    return out


def _make_cases() -> list:
    """Build mixed-content line cases across font families and sizes.

    Every token is a full word/character (not a lone punctuation mark) so
    the OCR detector reliably emits one box per token.  Within-line box-
    height drift is induced by mixing font *sizes* and *scripts* on a
    shared baseline - the two sources that survive detection reliably.
    Lone punctuation is a weak probe because the detector often merges or
    drops it, so it is not used as a primary drift source.
    """
    from ocr_layout.cases import MixedLineCase

    cases: list = []
    tag_cursor = 0

    # (name, tokens, small_indices, small_fs_ratio)
    # small_fs_ratio: small-run font size as a fraction of the main size.
    templates: list[tuple[str, list[str], list[int], float]] = [
        # Mixed font sizes on one baseline.  The most reliable drift
        # source: a smaller-font inline run (footnote / superscript-like)
        # produces strictly shorter detection boxes than the main run.
        ("mixedsize",
         ["Title", "note", "Body", "ref", "Tail"],
         [1, 3], 0.6),
        # Latin words + CJK characters, same font size.  CJK detection
        # boxes typically run a few px taller than Latin at the same
        # nominal font-size, so a mixed line has inherent box-height spread.
        ("latincjk",
         ["result", "中文", "value", "测试", "data", "算法"],
         [], 0.0),
        # Descenders vs all-caps: lowercase words with g/y/p push box
        # bottoms down; all-caps words are full cap-height.  Same script,
        # natural height spread from glyph shape.
        ("descenders",
         ["ghost", "QUICK", "jumps", "LAZY", "pygmy", "BOLD"],
         [], 0.0),
        # Pure CJK control: uniform script and metrics -> expect near-zero
        # drift.  Anchors the "no drift" end of the distribution.
        ("cjk_control",
         ["中文", "排版", "测试", "识别", "聚类", "算法"],
         [], 0.0),
        # CJK with trailing full-width punctuation on some tokens.  Punctuation
        # here rides on a real word (你好，) rather than standing alone, so the
        # detector still emits a usable box; the comma may nudge the box bottom.
        ("cjk_punct",
         ["你好，", "世界", "今天", "晴天。", "再见", "朋友"],
         [], 0.0),
    ]

    for fs in _FONT_SIZES:
        for fam in _ALL_FAMILIES:
            for name, tokens, small_idx, small_ratio in templates:
                tagged = _tagged(tokens, tag_cursor)
                tag_cursor += len(tokens)
                small_fs = round(fs * small_ratio) if small_ratio else 0
                cases.append(MixedLineCase(
                    id=f"drift_{name}_{fs}_{fam.replace(' ', '')}",
                    font_size_px=fs,
                    font_family=fam,
                    tokens=tagged,
                    small_token_indices=small_idx,
                    small_font_size_px=small_fs,
                    prefix=_TAGS[tag_cursor % len(_TAGS)],
                ))

    return cases


# ── Per-line measurement ─────────────────────────────────────────────────────


def _upper_median(values: list[float]) -> float:
    """High median: sorted[n // 2], matching _apply_paragraph_breaks."""
    if not values:
        return 0.0
    s = sorted(values)
    return s[len(s) // 2]


def _measure_lines(pr) -> list[dict]:
    """Group a pipeline result's boxes by cluster; compute both heights.

    Returns one record per cluster with >=2 boxes (a single-box line has
    no within-line drift to measure - union == median by definition).
    """
    by_cluster: dict[int, list] = {}
    for b in pr.boxes:
        by_cluster.setdefault(b.cluster_id, []).append(b)

    records = []
    for cid, boxes in sorted(by_cluster.items()):
        if len(boxes) < 2:
            continue
        tops = [b.top for b in boxes]
        bots = [b.bottom for b in boxes]
        heights = [b.height for b in boxes]
        union_h = max(bots) - min(tops)
        word_median_h = _upper_median(heights)
        if word_median_h <= 0:
            continue
        records.append({
            "cluster_id": cid,
            "n_boxes": len(boxes),
            "union_h": union_h,
            "word_median_h": word_median_h,
            "delta_px": union_h - word_median_h,
            "delta_ratio": (union_h - word_median_h) / word_median_h,
            "sample_text": " ".join(b.text[:20] for b in boxes[:4]),
        })
    return records


# ── Reporting ────────────────────────────────────────────────────────────────


def _pct(sorted_vals: list[float], p: float) -> float:
    """Percentile p in [0,100] of a sorted list."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _report(label: str, records: list[dict]) -> None:
    if not records:
        print(f"\n{label}: no multi-box lines")
        return

    deltas_px = sorted(r["delta_px"] for r in records)
    deltas_ratio = sorted(r["delta_ratio"] for r in records)
    n = len(records)

    # Fraction of lines where union inflates height at all (delta > 0).
    inflated = sum(1 for d in deltas_px if d > 0.5)  # >0.5px = beyond rounding
    # Fraction with material inflation: delta >= 2px (the granularity at
    # which the paragraph-break threshold meaningfully moves).
    material_px = sum(1 for d in deltas_px if d >= 2.0)
    material_pct = sum(1 for d in deltas_ratio if d >= 0.10)

    print(f"\n{'═' * 78}")
    print(f"{label}  (n={n} multi-box lines)")
    print(f"{'═' * 78}")
    print(f"  delta = union_h - word_median_h")
    print(f"  px:    median={statistics.median(deltas_px):+.2f}  "
          f"p75={_pct(deltas_px,75):+.2f}  p95={_pct(deltas_px,95):+.2f}  "
          f"max={max(deltas_px):+.2f}")
    print(f"  ratio: median={statistics.median(deltas_ratio)*100:+.1f}%  "
          f"p75={_pct(deltas_ratio,75)*100:+.1f}%  "
          f"p95={_pct(deltas_ratio,95)*100:+.1f}%  "
          f"max={max(deltas_ratio)*100:+.1f}%")
    print(f"  inflated (>0.5px):    {inflated}/{n}  ({100*inflated/n:.0f}%)")
    print(f"  material (>=2px):     {material_px}/{n}  ({100*material_px/n:.0f}%)")
    print(f"  material (>=10%):     {material_pct}/{n}  ({100*material_pct/n:.0f}%)")

    # Worst offenders - the lines where union would most distort the
    # paragraph-break threshold (threshold = h1/2+h2/2+max(h1,h2), so a
    # delta of Dpx raises the threshold by up to 1.5*Dpx).
    worst = sorted(records, key=lambda r: -r["delta_px"])[:8]
    print(f"\n  worst offenders (delta_px desc):")
    print(f"    {'delta_px':>9} {'delta_%':>8} {'n':>3} {'union':>7} {'median':>7}  text")
    for r in worst:
        print(f"    {r['delta_px']:>+9.2f} {r['delta_ratio']*100:>+7.1f}% "
              f"{r['n_boxes']:>3} {r['union_h']:>7.1f} {r['word_median_h']:>7.1f}  "
              f"{r['sample_text'][:48]}")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    from ocr_layout.pipeline import get_engine, release_engine, run_pipeline
    from ocr_layout.render import render_cases

    cases = _make_cases()
    print(f"Cases: {len(cases)} mixed-content lines "
          f"({_len_cases_breakdown(cases)})")

    engine = get_engine()
    print("Engine ready.\n")

    # Persistent output so the synthetic PNGs can be inspected by hand.
    # scratch/ is git-ignored (dev scratch only), so this does not pollute
    # the tracked tree.
    out_dir = Path(_project_root) / "scratch" / "drift_samples"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_records: list[dict] = []
    by_template: dict[str, list[dict]] = {}

    rr_list = render_cases(cases, out_dir, channel="msedge")
    for rr, case in zip(rr_list, cases):
        pr = run_pipeline(rr.png_path, engine)
        records = _measure_lines(pr)

        # Per-case box dump alongside the PNG, for visual cross-checking
        # of how the detector split the line into boxes and their heights.
        dump_path = out_dir / f"{case.id}.boxes.txt"
        with dump_path.open("w", encoding="utf-8") as fh:
            fh.write(f"case: {case.id}\n")
            fh.write(f"family={case.font_family}  main_fs={case.font_size_px}"
                     f"  small_fs={case.small_font_size_px}\n")
            fh.write(f"tokens: {case.tokens}\n\n")
            by_cluster: dict[int, list] = {}
            for b in pr.boxes:
                by_cluster.setdefault(b.cluster_id, []).append(b)
            for cid in sorted(by_cluster):
                boxes = by_cluster[cid]
                fh.write(f"cluster[{cid}]  n={len(boxes)}\n")
                tops = [b.top for b in boxes]
                bots = [b.bottom for b in boxes]
                union_h = max(bots) - min(tops)
                med_h = _upper_median([b.height for b in boxes])
                fh.write(f"  union_h={union_h:.1f}  word_median_h={med_h:.1f}"
                         f"  delta={union_h - med_h:+.1f}px\n")
                for b in boxes:
                    fh.write(f"    h={b.height:6.1f}  top={b.top:6.1f}"
                             f"  bot={b.bottom:6.1f}  {b.text[:30]!r}\n")
                fh.write("\n")

        all_records.extend(records)
        # Group by template name (first segment of the id).
        tmpl = case.id.split("_")[1]
        by_template.setdefault(tmpl, []).extend(records)

    print(f"\nSynthetic PNGs + per-case box dumps written to: {out_dir}\n")

    # Overall
    _report("ALL TEMPLATES COMBINED", all_records)

    # Per-template breakdown - shows which content type drives inflation.
    for tmpl in sorted(by_template):
        _report(f"template: {tmpl}", by_template[tmpl])

    # ── Decision summary ───────────────────────────────────────────────────
    if all_records:
        deltas_px = sorted(r["delta_px"] for r in all_records)
        deltas_ratio = sorted(r["delta_ratio"] for r in all_records)
        med_px = statistics.median(deltas_px)
        med_ratio = statistics.median(deltas_ratio)
        p95_ratio = _pct(deltas_ratio, 95)
        material_pct = sum(1 for d in deltas_px if d >= 2.0) / len(deltas_px)

        print(f"\n{'━' * 78}")
        print("DECISION SUMMARY")
        print(f"{'━' * 78}")
        print(f"  median inflation:  {med_px:+.2f} px  ({med_ratio*100:+.1f}%)")
        print(f"  p95 inflation:     {_pct(deltas_px,95):+.2f} px  ({p95_ratio*100:+.1f}%)")
        print(f"  lines with >=2px inflation: {100*material_pct:.0f}%")
        print()
        if material_pct < 0.05 and med_ratio < 0.05:
            print("  -> Drift is negligible.  union bbox can replace the")
            print("     word-box median in _apply_paragraph_breaks without")
            print("     materially moving the break threshold.")
        elif material_pct < 0.25:
            print("  -> Drift is modest.  union bbox would widen the break")
            print("     threshold on a minority of lines; the median is a")
            print("     marginal but real safeguard.  Judgement call.")
        else:
            print("  -> Drift is material.  union bbox systematically inflates")
            print("     the break threshold; the word-box median is earning")
            print("     its keep and should be retained.")

    release_engine()
    print("\nDone.")


def _len_cases_breakdown(cases) -> str:
    from collections import Counter
    by_template = Counter(c.id.split("_")[1] for c in cases)
    return ", ".join(f"{k}={v}" for k, v in sorted(by_template.items()))


if __name__ == "__main__":
    main()
