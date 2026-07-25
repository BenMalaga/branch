"""Spatial statistics: is that cluster real, or is it noise?

A density map shows where things pile up, but it cannot say whether a pile is
more than you would expect by chance. That difference matters the moment an
analysis is used to argue for money: a hearing, a grant, a council packet.

Getis-Ord Gi* answers it per cell, comparing the neighborhood around a cell to
the study area as a whole. Significance is corrected for testing many cells at
once (Benjamini-Hochberg), which is the step most published hotspot maps quietly
skip, and a permutation p-value is reported alongside so a skewed distribution
does not hide behind the normal assumption.
"""
from __future__ import annotations

import numpy as np


def neighbor_matrix(cells: list[str]) -> np.ndarray:
    """Binary spatial weights over H3 cells, each cell counted as its own neighbor.

    Gi* (with the star) includes the focal cell; Gi (without) excludes it. We use
    the starred form because a hotspot should include the place it is centered on.
    """
    import h3

    index = {c: i for i, c in enumerate(cells)}
    n = len(cells)
    w = np.zeros((n, n), dtype=np.float64)
    for c, i in index.items():
        w[i, i] = 1.0
        for nb in h3.grid_disk(c, 1):
            j = index.get(nb)
            if j is not None:
                w[i, j] = 1.0
    return w


def getis_ord(values: np.ndarray, w: np.ndarray, permutations: int = 199,
              seed: int = 0) -> dict:
    """Gi* z-scores and significance, corrected for testing many cells at once.

    Returns z, p (the FDR-adjusted analytic p, used for the classes), the raw
    analytic p, a permutation p as a robustness check, and a plain-language class
    per cell.
    """
    x = np.asarray(values, dtype=np.float64)
    n = x.size
    if n < 30:
        raise ValueError(
            f"Only {n} cells have data. Hotspot statistics need at least 30 to "
            f"say anything trustworthy, so use a finer resolution or a bigger area."
        )

    mean = x.mean()
    # population standard deviation, as the Gi* definition uses
    s = np.sqrt((x ** 2).mean() - mean ** 2)
    if s == 0:
        raise ValueError("Every cell holds the same value, so there is no "
                         "hot or cold spot to find.")

    wsum = w.sum(axis=1)
    wsq = (w ** 2).sum(axis=1)
    numer = w @ x - mean * wsum
    denom = s * np.sqrt(np.maximum((n * wsq - wsum ** 2) / (n - 1), 1e-12))
    z = numer / denom

    # Two p-values, because they answer different worries and have different floors.
    #
    # The analytic one comes from the normal distribution of the z-score and can
    # be arbitrarily small, which matters: correcting for many cells multiplies
    # p by up to the number of cells, and a permutation p can never go below
    # 1/(permutations+1). Correcting a permutation p across sixty cells makes
    # significance arithmetically impossible, which is a real trap.
    #
    # The permutation one makes no distributional assumption, so it is reported
    # alongside as a robustness check on skewed data.
    from scipy.stats import norm
    p_analytic = 2.0 * norm.sf(np.abs(z))

    rng = np.random.default_rng(seed)
    extreme = np.zeros(n, dtype=np.int64)
    obs = np.abs(w @ x - mean * wsum)
    for _ in range(permutations):
        shuffled = rng.permutation(x)
        sim = np.abs(w @ shuffled - mean * wsum)
        extreme += (sim >= obs).astype(np.int64)
    p_perm = (extreme + 1.0) / (permutations + 1.0)

    p = benjamini_hochberg(p_analytic)
    return {"z": z, "p": p, "p_analytic": p_analytic, "p_perm": p_perm,
            "perm_floor": 1.0 / (permutations + 1.0),
            "classes": classify(z, p)}


def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    """Control the false discovery rate across many cells.

    Testing thousands of cells at 0.05 produces dozens of "significant" cells by
    chance alone. Without this correction a hotspot map is decorative.
    """
    n = p.size
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    # enforce monotonicity walking back up the list
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty_like(ranked)
    out[order] = np.clip(ranked, 0, 1)
    return out


def classify(z: np.ndarray, p: np.ndarray) -> list[str]:
    """Plain-language class per cell, at the two thresholds planners quote."""
    out = []
    for zi, pi in zip(z, p):
        if pi <= 0.01:
            out.append("hot, 99% confidence" if zi > 0 else "cold, 99% confidence")
        elif pi <= 0.05:
            out.append("hot, 95% confidence" if zi > 0 else "cold, 95% confidence")
        else:
            out.append("not significant")
    return out
