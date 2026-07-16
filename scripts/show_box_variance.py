"""Show every individual OCR box height vs font-size so you can see the
variance that makes box_h an unreliable proxy for font-size.

Runs one font-size (default 16px) across multiple families + word samples,
prints every single box with its height, ratio, and rendered text.

Usage:
    python scripts/show_box_variance.py [font_size]
"""

from __future__ import annotations

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


def main():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    from ocr_layout.pipeline import get_engine, release_engine, run_pipeline
    from ocr_layout.render import render_cases
    from ocr_layout.evaluate import check_clustering_batch
    from ocr_layout.cases import BoxHeightCase

    font_size = int(sys.argv[1]) if len(sys.argv) > 1 else 16

    engine = get_engine()

    # Many words, several families at ONE font size
    latin_words = [
        "About", "Bring", "Could", "Dream", "Early", "Field", "Great",
        "Happy", "Index", "Judge", "Known", "Light", "Money", "Night",
        "Ocean", "Price", "Queen", "Right", "Small", "Today", "Under",
        "Value", "World", "Young", "Black", "Clean", "Dance", "Eight",
        "First", "Going", "Heart", "ghost", "QUICK", "brown", "fox",
        "Zebra", "After", "Chair", "Drink", "Enter", "Float", "Glass",
        "Horse", "Learn", "March", "Noise", "Panel", "Press", "Quite",
        "Round", "Shape", "Track", "Upper", "Waste", "jumps", "LAZY",
    ]
    cjk_words = [
        "中文排版", "日本語學", "漢字文化", "幽灵深渊", "命运光明",
        "測試識別", "聚类算法", "宇宙星球", "海洋森林", "春暖花开",
        "株式会社", "深度学习", "计算机视觉", "青山绿水", "古今中外",
    ]

    families = ["Arial", "Times New Roman", "Consolas", "Segoe UI",
                "Microsoft YaHei", "SimSun", "KaiTi"]

    cases = []
    px_pool = ["a","b","c","d","e","f","g","h","i","j","k","l","m","n"]

    for i, fam in enumerate(families):
        px = px_pool[i]
        words = []
        if fam in ("Microsoft YaHei", "SimSun", "KaiTi"):
            word_bank = cjk_words
        else:
            word_bank = latin_words
        for j, w in enumerate(word_bank):
            words.append(f"{px}{j}{w}")
        cases.append(BoxHeightCase(
            id=f"show_{px}_{font_size}",
            font_size_px=font_size,
            font_family=fam,
            words=words,
            prefix=px,
        ))

    with tempfile.TemporaryDirectory(prefix="showbox_") as tmp:
        rr_list = render_cases(cases, tmp)
        pr_list = [run_pipeline(rr.png_path, engine) for rr in rr_list]
        verdicts = check_clustering_batch(rr_list, pr_list)

    # ── Dump every matched pair ──────────────────────────────────────────────
    all_ratios = []
    print(f"{'family':<22} {'text':<35} {'fs':>4} {'box_h':>7} {'ratio':>8} {'Δpx':>6}")
    print("-" * 88)

    for v in verdicts:
        # Extract family from case_id
        parts = v.case_id.split("_")
        fam_label = parts[-1] if parts[-1] == "cjk" else parts[0] if len(parts) <= 2 else "?"

        for bh, th in zip(v.box_heights, v.truth_heights):
            if th <= 0:
                continue
            ratio = bh / th
            delta = bh - th
            all_ratios.append(ratio)

    # Sort by ratio descending so the outliers are visible at top
    pairs = []
    for v in verdicts:
        # Get family from render — use the ground-truth words' font_family
        fam = "?"
        # ... actually the verdict doesn't carry family directly.
        # Use case_id to identify
        cid = v.case_id
        for bh, th in zip(v.box_heights, v.truth_heights):
            if th <= 0:
                continue
            ratio = bh / th
            delta = bh - th
            pairs.append((cid, bh, th, ratio, delta))

    pairs.sort(key=lambda x: -x[3])  # sort by ratio desc

    for cid, bh, th, ratio, delta in pairs:
        print(f"{cid:<22} {'':<35} {th:>4.0f} {bh:>6.1f}px {ratio:>7.3f}× {delta:>+5.1f}px")

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'─' * 88}")
    print(f"font-size={font_size}px  n={len(all_ratios)} boxes")
    print(f"  ratio:  min={min(all_ratios):.3f}×  p5={sorted(all_ratios)[len(all_ratios)//20]:.3f}×  "
          f"median={statistics.median(all_ratios):.3f}×  "
          f"p95={sorted(all_ratios)[19*len(all_ratios)//20]:.3f}×  max={max(all_ratios):.3f}×")
    print(f"  stdev={statistics.stdev(all_ratios):.3f}×  → "
          f"box_h ranges from {min(all_ratios)*font_size:.0f}px to {max(all_ratios)*font_size:.0f}px "
          f"for the same {font_size}px font")

    release_engine()
    print("Done.")


if __name__ == "__main__":
    main()
