"""Integration tests: render → OCR → cluster → verify.

These tests generate real HTML, render via Chromium, run PP-OCR, and
check that the greedy line-clustering correctly separates ground-truth
lines.  They are slower than the pure-function tests in
``test_ppocr_layout.py`` but exercise the full pipeline end-to-end.
"""

from __future__ import annotations

import pytest

from .cases import (
    make_line_clustering_cases,
    make_box_height_cases,
)
from .render import render_cases
from .pipeline import run_pipeline
from .evaluate import (
    check_clustering,
    check_clustering_batch,
    compute_box_height_stats,
    ClusteringVerdict,
)


# ═══════════════════════════════════════════════════════════════════════════
# Smoke test — quick sanity check
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.slow
@pytest.mark.parametrize("case", make_line_clustering_cases(
    sizes=[16, 24],
    ratios=[0.5, 0.8, 1.0, 1.5],
))
def test_clustering_smoke(case, engine, tmp_path):
    """Quick smoke test: 2 sizes × 4 ratios = 8 cases."""
    rr = render_cases([case], tmp_path)[0]
    pr = run_pipeline(rr.png_path, engine)
    v = check_clustering(rr, pr)

    assert v.is_evaluable, (
        f"Too few matched boxes ({v.n_matched}) — "
        f"OCR may have failed to detect text"
    )
    assert v.false_merges == 0, v.summary()
    assert v.false_splits == 0, v.summary()


# ═══════════════════════════════════════════════════════════════════════════
# Normal line spacing — should always be clean
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.slow
@pytest.mark.parametrize("case", make_line_clustering_cases(
    sizes=[14, 16, 18, 20, 24, 32],
    ratios=[1.0, 1.2, 1.5, 2.0],
))
def test_normal_spacing_is_clean(case, engine, tmp_path):
    """At ≥1.0× line spacing, clustering must produce zero errors."""
    rr = render_cases([case], tmp_path)[0]
    pr = run_pipeline(rr.png_path, engine)
    v = check_clustering(rr, pr)

    if not v.is_evaluable:
        pytest.skip(f"insufficient matches ({v.n_matched})")

    assert v.false_merges == 0, v.summary()
    assert v.false_splits == 0, v.summary()


# ═══════════════════════════════════════════════════════════════════════════
# Tight spacing — should still pass for realistic cases
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.slow
@pytest.mark.parametrize("case", make_line_clustering_cases(
    sizes=[16, 20, 24],
    ratios=[0.6, 0.7, 0.8, 0.9],
))
def test_tight_spacing(case, engine, tmp_path):
    """At 0.6–0.9× spacing, clustering should still be correct.

    Below 0.6× the text begins to visually overlap — detector-level
    merging may occur, but that is not a clustering error.
    """
    rr = render_cases([case], tmp_path)[0]
    pr = run_pipeline(rr.png_path, engine)
    v = check_clustering(rr, pr)

    if not v.is_evaluable:
        pytest.skip(f"insufficient matches ({v.n_matched})")

    # We allow false_merges here because at <0.6× the detector itself
    # may produce overlapping boxes.  The assertion is that false_splits
    # — fragmenting one line across clusters — should never happen.
    assert v.false_splits == 0, (
        f"False split at ratio={case.line_height_ratio:.1f}× — "
        f"words on the same line should never be in different clusters.  "
        f"{v.summary()}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Box-height inflation measurement
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.slow
def test_box_height_inflation(engine, tmp_path):
    """Measure how much OCR box height exceeds font-size.

    This is a regression test: if the inflation factor changes
    significantly after a model upgrade, the clustering thresholds
    in ppocr.py may need re-tuning.
    """
    cases = make_box_height_cases(
        sizes=[12, 16, 20, 24, 32, 48],
        samples_per_combo=5,
    )
    rr_list = render_cases(cases, tmp_path)
    pr_list = [run_pipeline(rr.png_path, engine) for rr in rr_list]
    verdicts = check_clustering_batch(rr_list, pr_list)

    stats = compute_box_height_stats(verdicts)

    # Expected: median ratio ≈ 1.30–1.45 (from empirical study Jul 2026)
    assert stats.n >= 30, f"Only {stats.n} data points — need ≥30 for reliable stats"
    assert 1.15 <= stats.ratio_median <= 1.70, (
        f"Box-height inflation median = {stats.ratio_median:.3f} is outside "
        f"expected range [1.15, 1.70].  If this is a genuine change from a "
        f"model upgrade, re-evaluate the clustering thresholds in ppocr.py.  "
        f"Stats: {stats.summary()}"
    )

    # Absolute overshoot should be positive (boxes always ≥ font-size)
    assert stats.delta_px_median > 0, (
        f"Expected OCR boxes to be taller than font-size, "
        f"got median Δ = {stats.delta_px_median:+.1f} px"
    )

    print(f"\n    Box-height inflation: {stats.summary()}")
