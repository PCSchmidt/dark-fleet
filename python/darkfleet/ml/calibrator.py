"""Claim calibrator (ML.md section 5): P(this claim is a correct, supported identification).

A Hunter about to file a claim has evidence, a tracker, and a history. The bots and the Case Officer treat every claim
they decide to make as certain; this model gives them a calibrated probability, so a threshold can be a decision
(file only above it) and calibration can be measured at last.

Features are computed from the Hunter's own observation and its analyst bot's state only (`claim_features`), so
the same function serves training (bot episodes) and inference (the Case Officer).

    python -m darkfleet.ml.calibrator --config configs/calibrator.json --out models/calibrator
"""
from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from .common import assert_disjoint_splits, canonical_digest, cluster_bootstrap, ece, load_config, provenance, reliability, write_json

CLAIM_FEATURES = ["n_cited", "n_sar_cited", "n_ais_cited", "chain_span_ticks", "claimed_age_ticks", "max_silent_ticks",
                  "audit_consistent", "audit_spoof", "audit_inconclusive", "lock_dist_km", "lock_margin_km",
                  "n_ais_visible", "tick", "pf_spread_km", "dist_claim_to_estimate_km", "sar_remaining", "claimed_is_sar"]
CAP_KM = 100.0


def claim_features(bot, obs: dict, contact_id: str, evidence: list[str]) -> list[float]:
    cs = {c["contact_id"]: c for c in obs["contacts"]}
    ids = list(dict.fromkeys([contact_id, *evidence]))
    cited = [cs[i] for i in ids if i in cs]
    sar = [c for c in cited if c["source"] == "sar"]
    ais = [c for c in cited if c["source"] == "ais"]
    ticks = [obs["tick"] - c["age_ticks"] for c in sar]
    claimed = cs.get(contact_id)
    est = bot.estimate()
    spread = bot.pf.spread_m()
    d_est = math.hypot(claimed["x_m"] - est[0], claimed["y_m"] - est[1]) / 1000.0 if claimed and est else CAP_KM
    return [float(len(ids)), float(len(sar)), float(len(ais)), float(max(ticks) - min(ticks)) if len(ticks) >= 2 else 0.0,
            float(claimed["age_ticks"]) if claimed else 0.0, float(bot.max_silent),
            float(bot.audit_result == "consistent"), float(bot.audit_result == "spoof_suspected"), float(bot.audit_result == "inconclusive"),
            min(CAP_KM, (bot.lock_dist_m if bot.lock_dist_m is not None else CAP_KM * 1000) / 1000.0),
            min(CAP_KM, (bot.lock_margin_m if bot.lock_margin_m is not None else 0.0) / 1000.0),
            float(sum(1 for c in obs["contacts"] if c["source"] == "ais")), float(obs["tick"]),
            min(CAP_KM, spread / 1000.0) if math.isfinite(spread) else CAP_KM, min(CAP_KM, d_est),
            float(obs["resources"]["sar_tasks_remaining"]), float(bool(claimed and claimed["source"] == "sar"))]


# ---- data -----------------------------------------------------------------------------------------
VARIANTS = [dict(tracker="pf", require_dark_ticks=0, audit_ais=False), dict(tracker="pf", require_dark_ticks=6, audit_ais=True),
            dict(tracker="dr", require_dark_ticks=0, audit_ais=False), dict(tracker="pf", require_dark_ticks=0, audit_ais=True)]
TIERS = [("dark_run", 20), ("honest", 20), ("spoof", 20), ("dark_run", 45)]


