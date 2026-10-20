"""Small estimators for the block sample. Standard library only.

Blocks are clusters: one block can hold several swaps or transactions, so a share is a ratio estimate
sum(y_i) / sum(m_i) over sampled blocks. Its variance comes from the linearised residuals z_i = y_i - p*m_i.
The Wilson interval is then taken at the effective sample size n_eff = p(1-p)/var, capped at the number of
units (a cluster sample is never treated as more informative than a simple random sample of the same units).
"""
import math

Z95 = 1.959963984540054


def wilson(p, n, z=Z95):
    if n <= 0:
        return (0.0, 1.0)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def ratio_share(ys, ms, N=None):
    """One stratum. ys, ms: per-block counts. N: stratum size in blocks (finite-population correction)."""
    n, M = len(ys), sum(ms)
    if M == 0:
        return {"p": None, "units": 0, "blocks": n, "lo": None, "hi": None, "n_eff": 0, "deff": None}
    p = sum(ys) / M
    mbar = M / n
    fpc = 1 - n / N if N else 1.0
    var = fpc * sum((y - p * m) ** 2 for y, m in zip(ys, ms)) / (n - 1) / n / mbar ** 2 if n > 1 else 0.0
    n_eff = min(M, p * (1 - p) / var) if var > 0 else M
    lo, hi = wilson(p, n_eff)
    return {"p": p, "units": M, "blocks": n, "lo": lo, "hi": hi, "n_eff": n_eff,
            "deff": var / (p * (1 - p) / M) if var > 0 and 0 < p < 1 else None}


def stratified_ratio(strata):
    """strata: [(ys, ms, N)]. Combined ratio estimator over strata weighted by stratum size N."""
    num = sum(N * sum(ys) / len(ys) for ys, ms, N in strata)
    den = sum(N * sum(ms) / len(ms) for ys, ms, N in strata)
    units = sum(sum(ms) for _, ms, _ in strata)
    if den == 0:
        return {"p": None, "units": 0}
    p = num / den
    var = 0.0
    for ys, ms, N in strata:
        n = len(ys)
        if n < 2:
            continue
        z = [y - p * m for y, m in zip(ys, ms)]
        zb = sum(z) / n
        var += N * N * (1 - n / N) * sum((v - zb) ** 2 for v in z) / (n - 1) / n
    var /= den * den
    n_eff = min(units, p * (1 - p) / var) if var > 0 else units
    lo, hi = wilson(p, n_eff)
    return {"p": p, "units": units, "lo": lo, "hi": hi, "n_eff": n_eff}
