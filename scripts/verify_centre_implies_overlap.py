"""Numerical verification of the centre-distance ⇒ overlap-ratio implication.

Claim (hushsnap/ocr/ppocr.py:487-518):

    Reference line band  B = [M - H/2, M + H/2]   (height H, centre M)
    Candidate box        b = [c - h/2, c + h/2]   (height h, centre c)
    centre-distance gate (accept):   |c - M| < k * H
    overlap-ratio gate   (accept):   overlap(b, B) / min(h, H) > r

    Provided  k <= min(1/2, 1 - r),  centre-accept  ==>  overlap-accept.

Analytic worst case (derived by hand): for fixed d = |c-M| < kH the ratio
overlap/min(h,H) is minimised at h = H, d -> kH-, giving ratio -> 1 - k.
So the implication holds iff 1 - k > r  (i.e. k < 1 - r), plus the
k <= 1/2 guard against a tiny box whose centre sits inside the gate but
whose body falls outside the band.  Combined: k <= min(1/2, 1 - r).

This script confirms that with a large random sweep + directed probes at
the boundary, and reports the observed worst-case ratio vs the analytic
1 - k.

Run:  python scripts/verify_centre_implies_overlap.py
"""
from __future__ import annotations

import math
import random

# ── core geometry ────────────────────────────────────────────────────────────
# Set M = 0 by translation invariance; d = |c|.


def overlap_ratio(H: float, h: float, c: float) -> float:
    """overlap([c-h/2, c+h/2], [-H/2, H/2]) / min(h, H)."""
    lo = max(-H / 2, c - h / 2)
    hi = min(H / 2, c + h / 2)
    ov = hi - lo
    if ov <= 0:
        return 0.0
    return ov / min(h, H)


def centre_accepts(H: float, c: float, k: float) -> bool:
    return abs(c) < k * H


# ── random sweep ─────────────────────────────────────────────────────────────

def sweep(k: float, r: float, n: int, rng: random.Random) -> dict:
    """Sample n random (H, h, c); among centre-accepted pairs, count overlap
    rejections and track the minimum overlap ratio."""
    violations = 0
    n_centre_accept = 0
    min_ratio = math.inf
    min_ratio_sample = None
    # Bias sampling toward the analytic worst case (h ~ H, d ~ kH) so the
    # worst case is actually exercised, not just the bulk.
    for _ in range(n):
        H = rng.uniform(8.0, 80.0)            # line heights, px
        # h: mix of "near H" (worst case), small, and large
        roll = rng.random()
        if roll < 0.5:
            h = H * rng.uniform(0.8, 1.2)     # near H
        elif roll < 0.75:
            h = H * rng.uniform(0.05, 0.5)    # tiny box (k<=1/2 guard)
        else:
            h = H * rng.uniform(1.2, 4.0)     # tall box
        # c: bias toward the gate boundary d ~ kH
        if rng.random() < 0.6:
            d = k * H * rng.uniform(0.90, 0.999)   # just inside the gate
        else:
            d = k * H * rng.random()               # uniform [0, kH)
        c = d if rng.random() < 0.5 else -d

        if not centre_accepts(H, c, k):
            continue
        n_centre_accept += 1
        ratio = overlap_ratio(H, h, c)
        if ratio < min_ratio:
            min_ratio = ratio
            min_ratio_sample = (H, h, c)
        if not (ratio > r):                    # overlap gate rejects
            violations += 1

    return {
        "k": k, "r": r, "n": n,
        "n_centre_accept": n_centre_accept,
        "violations": violations,
        "min_ratio": min_ratio if min_ratio != math.inf else None,
        "min_ratio_sample": min_ratio_sample,
        "analytic_worst": 1.0 - k,
        "condition": k <= min(0.5, 1.0 - r),
    }


# ── directed boundary probe ──────────────────────────────────────────────────

def directed_probe(k: float, r: float) -> dict:
    """Construct the analytic worst case h = H, d -> kH- and report the ratio.
    Also scan h in (0, 2H] x d in (0, kH) on a fine grid to catch anything
    the hand analysis missed."""
    H = 40.0
    # analytic worst case
    h_w, c_w = H, k * H * (1 - 1e-9)
    worst_ratio = overlap_ratio(H, h_w, c_w)

    # fine grid search for the true minimum ratio over centre-accepted region
    gmin = math.inf
    g_sample = None
    for i in range(1, 2000):
        h = H * (i / 1000.0)                 # h in (0, 2H]
        for j in range(1, 2000):
            d = k * H * (j / 2000.0)         # d in (0, kH)
            ratio = overlap_ratio(H, h, d)
            if ratio < gmin:
                gmin = ratio
                g_sample = (H, h, d)
    return {
        "k": k, "r": r,
        "analytic_worst_h_eq_H": worst_ratio,
        "grid_min_ratio": gmin,
        "grid_min_sample": g_sample,
        "analytic_1_minus_k": 1.0 - k,
    }


