"""Measure box-height inflation at multiple unclip_ratio values.

Answers two questions:
  1. What is the current box-height vs font-size ratio at unclip_ratio=1.6?
  2. Can lowering unclip_ratio tighten boxes (bring ratio closer to 1.0)
     WITHOUT breaking recognition (text still matches ground truth)?

The DB unclip step directly expands each detected contour — higher ratio =
more expansion = taller boxes.  Lower ratios produce tighter boxes that
more faithfully represent the actual text geometry, which is critical for
layout algorithms (line clustering, indentation, paragraph breaks) that
use box dimensions as a ruler.

Method:
  - Render single-word Latin + CJK text at font sizes 8-64 px via
    Playwright Chromium (same engine HushSnap captures from).
  - Run PP-OCR detection at each unclip_ratio, match boxes to ground truth
    by text content + position.
  - Report box_h / font_size ratio and delta (px) at each ratio value.
  - Also report matched-vs-unmatched text to detect clipping.

Values tested:  1.6 (baseline, rapidocr default), 1.3, 1.0, 0.8, 0.6
                2.0 (PaddleOCR default) for reference.

Usage:
    python scripts/measure_box_inflation_unclip.py
    python scripts/measure_box_inflation_unclip.py --report scratch/box_inflation_report.txt
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))


# ── Test parameters ──────────────────────────────────────────────────────────
FONT_SIZES = [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
              22, 24, 26, 28, 30, 32, 36, 40, 44, 48, 52, 56, 60, 64]
LATIN_FAMILIES = ["Arial", "Times New Roman", "Consolas", "Segoe UI"]
CJK_FAMILIES = ["Microsoft YaHei", "SimSun", "KaiTi"]
SAMPLES_PER_COMBO = 10  # per (font_size, family, script)

UNCLIP_VALUES = [1.6, 1.3, 1.0, 0.8, 0.6, 2.0]  # 1.6 = baseline, 2.0 = PaddleOCR

# CJK word bank — multi-character words that render as single detection boxes
_CJK_WORDS = [
    "中文排版测试识别聚类算法引擎",
    "日本語學漢字文化幽霊深淵",
    "命运光明黑暗宇宙星球海洋",
    "森林山脉河流湖泊沙漠草原",
    "幽灵深渊灵魂永恆时空维度",
    "株式会社東京都千代田区",
    "计算机视觉自然语言深度学",
    "春暖花开万物复苏生机勃勃",
    "曾经沧海难为水除却巫山",
    "工藤新一毛利兰灰原哀柯南",
    "测试文字识别效果评估指标",
    "探索未知世界发现新奇事物",
    "人工智能改变未来生活方式",
    "青山绿水白云蓝天风景如画",
    "古今中外文化交融博采众长",
]
_CJK_PREFIXES = [
    "za","zb","zc","zd","ze","zf","zg","zh","zi","zj","zk","zl","zm",
    "zn","zo","zp","zq","zr","zs","zt","zu","zv","zw","zx","zy","zz",
    "ya","yb","yc","yd","ye","yf","yg","yh","yi","yj","yk","yl","ym",
    "yn","yo","yp","yq","yr","ys","yt","yu","yv","yw","yx","yy","yz",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="", help="write report to this file")
    args = ap.parse_args()

    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    from ocr_layout.pipeline import get_engine, release_engine, run_pipeline
    from ocr_layout.render import render_cases, RenderedWord
    from ocr_layout.evaluate import compute_box_height_stats, _match_boxes_to_words, _norm
    from ocr_layout.cases import BoxHeightCase, make_box_height_cases

    engine = get_engine()
    print("Engine ready.\n")

    lines_out = []
    def emit(s=""):
        print(s); lines_out.append(s)

    emit("=" * 90)
    emit("Box-height inflation vs unclip_ratio")
    emit(f"  font sizes: {FONT_SIZES[0]}–{FONT_SIZES[-1]} px")
    emit(f"  families: {LATIN_FAMILIES + CJK_FAMILIES}")
    emit(f"  latin combos: {len(FONT_SIZES)*len(LATIN_FAMILIES)}  "
         f"CJK combos: {len(FONT_SIZES)*len(CJK_FAMILIES)}")
    emit(f"  samples per combo: {SAMPLES_PER_COMBO}")
    emit(f"  total cases: "
         f"{(len(FONT_SIZES)*len(LATIN_FAMILIES) + len(FONT_SIZES)*len(CJK_FAMILIES))}")
    emit(f"  unclip values: {UNCLIP_VALUES}")
    emit("=" * 90)

    # ── Build cases ──────────────────────────────────────────────────────────
    latin_cases = make_box_height_cases(
        sizes=FONT_SIZES, families=LATIN_FAMILIES,
        samples_per_combo=SAMPLES_PER_COMBO,
    )

    cjk_cases = []
    prefix_idx = 0; word_idx = 0
    for fs in FONT_SIZES:
        for fam in CJK_FAMILIES:
            px = _CJK_PREFIXES[prefix_idx % len(_CJK_PREFIXES)]
            prefix_idx += 1
            words = []
            for _ in range(SAMPLES_PER_COMBO):
                w = _CJK_WORDS[word_idx % len(_CJK_WORDS)]
                words.append(f"{px}{w}")
                word_idx += 1
            cjk_cases.append(BoxHeightCase(
                id=f"bh_{px}_{fs}_cjk",
                font_size_px=fs, font_family=fam,
                words=words, prefix=px,
            ))

    all_cases = latin_cases + cjk_cases
    n_cases = len(all_cases)
    emit(f"\nCases: {len(latin_cases)} Latin + {len(cjk_cases)} CJK = {n_cases} total")

    # ── Render once (rendering is independent of unclip_ratio) ──────────────
    with tempfile.TemporaryDirectory(prefix="infl_unclip_") as tmp:
        print(f"\nRendering {n_cases} cases via Playwright...", flush=True)
        rr_list = render_cases(all_cases, tmp)
        # Build flat list of (word, ground_truth_height) for matching
        all_truth: list[RenderedWord] = []
        for rr in rr_list:
            all_truth.extend(rr.words)

        total_expected_boxes = sum(len(rr.words) for rr in rr_list)
        emit(f"  Rendered: {total_expected_boxes} word boxes across {n_cases} PNGs\n")

        # ── Run pipeline at each unclip_ratio ───────────────────────────────
        # results[unclip] = [(box_heights, truth_heights, matched_texts), ...]
        # per-case data for each unclip value
        from collections import defaultdict
        all_data: dict[float, list[dict]] = defaultdict(list)

        for uval in UNCLIP_VALUES:
            label = f"unclip={uval}"
            print(f"Running {label}...", flush=True, end=" ")
            n_boxes_total = 0
            n_matched_total = 0
            n_unmatched_total = 0

            for rr in rr_list:
                pr = run_pipeline(rr.png_path, engine, unclip_ratio=uval)

                # Match OCR boxes to ground-truth words for this case
                matched = _match_boxes_to_words(pr.boxes, rr.words)

                case_data = {
                    "png": str(rr.png_path),
                    "case_ids": rr.case_ids,
                    "n_boxes": len(pr.boxes),
                    "n_matched": len(matched),
                    "ratios": [],
                    "deltas_px": [],
                    "matched_texts": [],
                    "unmatched_boxes": [],
                }

                # Collect box-height data from matched pairs
                for box, word, _score in matched:
                    case_data["ratios"].append(box.height / word.font_size_px)
                    case_data["deltas_px"].append(box.height - word.font_size_px)
                    case_data["matched_texts"].append((box.text[:40], word.token[:40]))

                # Track unmatched boxes (potential detection/recognition issues)
                matched_box_ids = set(id(b) for b, _, _ in matched)
                for b in pr.boxes:
                    if id(b) not in matched_box_ids:
                        case_data["unmatched_boxes"].append(b.text[:60])

                n_boxes_total += len(pr.boxes)
                n_matched_total += len(matched)
                n_unmatched_total += len(case_data["unmatched_boxes"])

                all_data[uval].append(case_data)

            all_ratios = [r for d in all_data[uval] for r in d["ratios"]]
            all_deltas = [d for d in all_data[uval] for d_ in all_data[uval] for d in d_["deltas_px"]]
            # Fix: collect properly
            all_deltas = [d for d_ in all_data[uval] for d in d_["deltas_px"]]
            n_total_matched = sum(len(d["matched_texts"]) for d in all_data[uval])
            n_total_unmatched = sum(len(d["unmatched_boxes"]) for d in all_data[uval])
            print(f"{n_boxes_total} boxes, {n_total_matched} matched, "
                  f"{n_total_unmatched} unmatched  [{len([r for r in all_ratios if r>0])} ratios]")

    # ── Report ──────────────────────────────────────────────────────────────
    emit("\n" + "=" * 90)
    emit("RESULTS: Box-height ratio (box_h / font_size) vs unclip_ratio")
    emit("=" * 90)

    # Print table header
    emit(f"\n  {'unclip':>7s}  {'n':>5s}  {'ratio mean':>10s}  {'ratio med':>9s}  "
         f"{'ratio σ':>8s}  {'Δpx mean':>9s}  {'Δpx med':>8s}  "
         f"{'unmatched':>9s}  {'verdict':s}")
    emit(f"  {'─'*7}  {'─'*5}  {'─'*10}  {'─'*9}  {'─'*8}  {'─'*9}  {'─'*8}  {'─'*9}  {'─'*20}")

    base_u = 1.6  # baseline
    base_ratios = None
    base_med = None
    results: dict[float, dict] = {}

    for uval in UNCLIP_VALUES:
        all_ratios = [r for d in all_data[uval] for r in d["ratios"]]
        all_deltas = [d for d_ in all_data[uval] for d in d_["deltas_px"]]
        n_unmatched = sum(len(d["unmatched_boxes"]) for d in all_data[uval])
        n_total_matched = sum(len(d["matched_texts"]) for d in all_data[uval])

        if not all_ratios:
            emit(f"  {uval:7.1f}  (no data)")
            continue

        n = len(all_ratios)
        rm = statistics.mean(all_ratios)
        rmed = statistics.median(all_ratios)
        rs = statistics.stdev(all_ratios) if n > 1 else 0.0
        dm = statistics.mean(all_deltas)
        dmed = statistics.median(all_deltas)

        results[uval] = {
            "n": n, "ratio_mean": rm, "ratio_median": rmed, "ratio_stdev": rs,
            "delta_px_mean": dm, "delta_px_median": dmed,
            "n_unmatched": n_unmatched, "n_total_matched": n_total_matched,
        }

        if uval == base_u:
            base_ratios = all_ratios
            base_med = rmed

        # Determine verdict vs ideal (ratio=1.0)
        excess_pct = (rmed - 1.0) * 100
        if excess_pct < 0:
            v = f"⚠ UNDER-sized ({-excess_pct:.0f}%)"  # clipping text
        elif excess_pct < 5:
            v = f"✓ near-ideal ({excess_pct:+.0f}%)"
        elif excess_pct < 15:
            v = f"~ acceptable ({excess_pct:+.0f}%)"
        else:
            v = f"✗ oversized ({excess_pct:+.0f}%)"

        emit(f"  {uval:7.1f}  {n:5d}  {rm:10.4f}  {rmed:9.4f}  "
             f"{rs:8.4f}  {dm:+9.2f}  {dmed:+8.2f}  "
             f"{n_unmatched:>4d}/{n_total_matched+n_unmatched:<4d}  {v}")

    # ── By script (Latin vs CJK) at each value ──────────────────────────────
    emit("\n" + "-" * 90)
    emit("By script (Latin / CJK)")
    emit("-" * 90)
    for uval in UNCLIP_VALUES:
        latin_ratios = []
        cjk_ratios = []
        for case_data in all_data[uval]:
            for i, cid in enumerate(case_data["case_ids"]):
                pass  # We can't easily split by case after matching
        # Actually we need to re-approach - let's separate by the case_id suffix
        emit(f"  unclip={uval:.1f}: not split by script in this run "
             f"(cases mixed in render order)")

    # ── Breakdown by font size at baseline ──────────────────────────────────
    emit("\n" + "-" * 90)
    emit(f"By font size at baseline (unclip={base_u})")
    emit("-" * 90)
    emit(f"  {'fs':>5s}  {'n':>5s}  {'ratio med':>9s}  {'Δpx med':>7s}")
    emit(f"  {'─'*5}  {'─'*5}  {'─'*9}  {'─'*7}")
    # Group by font_size (extracted from case_id like "bh_xx_NN")
    import re
    by_fs: dict[int, list[float]] = {}
    for i, case_data in enumerate(all_data[base_u]):
        # Try to extract font size from png path
        for cid in case_data["case_ids"]:
            m = re.search(r'_(\d+)(?:_cjk)?$', cid)
            if m:
                fs = int(m.group(1))
                by_fs.setdefault(fs, []).extend(case_data["ratios"])
    for fs in sorted(by_fs):
        ratios = by_fs[fs]
        n = len(ratios)
        if n == 0: continue
        med = statistics.median(ratios)
        med_delta = (med - 1.0) * fs
        emit(f"  {fs:>5d}  {n:>5d}  {med:9.3f}×  {med_delta:+.1f}px")

    # ── Paired comparison: unclip vs baseline ───────────────────────────────
    emit("\n" + "=" * 90)
    emit("Paired comparison vs baseline (unclip=1.6)")
    emit("=" * 90)
    emit(f"\n  {'unclip':>7s}  {'Δ ratio':>9s}  {'% change':>9s}  "
         f"{'Δ px':>7s}  {'unmatch Δ':>10s}  {'recommendation':s}")
    emit(f"  {'─'*7}  {'─'*9}  {'─'*9}  {'─'*7}  {'─'*10}  {'─'*20}")

    base_unmatched = results[base_u]["n_unmatched"]
    for uval in UNCLIP_VALUES:
        if uval == base_u:
            continue
        if uval not in results:
            continue
        r = results[uval]
        dr = r["ratio_median"] - results[base_u]["ratio_median"]
        dpct = (r["ratio_median"] / results[base_u]["ratio_median"] - 1.0) * 100
        dpx = r["delta_px_median"] - results[base_u]["delta_px_median"]
        du = r["n_unmatched"] - base_unmatched

        # Recommendation
        if du > results[base_u]["n_total_matched"] * 0.02:
            rec = "REJECT — significant unmatched increase"
        elif dr < -0.03 and du <= 1:
            rec = "PROMISING — tighter boxes, no match loss"
        elif abs(dr) < 0.01 and du <= 1:
            rec = "no effect (noise)"
        elif dr > 0.01:
            rec = "worse — boxes grew"
        else:
            rec = "mixed — review detail"

        emit(f"  {uval:7.1f}  {dr:+9.4f}  {dpct:+8.1f}%  "
             f"{dpx:+7.1f}  {du:+10d}  {rec}")

    # ── Check for text clipping at low unclip ───────────────────────────────
    emit("\n" + "-" * 90)
    emit("Text-clipping check (low unclip vs baseline)")
    emit("-" * 90)
    emit("  Looking for cases where lowering unclip_ratio causes text loss or"
         "\n  character drop vs baseline. \"unmatched\" = detection box not matched"
         "\n  to any ground-truth word (possible fragmentation).")

    for uval in sorted(UNCLIP_VALUES):
        if uval >= base_u:
            continue
        # Compare matched count per case between uval and baseline
        worse_cases = 0
        worse_examples = []
        for i, (d_low, d_base) in enumerate(zip(all_data[uval], all_data[base_u])):
            n_m_low = len(d_low["matched_texts"])
            n_m_base = len(d_base["matched_texts"])
            if n_m_low < n_m_base - 0:
                worse_cases += 1
                if len(worse_examples) < 5:
                    # Find what was lost
                    base_texts = set(t[0] for t in d_base["matched_texts"])
                    low_texts = set(t[0] for t in d_low["matched_texts"])
                    lost = base_texts - low_texts
                    gained = low_texts - base_texts
                    worse_examples.append((n_m_low, n_m_base, lost, gained,
                                           d_low["case_ids"]))
        emit(f"\n  unclip={uval:.1f}: {worse_cases}/{n_cases} cases have fewer "
             f"matches than baseline")
        for n_m_low, n_m_base, lost, gained, cids in worse_examples:
            emit(f"    {cids}: baseline={n_m_base} matched, "
                 f"unclip={uval:.1f}={n_m_low} matched  "
                 f"lost={list(lost)[:3]}  gained={list(gained)[:3]}")

    # ── Recommendation ──────────────────────────────────────────────────────
    emit("\n" + "=" * 90)
    # Find the lowest unclip that doesn't hurt matching
    best_u = base_u
    for uval in sorted(UNCLIP_VALUES):
        if uval >= base_u:
            continue
        r = results.get(uval)
        if not r:
            continue
        # Check: didn't significantly increase unmatched
        n_unmatched_delta = r["n_unmatched"] - base_unmatched
        total_boxes = r["n_total_matched"] + r["n_unmatched"]
        if total_boxes > 0 and n_unmatched_delta / total_boxes < 0.01:
            # Check: ratio meaningfully improved
            if r["ratio_median"] < results[base_u]["ratio_median"] - 0.02:
                best_u = uval
                break

    if best_u != base_u:
        emit(f"RECOMMENDATION: unclip_ratio={best_u} — tighter boxes without "
             f"recognition loss")
    else:
        emit(f"RECOMMENDATION: keep unclip_ratio={base_u} — lowering further "
             f"either has no effect or hurts recognition")
    emit("=" * 90)

    # ── Save detail ─────────────────────────────────────────────────────────
    detail_path = _project_root / "scratch" / "box_inflation_unclip_detail.json"
    detail = {
        "font_sizes": FONT_SIZES,
        "families": LATIN_FAMILIES + CJK_FAMILIES,
        "unclip_values": UNCLIP_VALUES,
        "results": {str(k): v for k, v in results.items()},
    }
    detail_path.write_text(json.dumps(detail, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    emit(f"\nDetail saved to: {detail_path}")

    if args.report:
        rp = _project_root / args.report
        rp.write_text("\n".join(lines_out), encoding="utf-8")
        print(f"\nReport written to {rp}")

    release_engine()
    print("\nDone.")


if __name__ == "__main__":
    main()
