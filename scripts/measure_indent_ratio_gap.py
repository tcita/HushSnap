"""Measure the ratio gap between body and 1-char-indented CJK lines.

Question
--------
``_apply_indentation``'s main rule judges each line by ``ratio = offset / h``
where ``offset = bounding_box.x - baseline`` and ``h`` is the line's own
upper-median word-box height (``_word_upper_median(axis='h')``).  A line is
indented when ``ratio > 1.0``.

That ``>1.0`` threshold misses a 1-char CJK indent: a 1-char offset (~= font
size) divided by the box height (~1.2x font size, since every detection box
runs larger than the glyph) lands near ``0.83``.  Could the threshold be
lowered to catch 1-char indents without false-firing on top-aligned body lines
whose ratio is pushed above 0 by side-bearing + detection jitter?

This script answers that by measuring the two distributions directly:

  body   : top-aligned lines; true offset ~0, ratio nudged up by jitter.
  indent : 1-char-indented lines; true offset ~1 font size, ratio ~0.83.

For every rendered line (one OCR cluster) it records, mirroring production::

    baseline = min(left_edge) over all lines in the image
    offset   = left_edge - baseline           (left_edge = min(box.left))
    h        = upper_median(box.heights)      (the per-line ruler)
    ratio    = offset / h                      (the master-rule ratio)

It pools ``ratio`` across images, split by body vs indent, and prints the two
distributions plus the gap (or overlap) between them - which decides whether a
single-line threshold can separate 1-char indent from body jitter at all:

  clear gap  -> a threshold in the gap safely separates them (master rule +
                lowered threshold is enough; the clustering fallback is
                redundant).
  overlap    -> no single-line threshold works; either accept the miss at
                >1.0 (conservative) or rely on multi-line structure (the
                clustering fallback), which is a separate question.

No assertion / pass / fail - this is a measurement for human judgement.

Usage:
    python scripts/measure_indent_ratio_gap.py
    python scripts/measure_indent_ratio_gap.py --sizes 20,24,32
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


# ── Rendering ────────────────────────────────────────────────────────────────
#
# One image per (size, family): a body block (flush-left) followed by a
# 1-char-indented block (margin-left = 1 em = font_size_px).  Pure continuous
# CJK per line so OCR self-segments ~1 box/line - the production-like case.

_CJK_FAMILIES = ["Microsoft YaHei", "SimSun", "KaiTi"]
_FONT_SIZES = [16, 20, 24, 28, 32]
_CJK_LINE = "我凯瑞甘发誓如果我爱过寒王这辈子不得好死"


def _build_html(fs: int, fam: str, n_body: int, n_indent: int) -> str:
    lh = round(fs * 1.5, 1)
    rows = []
    for _ in range(n_body):
        rows.append(f'<div style="margin-left:0">{_CJK_LINE}</div>')
    # 1 em = font_size_px (CJK full-width glyph == font size)
    for _ in range(n_indent):
        rows.append(f'<div style="margin-left:{fs}px">{_CJK_LINE}</div>')
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

    baseline = min(left_edge) over all lines (production definition, now
    sentinel-excluded in production but these synthetic images have none).
    Each line's h = upper-median of its own box heights (production ruler).
    The first n_body lines are body (flush-left), the rest are 1-char indent.
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
    # split by cluster order: first n_body are body, rest are indent
    body = lines[:n_body]
    indent = lines[n_body:]
    return {
        "png": png_path.name,
        "body": body,
        "indent": indent,
    }


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
    """ASCII histogram of values in [lo, hi], binned into `bins`."""
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
    print(f"  px:   median offset/h at median ratio "
          f"(see per-size breakdown for px)")


def _gap(body: list[float], indent: list[float]) -> None:
    print(f"\n{'━' * 78}")
    print("SEPARATION (body vs 1-char indent)")
    print(f"{'━' * 78}")
    if not body or not indent:
        print("  insufficient data")
        return
    body_max = max(body)
    body_p95 = _pct(sorted(body), 95)
    body_p99 = _pct(sorted(body), 99) if len(body) > 1 else body_max
    indent_min = min(indent)
    indent_p5 = _pct(sorted(indent), 5)
    print(f"  body   upper:  p95={body_p95:.3f}  p99={body_p99:.3f}  "
          f"max={body_max:.3f}")
    print(f"  indent lower:  p5 ={indent_p5:.3f}  min={indent_min:.3f}")
    print()
    if body_max < indent_min:
        g = indent_min - body_max
        mid = (body_max + indent_min) / 2
        print(f"  CLEAR GAP: body max {body_max:.3f} < indent min "
              f"{indent_min:.3f}  (gap {g:.3f})")
        print(f"  -> a single-line threshold at ~{mid:.2f} separates them with")
        print(f"     {body_max:.2f} below / {indent_min:.2f} above.")
        print(f"     Master rule + lowered threshold is enough; the clustering")
        print(f"     fallback is redundant for 1-char indent.")
    elif body_p95 < indent_p5:
        g = indent_p5 - body_p95
        print(f"  MOSTLY SEPARATED (p95 body {body_p95:.3f} < p5 indent "
              f"{indent_p5:.3f}, gap {g:.3f}) but tails touch "
              f"(body max {body_max:.3f} >= indent min {indent_min:.3f}).")
        print(f"  -> a threshold around {body_p95:.2f}-{indent_p5:.2f} catches")
        print(f"     most 1-char indents; a few body tail/jitter outliers may")
        print(f"     false-fire.  Master rule + threshold is workable but lossy.")
    else:
        print(f"  OVERLAP: body p95 {body_p95:.3f} >= indent p5 "
              f"{indent_p5:.3f} (body max {body_max:.3f}, indent min "
              f"{indent_min:.3f}).")
        print(f"  -> no single-line threshold cleanly separates 1-char indent")
        print(f"     from body jitter.  Either keep >1.0 (conservative miss)")
        print(f"     or use multi-line structure (clustering) - a single-line")
        print(f"     threshold cannot do it safely.")
    print(f"\n  reference: current master threshold = 1.0  "
          f"(1-char indent lands ~0.83 -> MISSED at >1.0)")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", type=str, default="",
                    help="comma-separated font sizes px (default 16,20,24,28,32)")
    ap.add_argument("--n-body", type=int, default=6, help="body lines per image")
    ap.add_argument("--n-indent", type=int, default=6, help="indent lines per image")
    args = ap.parse_args()

    sizes = ([int(s) for s in args.sizes.split(",") if s.strip()]
             if args.sizes else list(_FONT_SIZES))

    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    out_dir = Path(_project_root) / "scratch" / "ratiogap_samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("ratiogap_*.png"):
        old.unlink()

    cases: list[dict] = []
    for fs in sizes:
        for fam in _CJK_FAMILIES:
            cid = f"ratiogap_{fs}_{fam.replace(' ', '')}"
            cases.append({
                "id": cid, "fs": fs, "fam": fam,
                "html": _build_html(fs, fam, args.n_body, args.n_indent),
            })

    print(f"Rendering {len(cases)} images "
          f"({len(sizes)} sizes x {len(_CJK_FAMILIES)} families, "
          f"{args.n_body} body + {args.n_indent} indent lines) -> {out_dir}")
    pngs = _render(cases, out_dir)
    print("Render done.  Initialising OCR engine.\n")

    from ocr_layout.pipeline import get_engine, release_engine
    engine = get_engine()
    print("Engine ready.  Running one OCR detection per image.\n")

    body_ratios: list[float] = []
    indent_ratios: list[float] = []
    by_size: dict[int, dict] = {}
    for c, png in zip(cases, pngs):
        rec = _measure(png, engine, args.n_body)
        if rec is None:
            print(f"  skip (no result): {c['id']}")
            continue
        b = [ln["ratio"] for ln in rec["body"] if ln["h"] > 0]
        ind = [ln["ratio"] for ln in rec["indent"] if ln["h"] > 0]
        body_ratios.extend(b)
        indent_ratios.extend(ind)
        by_size.setdefault(c["fs"], {"body": [], "indent": []})
        by_size[c["fs"]]["body"].extend(b)
        by_size[c["fs"]]["indent"].extend(ind)
        print(f"  {c['id']:40} body_med={statistics.median(b) if b else 0:.3f}  "
              f"indent_med={statistics.median(ind) if ind else 0:.3f}  "
              f"(n_body={len(b)}, n_indent={len(ind)})")
    release_engine()

    _dist("BODY (top-aligned) ratios", body_ratios)
    _dist("1-CHAR INDENT ratios", indent_ratios)

    print(f"\n{'═' * 78}")
    print("HISTOGRAMS  (ratio = offset / own_h)")
    print(f"{'═' * 78}")
    lo = 0.0
    hi = max(max(body_ratios, default=0), max(indent_ratios, default=0), 1.2)
    hi = min(hi, 1.6)
    print("\nbody:")
    print(_hist(body_ratios, lo, hi))
    print("\n1-char indent:")
    print(_hist(indent_ratios, lo, hi))

    for fs in sorted(by_size):
        _gap(by_size[fs]["body"], by_size[fs]["indent"])
        print(f"  [size {fs}px]")

    print(f"\n{'━' * 78}")
    print("OVERALL (all sizes/families pooled)")
    print(f"{'━' * 78}")
    _gap(body_ratios, indent_ratios)
    print("\nDone.")


if __name__ == "__main__":
    main()