def main() -> None:
    rng = random.Random(20260729)
    N = 2_000_000

    print("=" * 78)
    print("centre-distance ⇒ overlap-ratio  —  numerical verification")
    print(f"random samples per config: {N:,}  (seed 20260729)")
    print("=" * 78)

    configs = [
        ("production (literal comment)", 0.4, 0.5),
        ("code-effective (k=0.4/1.2)",   0.4 / 1.2, 0.5),
        ("boundary k=0.5 (=min(½,1−r))", 0.5, 0.5),
        ("past limit k=0.6 (>½)",        0.6, 0.5),
        ("violates k≤1−r: k=0.4 r=0.65", 0.4, 0.65),
    ]

    print(f"\n{'config':<34} {'k':>6} {'r':>5} {'cond':>5} "
          f"{'viol':>7} {'minRatio':>10} {'1−k':>7}  verdict")
    print("-" * 90)
    for label, k, r in configs:
        res = sweep(k, r, N, rng)
        mr = res["min_ratio"]
        mr_s = f"{mr:.5f}" if mr is not None else "  n/a"
        cond = "✓" if res["condition"] else "✗"
        if res["violations"] == 0 and res["condition"]:
            verdict = "HOLDS (centre ⇒ overlap)"
        elif res["violations"] == 0 and not res["condition"]:
            verdict = "no viol in sample (lucky)"
        else:
            verdict = f"VIOLATIONS: {res['violations']}"
        print(f"{label:<34} {k:>6.3f} {r:>5.2f} {cond:>5} "
              f"{res['violations']:>7} {mr_s:>10} {res['analytic_worst']:>7.3f}  {verdict}")

    print("\n" + "=" * 78)
    print("Directed boundary probes (analytic worst case h=H, d→kH⁻ + fine grid)")
    print("=" * 78)
    print(f"{'config':<34} {'k':>6} {'r':>5} {'analytic':>10} "
          f"{'grid_min':>10} {'1−k':>7}  verdict")
    print("-" * 90)
    for label, k, r in configs:
        dp = directed_probe(k, r)
        holds = dp["grid_min_ratio"] > r
        verdict = ("min > r ✓" if holds else f"min ≤ r ✗  (gap {dp['grid_min_ratio'] - r:+.4f})")
        print(f"{label:<34} {k:>6.3f} {r:>5.2f} "
              f"{dp['analytic_worst_h_eq_H']:>10.5f} "
              f"{dp['grid_min_ratio']:>10.5f} "
              f"{dp['analytic_1_minus_k']:>7.3f}  {verdict}")
        gs = dp["grid_min_sample"]
        print(f"      grid-min at H={gs[0]:.1f} h={gs[1]:.3f} d={gs[2]:.4f} "
              f"(h/H={gs[1]/gs[0]:.3f}, d/(kH)={gs[2]/(k*gs[0]):.4f})")

    # ── Converse check: is the entailment strict (not an equivalence)? ──────
    # centre ⇒ overlap is one-way.  Show pairs that OVERLAP accepts but
    # CENTRE rejects exist -> centre is a strict subset (stricter), so the
    # two gates are NOT equivalent.  Direct construction at h = H:
    #   overlap ratio = 1 − d/H , accepted while d/H < 1 − r
    #   centre       accepted while d/H < k
    # so for any d/H in [k, 1−r) overlap accepts and centre rejects.
    print("=" * 78)
    print("Converse: does overlap ⇒ centre?  (is the entailment an equivalence?)")
    print("=" * 78)
    for label, k, r in configs[:3]:
        H = 40.0
        h = H
        d = H * (k + (1.0 - r - k) / 2.0)   # midpoint of [k, 1-r)
        if not (k <= d / H < 1.0 - r):
            continue
        ov = overlap_ratio(H, h, d)
        c_acc = centre_accepts(H, d, k)
        print(f"{label:<34} h/H=1  d/H={d/H:.3f}  "
              f"overlap_ratio={ov:.3f} (>r={r}{'✓' if ov>r else '✗'})  "
              f"centre_accept={c_acc}  "
              f"-> overlap accepts, centre {'accepts' if c_acc else 'rejects'}")

    print("\n" + "=" * 78)
    print("Summary")
    print("=" * 78)
    print("""
Analytic result:  min over centre-accepted region of  overlap/min(h,H)
                  = 1 − k,  attained at  h = H, d → kH⁻.

  ⇒ implication holds iff  1 − k > r   AND   k ≤ ½
  ⇒ combined guard:  k ≤ min(½, 1 − r)        (exactly the code's claim)

Production (k=0.4, r=0.5):  worst ratio = 1−0.4 = 0.60 > 0.50  ✓  (margin 0.10)
Code-effective (k=1/3):    worst ratio = 1−1/3 = 0.667 > 0.50  ✓ (margin 0.167)
Boundary k=0.5, r=0.5:     worst ratio → 0.50⁺  (vanishing margin; strict < saves it)
Past limit k=0.6:          tiny box at d>½H falls outside band → ratio 0  ✗
Past limit r=0.65,k=0.4:   worst ratio 0.60 < 0.65  ✗
""")


if __name__ == "__main__":
    main()
