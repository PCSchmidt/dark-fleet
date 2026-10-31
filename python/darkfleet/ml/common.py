"""Shared plumbing for the D5 models: run provenance, dataset digests, metrics with intervals.

Every trained artifact records (ML.md section 8): config and its hash, seeds, code commit (and whether the
tree was dirty), a digest of the exact training data, and library versions.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]


def git_state() -> dict:
    def run(*a):
        try:
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            return ""
    return {"commit": run("rev-parse", "HEAD")[:12] or "unknown", "dirty": bool(run("status", "--porcelain"))}


def versions() -> dict:
    out = {"python": platform.python_version(), "numpy": np.__version__}
    for m in ("sklearn", "onnx", "onnxruntime", "skl2onnx"):
        try:
            out[m] = __import__(m).__version__
        except Exception:
            out[m] = None
    return out


def canonical_digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=float).encode()).hexdigest()


def provenance(config: dict, dataset_digest: str) -> dict:
    return {"config": config, "config_hash": canonical_digest(config), "dataset_digest": dataset_digest,
            "git": git_state(), "versions": versions(), "platform": sys.platform}


def load_config(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, default=float), encoding="utf-8")


# ---- metrics ---------------------------------------------------------------------------------------
def cluster_bootstrap(metric, groups: np.ndarray, n_boot: int = 400, seed: int = 5) -> tuple[float, float, float]:
    """Percentile CI for ``metric(index_array)`` resampling whole groups (episodes), not rows."""
    ug = np.unique(groups)
    idx_of = {g: np.flatnonzero(groups == g) for g in ug}
    point = metric(np.arange(len(groups)))
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        pick = rng.choice(ug, size=len(ug), replace=True)
        v = metric(np.concatenate([idx_of[g] for g in pick]))
        if v is not None and np.isfinite(v):
            vals.append(v)
    if not vals:
        return float(point), float("nan"), float("nan")
    return float(point), float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error with equal-width bins."""
    edges = np.linspace(0, 1, bins + 1)
    tot, n = 0.0, len(p)
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.any():
            tot += m.sum() / n * abs(p[m].mean() - y[m].mean())
    return float(tot)


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.any():
            out.append({"bin": [round(lo, 2), round(hi, 2)], "n": int(m.sum()), "mean_pred": float(p[m].mean()), "frac_pos": float(y[m].mean())})
    return out
