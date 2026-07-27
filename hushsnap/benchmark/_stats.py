"""Statistical functions for benchmark analysis (no scipy dependency)."""

import math


def mann_whitney_u(a: list[float], b: list[float]) -> dict:
    """Mann-Whitney U test — statistical significance between two samples.

    Returns a dict with keys ``u``, ``z``, ``p``, ``significant``,
    ``n1``, ``n2``.  Uses normal approximation for N ≥ 8; no scipy
    dependency.

    Parameters
    ----------
    a, b:
        Two independent samples (lists of floats).
    """
    n1, n2 = len(a), len(b)
    if n1 < 3 or n2 < 3:
        return {"u": -1, "z": 0, "p": -1, "significant": False,
                "note": "too few samples", "n1": n1, "n2": n2}

    # Rank all values
    combined = [(v, 0) for v in a] + [(v, 1) for v in b]
    combined.sort(key=lambda x: x[0])

    ranks: list[tuple[float, int]] = []
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + j + 1) / 2  # 1-indexed, tie-adjusted
        for k in range(i, j):
            ranks.append((avg_rank, combined[k][1]))
        i = j

    r1 = sum(r for r, grp in ranks if grp == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    u2 = n1 * n2 - u1
    u = min(u1, u2)

    # Normal approximation with tie correction
    mu = n1 * n2 / 2
    rank_counts: dict[float, int] = {}
    for r, _ in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1
    tie_corr = sum(c**3 - c for c in rank_counts.values()) / ((n1 + n2) * (n1 + n2 - 1))
    sigma = math.sqrt(n1 * n2 / 12 * ((n1 + n2 + 1) - tie_corr))

    z = (u - mu) / sigma if sigma > 1e-9 else 0.0
    abs_z = abs(z)
    p = 2 * (1 - _normal_cdf_approx(abs_z)) if abs_z > 0 else 1.0

    return {
        "u": u, "z": z,
        "p": max(0.0, min(1.0, p)),
        "significant": p < 0.05,
        "n1": n1, "n2": n2,
    }


def _normal_cdf_approx(x: float) -> float:
    """Abramowitz & Stegun 26.2.17 approximation for the standard normal CDF Φ(x)."""
    if x < 0:
        return 1 - _normal_cdf_approx(-x)
    b0, b1, b2 = 0.2316419, 0.319381530, -0.356563782
    b3, b4, b5 = 1.781477937, -1.821255978, 1.330274429
    t = 1 / (1 + b0 * x)
    pdf = (1 / math.sqrt(2 * math.pi)) * math.exp(-x * x / 2)
    return 1 - pdf * (b1*t + b2*t**2 + b3*t**3 + b4*t**4 + b5*t**5)


def classify_shape(retention: float, decay_lambda: float = -1.0,
                   r_squared: float = -1.0) -> str:
    """Classify the memory shape from retention ratio and exponential decay rate.

    Parameters
    ----------
    retention:
        ``WS_after / WS_peak``.  ≈1.0 → plateau (memory held);
        ≪1.0 → spike (memory released).  NB for this engine a plateau is
        the EXPECTED steady state, not an anomaly: the detector's
        intermediate tensors commit once on first inference and stay
        resident for the process lifetime (release_engine is dead
        code in production); the OS only reclaims those physical pages
        via idle-trim.  See scripts/OCR_FIRST_INFERENCE.md.
    decay_lambda:
        Exponential decay rate in s⁻¹.  Pass -1 if unavailable.
    r_squared:
        Fit quality (R²) of the exponential model.  Pass -1 if unavailable
        (λ-based gates are then skipped).

    Returns
    -------
    str
        One of ``PLATEAU``, ``SPIKE``, or ``MIXED`` with a short label.
    """
    lambda_ok = r_squared < 0 or r_squared >= 0.5

    if retention > 0.8 and lambda_ok and 0 <= decay_lambda < 0.2:
        return "PLATEAU  (det tensors resident - expected, see OCR_FIRST_INFERENCE.md)"
    if retention < 0.5 and lambda_ok and decay_lambda > 1.0:
        return "SPIKE    (sharp peak, fast release)"
    if retention < 0.5 and decay_lambda > 0.1:
        return "SPIKE    (slow tail but released)"
    if retention < 0.5:
        return "SPIKE    (released, λ unreliable)"
    if lambda_ok and decay_lambda > 0.5:
        return "MIXED    (some retention but decaying)"
    return "PLATEAU  (det tensors resident - expected)"
