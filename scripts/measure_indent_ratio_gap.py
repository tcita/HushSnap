"""Measure the ratio gap between body and 1-em indent; assert the 0.5 threshold.

Question
--------
``_apply_indentation``'s main rule judges each line by::

    ratio = (bounding_box.x - baseline) / line_height

where ``baseline`` is the leftmost box edge (``min(bounding_box.x)`` over all
non-sentinel lines) and ``line_height`` is the line's own upper-median word-box
height (``_word_upper_median(axis='h')``).  A line is indented when
``ratio > 0.5`` (lowered from ``>1.0`` on master, commit 24ff563).

The ``>1.0`` threshold missed every 1-char CJK indent: a 1-em offset
(~= font size) divided by the detection-box height (which runs 1.2-1.7x
font size - larger, but not a fixed ratio: it rises at small sizes and for
narrow glyphs; the only provable relationship is box > font size, so a 1-em
indent lands well under 1.0) lands near ``0.83`` (for CJK at typical sizes).
Could the threshold be lowered to 0.5 - small enough to catch a 1-em indent, large
enough to never false-fire on top-aligned body jitter?

This script answers that directly with an explicit two-claim assertion:

  * body   (flush-left): max ratio < 0.5   -> never false-fires
  * indent (1 em):              min ratio > 0.5   -> shallowest indent caught

backed by enough data that the claim is not a tuned fraction.  It mirrors
production exactly::

    baseline = min(left_edge) over all lines in the image
    offset   = left_edge - baseline           (left_edge = min(box.left))
    h        = upper_median(box.heights)       (the per-line ruler)
    ratio    = offset / h

It pools ``ratio`` across images split by body vs indent, per content stratum
and per font size, and reports the two distributions plus the verdict at 0.5.

Content - five strata spanning the typographies whose geometry differs:

  zh-Hans / zh-Hant / ja   continuous CJK / kana, 1 char == 1 em (full-width
                           glyph).  The 1-char-indent concept is native here.
  en                       spaced Latin; a line yields several word boxes and
                           min(box.left) gives the line edge as production does.
  digits                   comma-separated digit groups, no spaces; narrow
                           glyphs (digit box ~0.55 em) stress the denominator
                           - the narrowest-valley case.

Indent unit is 1 em = ``font_size_px`` (CSS ``margin-left``) for every stratum
- a full-width glyph for CJK/JP (literally one character) and the typographic
em unit for Latin/digits.  The detection-box height inflation that sets the
ratio denominator is font-size-proportional for every script, so the ratio
math transfers.

No hard pass/fail exit code - this is a measurement for human judgement.

Usage:
    python scripts/measure_indent_ratio_gap.py
    python scripts/measure_indent_ratio_gap.py --content zh-Hans,en --sizes 24
    python scripts/measure_indent_ratio_gap.py --content digits
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


# ── Content strata ───────────────────────────────────────────────────────────
#
# One representative line per stratum, repeated per image (controlled - the
# measurement is geometric, not content-dependent).  CJK/Japanese are
# continuous (no spaces) so the OCR self-segments ~1 box/line, the
# production-like case; English is spaced; digits are comma-grouped (no
# spaces) so a line may split into several boxes - all exercise min(box.left)
# as the line edge exactly as production does on a mixed box count.  Families
# are those confirmed installed on the Windows box.

_CONTENT = [
    {
        "code": "zh-Hans", "label": "Simplified Chinese",
        "families": ["Microsoft YaHei", "SimSun", "SimHei"],
        "line": "测试简体中文文本缩进检测阈值的统计分布与稳定性",
    },
    {
        "code": "zh-Hant", "label": "Traditional Chinese",
        "families": ["Microsoft JhengHei"],
        "line": "測試繁體中文文字縮進檢測閾值的統計分佈與穩定性",
    },
    {
        "code": "ja", "label": "Japanese",
        "families": ["Yu Gothic", "MS Gothic"],
        "line": "日本語テキストのインデント検出閾値の統計分布と安定性",
    },
    {
        "code": "en", "label": "English (Latin)",
        "families": ["Arial", "Times New Roman", "Consolas", "Georgia"],
        "line": "The quick brown fox jumps over the lazy dog every day now",
    },
    {
        "code": "digits", "label": "Pure digits",
        "families": ["Consolas", "Constantia", "Arial", "Times New Roman",
                     "Georgia", "Microsoft YaHei"],
        "line": "3,14159265,3589793,23846264,33832795,02884197",
    },
]

_FONT_SIZES = [16, 20, 24, 28, 32]
_LINE_HEIGHTS = [1.3, 1.5, 1.8]
_THRESHOLD = 0.5


def _build_html(fs: int, fam: str, lh: float, line: str,
                n_body: int, n_indent: int) -> str:
    rows = []
    for _ in range(n_body):
        rows.append(f'<div style="margin-left:0">{line}</div>')
    # 1 em = font_size_px (CJK full-width glyph == font size; Latin em unit).
    for _ in range(n_indent):
        rows.append(f'<div style="margin-left:{fs}px">{line}</div>')
    body = "".join(rows)
    css = (
        "* { margin:0; padding:0; box-sizing:border-box; }"
        "body { background:white; }"
        ".block { padding:4px 8px; }"
    )
    # line-height must be a px value, not a unitless ratio: `line-height:1.5px`
    # would collapse every line to 1.5 px.  Convert ratio -> px (== the existing
    # sibling scripts' `lh = round(fs * 1.5, 1)` convention).
    lh_px = round(fs * lh, 1)
    return (
        f'<!DOCTYPE html><html><head><meta charset="UTF-8">'
        f'<style>{css}</style></head><body>'
        f'<div class="block" style="font-size:{fs}px;line-height:{lh_px}px;'
        f"font-family:'{fam}',sans-serif;\">{body}</div></body></html>"
    )


def _render(cases: list[dict], out_dir: Path) -> list[Path]:
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


# ── Measurement (mirrors production master rule) ────────────────────────────


def _upper_median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[len(s) // 2]


def _measure(png_path: Path, engine, n_body: int) -> dict | None:
    """One detection; return per-line ratio records split body/indent.

    baseline = min(left_edge) over all lines (production definition; these
    synthetic images have no paragraph-break sentinels).  Each line's h is
    the upper-median of its own box heights (production ruler).  The first
    n_body lines are body (flush-left), the rest are 1-em indent.
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
        lines.append({
            "left_edge": min(b.left for b in boxes),
            "h": _upper_median([b.height for b in boxes]),
        })
    if len(lines) < 2:
        return None
    baseline = min(ln["left_edge"] for ln in lines)
    for ln in lines:
        ln["offset"] = ln["left_edge"] - baseline
        ln["ratio"] = ln["offset"] / ln["h"] if ln["h"] > 0 else 0.0
    body = lines[:n_body]
    indent = lines[n_body:]
    return {"png": png_path.name, "body": body, "indent": indent}


