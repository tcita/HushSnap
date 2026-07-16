"""Match OCR boxes to ground-truth words and verify clustering correctness.

The central function is :func:`check_clustering` — given a
:class:`RenderResult` and a :class:`PipelineResult`, it matches every
OCR box to its ground-truth word by text-content overlap, then checks
whether the greedy clustering correctly separated the ground-truth lines.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .render import RenderResult, RenderedWord
    from .pipeline import PipelineResult, DetectedBox


# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ClusteringVerdict:
    """Result of checking one test case's clustering against ground truth."""

    case_id: str = ""

    # Input counts
    n_ground_truth_words: int = 0
    n_ocr_boxes: int = 0
    n_matched: int = 0

    # Clustering errors
    false_merges: int = 0
    """Boxes from different ground-truth lines ended up in the same cluster."""

    false_splits: int = 0
    """Boxes from the same ground-truth line ended up in different clusters."""

    # Cluster details for diagnostics
    clusters: list[list[str]] = field(default_factory=list)
    """Per-cluster list of matched token strings."""

    # Box-height statistics (for inflation measurement)
    box_heights: list[float] = field(default_factory=list)
    truth_heights: list[float] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True when no false merges or splits were found."""
        return self.false_merges == 0 and self.false_splits == 0

    @property
    def is_evaluable(self) -> bool:
        """True when enough boxes were matched for a meaningful verdict."""
        return self.n_matched >= 4

    def summary(self) -> str:
        """One-line human-readable verdict."""
        if not self.is_evaluable:
            return f"[{self.case_id}] insufficient data ({self.n_matched} matched)"
        if self.is_clean:
            return f"[{self.case_id}] ✓  {self.n_matched} boxes, "
            f"{len(self.clusters)} clusters, no errors"
        return (
            f"[{self.case_id}] ✗  {self.n_matched} boxes, "
            f"merges={self.false_merges} splits={self.false_splits}"
        )


@dataclass
class BoxHeightStats:
    """Aggregate box-height-vs-font-size statistics."""

    n: int = 0
    ratio_mean: float = 0.0
    ratio_median: float = 0.0
    ratio_stdev: float = 0.0
    delta_px_mean: float = 0.0
    delta_px_median: float = 0.0

    def summary(self) -> str:
        return (
            f"n={self.n}  ratio: mean={self.ratio_mean:.3f} "
            f"median={self.ratio_median:.3f} σ={self.ratio_stdev:.3f}  "
            f"Δpx: mean={self.delta_px_mean:+.1f} median={self.delta_px_median:+.1f}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Text normalisation
# ═══════════════════════════════════════════════════════════════════════════


def _norm(s: str) -> str:
    """Strip diacritics, punctuation, case → bare alphanumeric."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ═══════════════════════════════════════════════════════════════════════════
# Matching
# ═══════════════════════════════════════════════════════════════════════════


def _match_boxes_to_words(
    boxes: list["DetectedBox"],
    words: list["RenderedWord"],
) -> list[tuple["DetectedBox", "RenderedWord", float]]:
    """Greedy match each OCR box to the best ground-truth word.

    Returns a list of (box, word, score) tuples.  Unmatched boxes are
    omitted.  Each word is used at most once.

    Matching uses character-overlap of normalised text, plus spatial
    proximity as a tie-breaker.
    """
    used_words: set[int] = set()
    matches: list[tuple["DetectedBox", "RenderedWord", float]] = []

    for b in boxes:
        b_norm = _norm(b.text)
        if len(b_norm) < 2:
            continue

        best_w = None
        best_score = 0.0
        best_dist = float("inf")

        for wi, w in enumerate(words):
            if wi in used_words:
                continue
            w_norm = _norm(w.token)
            if len(w_norm) < 2:
                continue

            # Character overlap
            common = sum(1 for c in b_norm if c in w_norm)
            text_score = common / max(len(b_norm), len(w_norm))

            if text_score < 0.25:
                continue

            # Spatial proximity
            dx = b.center_x - (w.x + w.width / 2)
            dy = b.center_y - (w.y + w.height / 2)
            dist = math.hypot(dx, dy)

            # Combined score: prefer high text overlap, then proximity
            score = text_score - dist / 500.0  # slight spatial bias
            if score > best_score:
                best_score = score
                best_w = wi, w
                best_dist = dist

        if best_w is not None and best_score > 0.2 and best_dist < 200:
            matches.append((b, best_w[1], best_score))
            used_words.add(best_w[0])

    return matches


