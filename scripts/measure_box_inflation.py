"""Measure PP-OCR detection box height vs rendered font-size.

Reuses the existing ocr_layout test infrastructure (render_cases,
run_pipeline, compute_box_height_stats).  Extends make_box_height_cases
with CJK word support.

Usage:
    python scripts/measure_box_inflation.py
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


def main():
    import os, tempfile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    from ocr_layout.pipeline import get_engine, release_engine, run_pipeline
    from ocr_layout.render import render_cases
    from ocr_layout.evaluate import (
        check_clustering_batch, compute_box_height_stats,
    )
    from ocr_layout.cases import (
        BoxHeightCase,
        make_box_height_cases,
    )

    engine = get_engine()
    print("Engine ready.\n")

    # ── Parameters ───────────────────────────────────────────────────────────
    font_sizes = [
        8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
        22, 24, 26, 28, 30, 32, 36, 40, 44, 48, 52, 56, 60, 64,
    ]
    latin_families = ["Arial", "Times New Roman", "Consolas", "Segoe UI"]
    cjk_families = ["Microsoft YaHei", "SimSun", "KaiTi"]
    samples_per_combo = 15

    # CJK word bank
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

    # ── Build cases ──────────────────────────────────────────────────────────
    # Latin: use existing generator
    latin_cases = make_box_height_cases(
        sizes=font_sizes,
        families=latin_families,
        samples_per_combo=samples_per_combo,
    )

    # CJK: build manually (make_box_height_cases only uses _EN_WORDS)
    cjk_prefix_pool = [
        "za","zb","zc","zd","ze","zf","zg","zh","zi","zj","zk","zl","zm",
        "zn","zo","zp","zq","zr","zs","zt","zu","zv","zw","zx","zy","zz",
        "ya","yb","yc","yd","ye","yf","yg","yh","yi","yj","yk","yl","ym",
        "yn","yo","yp","yq","yr","ys","yt","yu","yv","yw","yx","yy","yz",
        "xa","xb","xc","xd","xe","xf","xg","xh","xi","xj","xk","xl","xm",
        "xn","xo","xp","xq","xr","xs","xt","xu","xv","xw","xx","xy","xz",
        "wa","wb","wc","wd","we","wf","wg","wh","wi","wj","wk","wl","wm",
        "wn","wo","wp","wq","wr","ws","wt","wu","wv","ww","wx","wy","wz",
    ]
    cjk_cases = []
    prefix_idx = 0
    word_idx = 0
    for fs in font_sizes:
        for fam in cjk_families:
            px = cjk_prefix_pool[prefix_idx % len(cjk_prefix_pool)]
            prefix_idx += 1
            words = []
            for _ in range(samples_per_combo):
                w = _CJK_WORDS[word_idx % len(_CJK_WORDS)]
                words.append(f"{px}{w}")
                word_idx += 1
            cjk_cases.append(BoxHeightCase(
                id=f"bh_{px}_{fs}_cjk",
                font_size_px=fs,
                font_family=fam,
                words=words,
                prefix=px,
            ))

    all_cases = latin_cases + cjk_cases
    print(f"Cases: {len(latin_cases)} Latin + {len(cjk_cases)} CJK = {len(all_cases)} total")
    print(f"Expected data points: ~{len(all_cases) * samples_per_combo}\n")

    # ── Render + pipeline ────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory(prefix="infl_") as tmp:
        rr_list = render_cases(all_cases, tmp)
        pr_list = [run_pipeline(rr.png_path, engine) for rr in rr_list]
        verdicts = check_clustering_batch(rr_list, pr_list)

    # ── Split verdicts by script ─────────────────────────────────────────────
    latin_verdicts = [v for v in verdicts if not v.case_id.endswith("_cjk")]
    cjk_verdicts = [v for v in verdicts if v.case_id.endswith("_cjk")]

    for label, vlist in [("Latin", latin_verdicts), ("CJK", cjk_verdicts)]:
        stats = compute_box_height_stats(vlist)
        if stats.n == 0:
            print(f"{label}: no data")
            continue

        # Also per-font-size breakdown
        by_fs: dict[int, list[float]] = {}
        for v in vlist:
            fs_str = v.case_id.split("_")[2] if len(v.case_id.split("_")) >= 3 else ""
            try:
                fs = int(fs_str)
            except ValueError:
                continue
            for bh, th in zip(v.box_heights, v.truth_heights):
                if th > 0:
                    by_fs.setdefault(fs, []).append(bh / th)

        print(f"{'─' * 70}")
        print(f"{label} (n={stats.n})")
        print(f"{'─' * 70}")
        print(f"  ratio:  mean={stats.ratio_mean:.3f}×  median={stats.ratio_median:.3f}×  "
              f"stdev={stats.ratio_stdev:.3f}×")
        print(f"  delta:  mean={stats.delta_px_mean:+.1f}px  median={stats.delta_px_median:+.1f}px")

        # Per-size median ratio
        if len(by_fs) >= 3:
            print(f"\n  {'fs':>5}  {'n':>5}  {'med ratio':>10}")
            print(f"  {'─' * 25}")
            for fs in sorted(by_fs):
                ratios = by_fs[fs]
                print(f"  {fs:>5}  {len(ratios):>5}  {statistics.median(ratios):>9.3f}×")

            # Check constancy
            fs_vals = sorted(by_fs)
            med_ratios = [statistics.median(by_fs[fs]) for fs in fs_vals]
            if len(med_ratios) >= 4:
                import numpy as np
                slope_r, intercept_r = np.polyfit(fs_vals, med_ratios, 1)
                rel = abs(slope_r) / statistics.mean(med_ratios)
                print(f"\n  ratio(fs) ≈ {slope_r:.5f}×fs + {intercept_r:.3f}  "
                      f"(rel slope={rel:.5f}, "
                      f"{'NOT constant' if rel > 0.002 else '~constant'})")

        # Percentiles
        all_ratios = []
        for v in vlist:
            for bh, th in zip(v.box_heights, v.truth_heights):
                if th > 0:
                    all_ratios.append(bh / th)
        if all_ratios:
            sr = sorted(all_ratios)
            print(f"  ratio p5={sr[len(sr)//20]:.3f}×  p25={sr[len(sr)//4]:.3f}×  "
                  f"p75={sr[3*len(sr)//4]:.3f}×  p95={sr[19*len(sr)//20]:.3f}×")
        print()

    # ── Combined ─────────────────────────────────────────────────────────────
    combined = compute_box_height_stats(verdicts)
    print(f"{'═' * 70}")
    print(f"Combined: n={combined.n}  ratio median={combined.ratio_median:.3f}×  "
          f"delta median={combined.delta_px_median:+.1f}px")

    release_engine()
    print("Done.")


if __name__ == "__main__":
    main()
