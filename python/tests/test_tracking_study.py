"""Regression guard for the D1 tracking result on a small dev-seed subset.

The authoritative numbers (held-out seeds, 60 episodes) live in docs/results/d1_heldout.json and
docs/D1_EVIDENCE.md; this test only protects against the filter silently regressing.
"""
import numpy as np
import pytest

pytest.importorskip("darkfleet_core", reason="build the darkfleet_core module first")

from darkfleet.evaluate import DEV_SEEDS, bootstrap_ci, tracking_episode  # noqa: E402


def test_bootstrap_ci_brackets_the_mean():
    d = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    mean, lo, hi = bootstrap_ci(d, n_boot=2000)
    assert lo <= mean <= hi and mean == 3.0


def test_pf_improves_negative_log_likelihood_when_sar_is_available():
    eps = [tracking_episode(s, sar_tasks=6, n_particles=2000) for s in DEV_SEEDS[:16]]
    eps = [e for e in eps if e.get("n_dark", 0) > 0]
    assert len(eps) >= 12
    diffs = np.array([e["nll_dr"] - e["nll_pf"] for e in eps])  # positive => PF better
    mean, lo, hi = bootstrap_ci(diffs, n_boot=2000)
    assert lo > 0, (mean, lo, hi)