# ═══════════════════════════════════════════════════════════════════════════
# Clustering verification
# ═══════════════════════════════════════════════════════════════════════════


def check_clustering(
    render_result: "RenderResult",
    pipeline_result: "PipelineResult",
) -> ClusteringVerdict:
    """Match OCR boxes to ground truth, then check for merge/split errors.

    Returns a :class:`ClusteringVerdict` that can be asserted on in tests::

        v = check_clustering(render_result, pipeline_result)
        assert v.false_merges == 0, v.summary()
    """
    words = render_result.words
    boxes = pipeline_result.boxes

    v = ClusteringVerdict(
        case_id=",".join(render_result.case_ids),
        n_ground_truth_words=len(words),
        n_ocr_boxes=len(boxes),
    )

    if not boxes or not words:
        return v

    # Collect box heights for inflation measurement
    for b in boxes:
        v.box_heights.append(b.height)
    for w in words:
        v.truth_heights.append(w.font_size_px)

    # Match
    matched = _match_boxes_to_words(boxes, words)
    v.n_matched = len(matched)

    if v.n_matched < 2:
        return v

    # Group matched boxes by cluster_id
    clusters: dict[int, set[int]] = {}
    for b, w, _score in matched:
        clusters.setdefault(b.cluster_id, set()).add(w.line_idx)

    # Build display strings
    v.clusters = []
    for cid in sorted(clusters):
        ctexts = [b.text for b, w, _ in matched if b.cluster_id == cid]
        v.clusters.append(ctexts)

    # Count false merges: a cluster containing words from >1 ground-truth lines
    for cid, line_ids in clusters.items():
        if len(line_ids) > 1:
            v.false_merges += len(line_ids) - 1

    # Count false splits: one ground-truth line's words spread across >1 cluster
    all_line_ids = set(w.line_idx for _, w, _ in matched)
    for li in all_line_ids:
        cids_for_line = set()
        for b, w, _ in matched:
            if w.line_idx == li:
                cids_for_line.add(b.cluster_id)
        if len(cids_for_line) > 1:
            v.false_splits += len(cids_for_line) - 1

    return v


# ═══════════════════════════════════════════════════════════════════════════
# Batch evaluation
# ═══════════════════════════════════════════════════════════════════════════


def check_clustering_batch(
    render_results: list["RenderResult"],
    pipeline_results: list["PipelineResult"],
) -> list[ClusteringVerdict]:
    """Evaluate a batch of cases, one pair at a time."""
    verdicts = []
    for rr, pr in zip(render_results, pipeline_results):
        verdicts.append(check_clustering(rr, pr))
    return verdicts


def compute_box_height_stats(
    verdicts: list[ClusteringVerdict],
) -> BoxHeightStats:
    """Aggregate box-height-vs-font-size across many verdicts."""
    ratios = []
    deltas = []
    for v in verdicts:
        for bh, th in zip(v.box_heights, v.truth_heights):
            if th > 0:
                ratios.append(bh / th)
                deltas.append(bh - th)

    if not ratios:
        return BoxHeightStats()

    return BoxHeightStats(
        n=len(ratios),
        ratio_mean=statistics.mean(ratios),
        ratio_median=statistics.median(ratios),
        ratio_stdev=statistics.stdev(ratios) if len(ratios) > 1 else 0.0,
        delta_px_mean=statistics.mean(deltas),
        delta_px_median=statistics.median(deltas),
    )


def print_verdict_summary(verdicts: list[ClusteringVerdict]):
    """Print a formatted table of verdicts to stdout."""
    sep = "=" * 78
    print(f"\n{sep}")
    print("Clustering Evaluation Summary")
    print(sep)

    clean = [v for v in verdicts if v.is_clean and v.is_evaluable]
    failed = [v for v in verdicts if not v.is_clean and v.is_evaluable]
    skipped = [v for v in verdicts if not v.is_evaluable]

    print(f"  Total: {len(verdicts)}  |  ✓ OK: {len(clean)}  |  "
          f"✗ errors: {len(failed)}  |  skipped: {len(skipped)}")

    # Group failures by ratio
    if failed:
        print(f"\n  Failures:")
        for v in failed:
            print(f"    {v.summary()}")
            for ci, c in enumerate(v.clusters):
                print(f"      cluster[{ci}]: {c}")

    # Box height stats
    stats = compute_box_height_stats(verdicts)
    if stats.n > 0:
        print(f"\n  Box height inflation: {stats.summary()}")

    print(f"{sep}\n")