# ── Reporting ────────────────────────────────────────────────────────────────


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _hist(values: list[float], lo: float, hi: float, bins: int = 24) -> str:
    if not values:
        return "(none)"
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - lo) / step), bins - 1) if step > 0 else 0
        counts[max(0, idx)] += 1
    mx = max(counts) or 1
    lines = []
    for i in range(bins):
        lo_v = lo + i * step
        hi_v = lo + (i + 1) * step
        bar = "#" * round(counts[i] / mx * 40)
        lines.append(f"  [{lo_v:5.2f},{hi_v:5.2f}) {counts[i]:>4} {bar}")
    return "\n".join(lines)


def _dist(label: str, values: list[float]) -> None:
    if not values:
        print(f"\n{label}: no samples")
        return
    s = sorted(values)
    print(f"\n{label}  (n={len(values)})")
    print(f"  ratio: min={s[0]:.3f}  p5={_pct(s,5):.3f}  p25={_pct(s,25):.3f}  "
          f"median={statistics.median(s):.3f}  p75={_pct(s,75):.3f}  "
          f"p95={_pct(s,95):.3f}  max={s[-1]:.3f}")


def _verdict(label: str, body: list[float], indent: list[float]) -> None:
    """Assert the two 0.5 claims; report the valley and violations.

    body   (flush-left):   max ratio < 0.5  -> no false-fire on flush-left jitter
    indent (1 em):   min ratio > 0.5  -> shallowest real indent is caught
    """
    print(f"\n{'━' * 78}")
    print(f"{label}")
    print(f"{'━' * 78}")
    if not body or not indent:
        print("  insufficient data")
        return
    b_sorted = sorted(body)
    i_sorted = sorted(indent)
    b_max = b_sorted[-1]
    b_p95 = _pct(b_sorted, 95)
    i_min = i_sorted[0]
    i_p5 = _pct(i_sorted, 5)
    b_over = sum(1 for r in body if r > _THRESHOLD)
    i_under = sum(1 for r in indent if r <= _THRESHOLD)

    print(f"  body   (flush-left):  n={len(body):4}  p95={b_p95:.3f}  "
          f"max={b_max:.3f}   ->  max < {_THRESHOLD}? "
          f"{'YES' if b_max < _THRESHOLD else 'NO'}"
          f"   false-fires (>{_THRESHOLD}): {b_over}/{len(body)}")
    print(f"  indent (1 em):   n={len(indent):4}  p5 ={i_p5:.3f}  "
          f"min={i_min:.3f}   ->  min > {_THRESHOLD}? "
          f"{'YES' if i_min > _THRESHOLD else 'NO'}"
          f"   misses (<= {_THRESHOLD}): {i_under}/{len(indent)}")

    margin_below = _THRESHOLD - b_max
    margin_above = i_min - _THRESHOLD
    print(f"  {_THRESHOLD} margin:  body max is {margin_below:+.3f} below "
          f"{_THRESHOLD},  indent min is {margin_above:+.3f} above "
          f"{_THRESHOLD}")
    if b_max < _THRESHOLD < i_min:
        g = i_min - b_max
        print(f"  VERDICT: SEPARATED - body max {b_max:.3f} < {_THRESHOLD} < "
              f"indent min {i_min:.3f}  (valley {g:.3f} wide, "
              f"{_THRESHOLD} inside it)")
        print(f"           both claims HOLD: no false-fire, shallowest "
              f"indent caught.")
    elif b_max < i_min:
        g = i_min - b_max
        print(f"  VERDICT: PARTIALLY SEPARATED - body max {b_max:.3f} < "
              f"indent min {i_min:.3f} (gap {g:.3f}) BUT {_THRESHOLD} is NOT "
              f"inside the valley ({_THRESHOLD} outside "
              f"[{b_max:.3f},{i_min:.3f}]).")
        if i_min <= _THRESHOLD:
            print(f"           a 1-em indent dips to/under {_THRESHOLD} "
                  f"(min {i_min:.3f}) -> would be MISSED.")
        if b_max >= _THRESHOLD:
            print(f"           flush-left jitter reaches/over {_THRESHOLD} "
                  f"(max {b_max:.3f}) -> would FALSE-FIRE.")
    else:
        print(f"  VERDICT: OVERLAP - body max {b_max:.3f} >= indent min "
              f"{i_min:.3f}: no single-line threshold at {_THRESHOLD} cleanly "
              f"separates them.")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--content", type=str, default="",
                    help="comma-separated content codes (default all): "
                         "zh-Hans,zh-Hant,ja,en,digits")
    ap.add_argument("--sizes", type=str, default="",
                    help="comma-separated font sizes px (default 16,20,24,28,32)")
    ap.add_argument("--line-heights", type=str, default="",
                    help="comma-separated line-height ratios (default 1.3,1.5,1.8)")
    ap.add_argument("--n-body", type=int, default=8, help="body lines per image")
    ap.add_argument("--n-indent", type=int, default=8, help="indent lines per image")
    args = ap.parse_args()

    sizes = ([int(s) for s in args.sizes.split(",") if s.strip()]
             if args.sizes else list(_FONT_SIZES))
    lhs = ([float(s) for s in args.line_heights.split(",") if s.strip()]
           if args.line_heights else list(_LINE_HEIGHTS))
    sel_codes = ([c.strip() for c in args.content.split(",") if c.strip()]
                 if args.content else [c["code"] for c in _CONTENT])
    cmap = {c["code"]: c for c in _CONTENT}
    for c in sel_codes:
        if c not in cmap:
            ap.error(f"unknown content {c!r}; choices: {list(cmap)}")
    content = [cmap[c] for c in sel_codes]

    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    out_dir = Path(_project_root) / "scratch" / "ratiogap_samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("ratiogap_*.png"):
        old.unlink()

    cases: list[dict] = []
    for c in content:
        for fs in sizes:
            for lh in lhs:
                for fam in c["families"]:
                    cid = (f"ratiogap_{c['code']}_"
                           f"{fam.replace(' ', '')}_{fs}_{str(lh).replace('.', 'p')}")
                    cases.append({
                        "id": cid, "content": c["code"], "fs": fs, "fam": fam,
                        "lh": lh,
                        "html": _build_html(fs, fam, lh, c["line"],
                                            args.n_body, args.n_indent),
                    })

    print(f"Rendering {len(cases)} images "
          f"({len(content)} content x sizes {sizes} x line-heights {lhs}, "
          f"{args.n_body} body + {args.n_indent} 1-em-indent lines) -> {out_dir}")
    print(f"threshold = {_THRESHOLD}  (master; old 1.0 missed the 1-em "
          f"indent at ~0.83)")
    pngs = _render(cases, out_dir)
    print("Render done.  Initialising OCR engine.\n")

    from ocr_layout.pipeline import get_engine, release_engine
    engine = get_engine()
    print("Engine ready.  Running one OCR detection per image.\n")

    body_by: dict[str, list[float]] = {c["code"]: [] for c in content}
    ind_by: dict[str, list[float]] = {c["code"]: [] for c in content}
    by_size: dict[int, dict] = {}
    all_body: list[float] = []
    all_indent: list[float] = []
    skipped = 0
    for c, png in zip(cases, pngs):
        rec = _measure(png, engine, args.n_body)
        if rec is None:
            print(f"  skip (no result): {c['id']}")
            skipped += 1
            continue
        b = [ln["ratio"] for ln in rec["body"] if ln["h"] > 0]
        ind = [ln["ratio"] for ln in rec["indent"] if ln["h"] > 0]
        body_by[c["content"]].extend(b)
        ind_by[c["content"]].extend(ind)
        all_body.extend(b)
        all_indent.extend(ind)
        by_size.setdefault(c["fs"], {"body": [], "indent": []})
        by_size[c["fs"]]["body"].extend(b)
        by_size[c["fs"]]["indent"].extend(ind)
        print(f"  {c['id']:54} body_med={statistics.median(b) if b else 0:.3f}  "
              f"indent_med={statistics.median(ind) if ind else 0:.3f}  "
              f"(n_body={len(b)}, n_indent={len(ind)})")
    release_engine()

    print(f"\n{'═' * 78}")
    print(f"REPRODUCTION SUMMARY  (threshold = {_THRESHOLD}, "
          f"{len(cases)} images, skipped {skipped})")
    print(f"{'═' * 78}")
    print(f"  total body   ratios: {len(all_body)}")
    print(f"  total indent ratios: {len(all_indent)}")

    # Per-content distributions + verdict
    for c in content:
        _dist(f"{c['label']}  BODY (flush-left)", body_by[c["code"]])
        _dist(f"{c['label']}  1-em INDENT", ind_by[c["code"]])
    print(f"\n{'═' * 78}")
    print("PER-CONTENT VERDICTS")
    print(f"{'═' * 78}")
    for c in content:
        _verdict(f"{c['label']}", body_by[c["code"]], ind_by[c["code"]])

    # Pooled histograms
    print(f"\n{'═' * 78}")
    print(f"POOLED HISTOGRAMS  (ratio = offset / own_h, all content pooled)")
    print(f"{'═' * 78}")
    lo = 0.0
    hi = min(max(max(all_body, default=0), max(all_indent, default=0), 1.2), 1.6)
    print("\nbody (flush-left):")
    print(_hist(all_body, lo, hi))
    print("\n1-em indent:")
    print(_hist(all_indent, lo, hi))

    # Per-size verdict (does separation hold at every font size?)
    print(f"\n{'═' * 78}")
    print("PER-SIZE VERDICTS  (all content pooled)")
    print(f"{'═' * 78}")
    for fs in sorted(by_size):
        _verdict(f"size {fs}px", by_size[fs]["body"], by_size[fs]["indent"])

    # Overall verdict
    print(f"\n{'═' * 78}")
    print("OVERALL VERDICT  (all content / sizes / families pooled)")
    print(f"{'═' * 78}")
    _verdict("ALL pooled", all_body, all_indent)
    print("\nDone.")


if __name__ == "__main__":
    main()