def collect_claim(job: tuple) -> dict | None:
    seed, n_particles, force_tier = job
    import darkfleet_core as core

    from ..episode import make_scenario
    from ..policies import HunterBot, HunterConfig, RunnerPolicy

    variant = VARIANTS[seed % len(VARIANTS)]
    kind, n_bg = TIERS[(seed // len(VARIANTS)) % len(TIERS)] if force_tier is None else TIERS[force_tier]
    scenario = make_scenario(seed, n_bg, brief="profile")
    w = core.World(json.dumps(scenario))
    runner = RunnerPolicy(kind=kind, seed=seed)
    bot = HunterBot(HunterConfig(pf_seed=seed, n_particles=n_particles, **variant))
    feats = None
    while not w.ended:
        obs_r = json.loads(w.observe("runner"))
        obs_h = json.loads(w.observe("hunter"))
        for a in runner.act(obs_r):
            w.submit_action(json.dumps(a))
        for a in bot.act(obs_h):
            if a["action_type"] == "file_claim" and feats is None:
                feats = claim_features(bot, obs_h, a["params"]["contact_id"], a["params"]["evidence"])
            w.submit_action(json.dumps(a))
        w.step(1)
    if feats is None:
        return None
    oc = json.loads(w.outcome())["outcome"]
    return {"seed": seed, "x": feats, "y": int(oc == "correct_supported"), "outcome": oc, "kind": kind, "n_bg": n_bg,
            "variant": seed % len(VARIANTS)}


def build_dataset(first: int, n: int, n_particles: int, workers: int, force_tier: int | None = None) -> list[dict]:
    jobs = [(s, n_particles, force_tier) for s in range(first, first + n)]
    if workers <= 1:
        out = [collect_claim(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            out = list(ex.map(collect_claim, jobs, chunksize=4))
    return [r for r in out if r is not None]


def arrays(rows):
    return (np.array([r["x"] for r in rows], dtype=np.float32), np.array([r["y"] for r in rows], dtype=int),
            np.array([r["seed"] for r in rows]))


# ---- calibration ----------------------------------------------------------------------------------
def logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_platt(raw: np.ndarray, y: np.ndarray) -> dict:
    from sklearn.linear_model import LogisticRegression
    lr = LogisticRegression(C=1e6, max_iter=1000).fit(logit(raw).reshape(-1, 1), y)
    return {"kind": "platt", "a": float(lr.coef_[0][0]), "b": float(lr.intercept_[0])}


def fit_isotonic(raw: np.ndarray, y: np.ndarray) -> dict:
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999).fit(raw, y)
    return {"kind": "isotonic", "x": [float(v) for v in iso.X_thresholds_], "y": [float(v) for v in iso.y_thresholds_]}


def apply_calibration(cal: dict, raw: np.ndarray) -> np.ndarray:
    if cal["kind"] == "identity":
        return np.clip(raw, 1e-6, 1 - 1e-6)
    if cal["kind"] == "platt":
        return 1.0 / (1.0 + np.exp(-(cal["a"] * logit(raw) + cal["b"])))
    return np.interp(raw, cal["x"], cal["y"])


def scores(p: np.ndarray, y: np.ndarray) -> dict:
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"log_loss": float(log_loss(y, p, labels=[0, 1])), "brier": float(brier_score_loss(y, p)),
            "ece": ece(p, y), "auroc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else None}


def ci_scores(p, y, g):
    out = {}
    for name, fn in {"log_loss": lambda ix: scores(p[ix], y[ix])["log_loss"], "brier": lambda ix: scores(p[ix], y[ix])["brier"],
                     "ece": lambda ix: scores(p[ix], y[ix])["ece"]}.items():
        out[name] = cluster_bootstrap(fn, g, n_boot=300)
    out["auroc"] = scores(p, y)["auroc"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Train and evaluate the claim calibrator")
    ap.add_argument("--config", default="configs/calibrator.json")
    ap.add_argument("--out", default="models/calibrator")
    ap.add_argument("--workers", type=int, default=20)
    args = ap.parse_args()
    cfg = load_config(args.config)
    assert_disjoint_splits(cfg["splits"])
    t0 = time.perf_counter()
    ds = {k: build_dataset(v["first_seed"], v["n"], cfg["n_particles"], args.workers, v.get("force_tier"))
          for k, v in cfg["splits"].items()}
    digest = canonical_digest({k: [(r["seed"], r["y"], [round(v, 4) for v in r["x"]]) for r in rows] for k, rows in ds.items()})
    A = {k: arrays(v) for k, v in ds.items()}
    Xtr, ytr, _ = A["train"]
    Xv, yv, _ = A["val"]

    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    gbt = GradientBoostingClassifier(random_state=cfg["seed"], **cfg["gbt"]).fit(Xtr, ytr)
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, C=cfg.get("logreg_C", 1.0))).fit(Xtr, ytr)
    base_rate = float(ytr.mean())
    raw_v = gbt.predict_proba(Xv)[:, 1]
    cals = {"identity": {"kind": "identity"}, "platt": fit_platt(raw_v, yv), "isotonic": fit_isotonic(raw_v, yv)}
    val_ll = {k: scores(apply_calibration(c, raw_v), yv)["log_loss"] for k, c in cals.items()}
    chosen = min(val_ll, key=val_ll.get)

    candidates = {
        "constant_base_rate": lambda X: np.full(len(X), base_rate),
        "overconfident_bot": lambda X: np.full(len(X), 0.99),    # what every agent implicitly claims today
        "logreg": lambda X: lr.predict_proba(X)[:, 1],
        "gbt_raw": lambda X: gbt.predict_proba(X)[:, 1],
        f"gbt_{chosen}": lambda X: apply_calibration(cals[chosen], gbt.predict_proba(X)[:, 1]),
    }
    results = {"chosen_calibration": chosen, "val_log_loss_by_calibration": val_ll, "base_rate_train": base_rate}
    for split in [k for k in A if k.startswith("test")]:
        X, y, g = A[split]
        results[split] = {"n": int(len(y)), "positives": int(y.sum()),
                          "models": {name: ci_scores(fn(X), y, g) for name, fn in candidates.items()},
                          "reliability": {name: reliability(candidates[name](X), y) for name in (f"gbt_{chosen}", "gbt_raw", "overconfident_bot")}}
    # ablation: drop feature groups (GBT + chosen calibration refit on val)
    groups = {"no_lock_features": ["lock_dist_km", "lock_margin_km"], "no_audit_features": ["audit_consistent", "audit_spoof", "audit_inconclusive"],
              "no_tracker_features": ["pf_spread_km", "dist_claim_to_estimate_km"], "no_silence_feature": ["max_silent_ticks"]}
    X, y, g = A["test_iid"]
    abl = {}
    for label, drop in groups.items():
        keep = [i for i, n in enumerate(CLAIM_FEATURES) if n not in drop]
        m = GradientBoostingClassifier(random_state=cfg["seed"], **cfg["gbt"]).fit(Xtr[:, keep], ytr)
        cal = fit_platt(m.predict_proba(Xv[:, keep])[:, 1], yv) if chosen == "platt" else fit_isotonic(m.predict_proba(Xv[:, keep])[:, 1], yv)
        abl[label] = ci_scores(apply_calibration(cal, m.predict_proba(X[:, keep])[:, 1]), y, g)
    results["ablations_test_iid"] = abl
    # selective prediction on the iid test split: file only above a threshold
    p = candidates[f"gbt_{chosen}"](X)
    results["selective_prediction_test_iid"] = [
        {"threshold": t, "coverage": float((p >= t).mean()), "precision": float(y[p >= t].mean()) if (p >= t).any() else None}
        for t in (0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9)]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    onnx_info = export(gbt, cals[chosen], A["test_iid"][0], out)
    prov = provenance(cfg, digest)
    prov["sizes"] = {k: {"rows": len(v), "positives": int(sum(r["y"] for r in v))} for k, v in ds.items()}
    write_json(out / "metrics.json", {"provenance": prov, "features": CLAIM_FEATURES, "results": results, "onnx": onnx_info,
                                      "train_seconds": round(time.perf_counter() - t0, 1)})
    print(json.dumps({"sizes": prov["sizes"], "chosen": chosen, "onnx": onnx_info}, indent=1))
    for name, s in results["test_iid"]["models"].items():
        print(f"{name:20s} log_loss {s['log_loss'][0]:.3f} brier {s['brier'][0]:.3f} ece {s['ece'][0]:.3f} auroc {s['auroc']}")


def export(gbt, cal: dict, X_check: np.ndarray, out: Path) -> dict:
    import onnxruntime as ort
    from skl2onnx import convert_sklearn
    from skl2onnx.common.data_types import FloatTensorType
    onx = convert_sklearn(gbt, initial_types=[("features", FloatTensorType([None, len(CLAIM_FEATURES)]))],
                          options={id(gbt): {"zipmap": False}}, target_opset=17)
    (out / "calibrator.onnx").write_bytes(onx.SerializeToString())
    write_json(out / "calibration.json", {"features": CLAIM_FEATURES, "calibration": cal})
    model = ClaimConfidence(out)
    sk = apply_calibration(cal, gbt.predict_proba(X_check.astype(np.float32))[:, 1])
    ox = np.array([model.predict_row(r) for r in X_check])
    raw_ox = ort.InferenceSession(str(out / "calibrator.onnx"), providers=["CPUExecutionProvider"]).run(None, {"features": X_check.astype(np.float32)})[1][:, 1]
    raw_sk = gbt.predict_proba(X_check.astype(np.float32))[:, 1]
    d_raw, d_end = float(np.max(np.abs(raw_ox - raw_sk))), float(np.max(np.abs(ox - sk)))
    return {"bytes": (out / "calibrator.onnx").stat().st_size, "max_abs_raw_diff": d_raw, "max_abs_calibrated_diff": d_end,
            "parity_ok": d_raw < 1e-4 and d_end < 1e-4, "rows_checked": int(len(X_check))}


class ClaimConfidence:
    """Runtime: ONNX tree ensemble + stored calibration map. Needs onnxruntime."""

    def __init__(self, model_dir: str | Path):
        import onnxruntime as ort
        d = Path(model_dir)
        self.sess = ort.InferenceSession(str(d / "calibrator.onnx"), providers=["CPUExecutionProvider"])
        meta = json.loads((d / "calibration.json").read_text(encoding="utf-8"))
        if meta["features"] != CLAIM_FEATURES:
            raise ValueError("feature list in the model directory does not match this code")
        self.cal = meta["calibration"]

    def predict_row(self, x) -> float:
        raw = self.sess.run(None, {"features": np.asarray(x, dtype=np.float32).reshape(1, -1)})[1][:, 1]
        return float(apply_calibration(self.cal, raw)[0])

    def predict(self, bot, obs: dict, contact_id: str, evidence: list[str]) -> float:
        return self.predict_row(claim_features(bot, obs, contact_id, evidence))


if __name__ == "__main__":
    main()
