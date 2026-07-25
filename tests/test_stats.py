"""Hot spot statistics have to be honest in both directions.

A test that only checks it finds a cluster will pass on a tool that calls
everything significant. These check the other half too: random data must come
back quiet.

They also pin the bug that made the first version useless. Permutation p-values
cannot go below 1/(permutations+1), so correcting them across sixty cells made
significance arithmetically impossible: a z-score of 5.08 was reported as "not
significant". Classes now come from the analytic p, corrected.
"""
import numpy as np
import pytest

from branch import stats


def _grid(n_side=8):
    """A square lattice of H3-like indices is not needed: build weights directly."""
    n = n_side * n_side
    w = np.zeros((n, n))
    for i in range(n_side):
        for j in range(n_side):
            a = i * n_side + j
            w[a, a] = 1.0
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ii, jj = i + di, j + dj
                if 0 <= ii < n_side and 0 <= jj < n_side:
                    w[a, ii * n_side + jj] = 1.0
    return w


def test_random_data_produces_almost_no_hotspots():
    w = _grid()
    rng = np.random.default_rng(3)
    x = rng.normal(100, 15, w.shape[0])
    out = stats.getis_ord(x, w, permutations=99, seed=1)
    hot = sum(1 for c in out["classes"] if c != "not significant")
    assert hot <= 2, f"{hot} cells called significant on random data"


def test_a_planted_cluster_is_found():
    w = _grid()
    x = np.full(w.shape[0], 100.0)
    for cell in (27, 28, 35, 36):          # a solid block of high values
        x[cell] = 900.0
    out = stats.getis_ord(x, w, permutations=99, seed=1)
    assert any(c.startswith("hot") for c in out["classes"])


def test_significance_is_reachable_at_all():
    """The regression: correcting a permutation p made every cell insignificant."""
    w = _grid()
    x = np.full(w.shape[0], 10.0)
    x[27] = x[28] = x[35] = x[36] = 5000.0
    out = stats.getis_ord(x, w, permutations=99, seed=1)
    assert out["p"].min() < 0.01
    assert out["perm_floor"] == pytest.approx(1 / 100)


def test_multiple_testing_correction_is_applied():
    p = np.array([0.001, 0.02, 0.03, 0.5])
    adj = stats.benjamini_hochberg(p)
    assert (adj >= p).all()                      # correction only ever raises p
    assert adj[0] == pytest.approx(0.004)        # 0.001 * 4/1
    assert (np.diff(adj[np.argsort(p)]) >= -1e-12).all()   # stays monotone


def test_it_refuses_when_there_is_too_little_to_say():
    w = _grid(4)                                   # 16 cells, under the floor of 30
    with pytest.raises(ValueError, match="at least 30"):
        stats.getis_ord(np.arange(16.0), w)


def test_it_refuses_when_every_value_is_identical():
    w = _grid()
    with pytest.raises(ValueError, match="same value"):
        stats.getis_ord(np.full(w.shape[0], 7.0), w)
