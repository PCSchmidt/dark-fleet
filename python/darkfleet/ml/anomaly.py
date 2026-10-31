"""AIS gap anomaly detector (ML.md section 2): is this AIS silence the target going dark, or an innocent fault?

Task. A Hunter watching AIS sees vessels fall silent. At fixed silence checkpoints (6, 12, 18, 24 ticks), using only
what was reported *before* the gap, score the probability that the gap is the Runner deliberately going dark.
The Runner's dark windows last 40-70 ticks; innocent outages last 6-29 ticks and random drop-outs 1-2, so waiting
long enough solves the task by itself. The point of the model is to decide *earlier* than that rule can.

Labels come from the Runner's script (dark window), never from anything a Hunter sees. MMSI and name are NOT features.

    python -m darkfleet.ml.anomaly --config configs/anomaly.json --out models/anomaly
"""
from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from .common import assert_disjoint_splits, canonical_digest, cluster_bootstrap, load_config, provenance, write_json

TICK_S = 300.0
RUNNER_MMSI = "999000001"
KINEMATIC = ["speed_mps", "speed_std", "hdg_sin", "hdg_cos", "turn_rate_deg", "straightness"]
CONTEXT = ["x_km", "y_km", "dist_last_known_km", "gap_start_tick", "prior_gaps", "track_len"]
SILENCE = ["silence_ticks"]
FEATURES = KINEMATIC + CONTEXT + SILENCE


# ---- data -----------------------------------------------------------------------------------------
def features(hist: list[tuple[int, float, float]], last_known: dict, silence: int, prior_gaps: int) -> list[float] | None:
    """Features from AIS reports up to the start of the gap. ``hist`` = [(tick, x_m, y_m)] sorted by tick."""
    if len(hist) < 4:
        return None
    h = hist[-8:]
    t = np.array([p[0] for p in h], float)
    xy = np.array([[p[1], p[2]] for p in h], float)
    seg = np.diff(xy, axis=0)
    dt = np.diff(t) * TICK_S
    sp = np.hypot(seg[:, 0], seg[:, 1]) / dt
    hd = np.degrees(np.arctan2(seg[:, 0], seg[:, 1]))
    turn = np.abs((np.diff(hd) + 180.0) % 360.0 - 180.0)
    path = float(np.hypot(seg[:, 0], seg[:, 1]).sum())
    net = float(np.hypot(*(xy[-1] - xy[0])))
    mean_h = math.atan2(float(np.sum(np.sin(np.radians(hd)))), float(np.sum(np.cos(np.radians(hd)))))
    x, y = float(xy[-1][0]), float(xy[-1][1])
    return [float(sp.mean()), float(sp.std()), math.sin(mean_h), math.cos(mean_h),
            float(turn.mean()) if len(turn) else 0.0, net / path if path > 0 else 1.0,
            x / 1000.0, y / 1000.0, math.hypot(x - last_known["x_m"], y - last_known["y_m"]) / 1000.0,
            float(hist[-1][0]), float(prior_gaps), float(len(hist)), float(silence)]


def collect_episode(job: tuple) -> list[dict]:
    """Passive-observer episode: returns one row per (gap, checkpoint) reached."""
    seed, kind, n_bg, outage_p, checkpoints, min_gap, speed_scale = job
    import darkfleet_core as core

    from ..episode import make_scenario
    from ..policies import RunnerPolicy

    scenario = make_scenario(seed, n_bg, brief="profile", outage_probability=outage_p)
    w = core.World(json.dumps(scenario))
    runner = RunnerPolicy(kind=kind, seed=seed, max_speed_mps=8.5 * speed_scale)
    reports: dict[str, list[tuple[int, float, float]]] = {}
    last_known = None
    for _ in range(int(scenario["time"]["duration_ticks"])):
        if w.ended:
            break
        for a in runner.act(json.loads(w.observe("runner"))):
            w.submit_action(json.dumps(a))
        w.step(1)
        obs = json.loads(w.observe("hunter"))
        if last_known is None:
            last_known = obs["brief"]["last_known"]
        for c in obs["contacts"]:
            if c["source"] == "ais" and c["age_ticks"] == 0:
                reports.setdefault(c["claimed_mmsi"], []).append((obs["tick"], c["x_m"], c["y_m"]))
    end = w.tick
    rows = []
    for mmsi, rep in reports.items():
        ticks = [r[0] for r in rep]
        prior = 0
        for i in range(len(rep)):
            nxt = ticks[i + 1] if i + 1 < len(rep) else end + 1
            gap_len = nxt - ticks[i] - 1
            if gap_len < min_gap:
                continue
            malicious = bool(mmsi == RUNNER_MMSI and kind == "dark_run" and runner.t_dark <= ticks[i] + 1 <= runner.t_dark + 3)
            for s in checkpoints:
                if gap_len >= s:
                    f = features(rep[: i + 1], last_known, s, prior)
                    if f is not None:
                        rows.append({"seed": seed, "kind": kind, "mmsi_hash": hash_id(mmsi), "gap_len": gap_len, "s": s,
                                     "y": int(malicious), "x": f})
            prior += 1
    return rows


def hash_id(mmsi: str) -> int:
    return int(canonical_digest(mmsi)[:8], 16)


def build_dataset(seeds: list[int], kinds: list[str], n_bg: int, outage_p: float, checkpoints: list[int], min_gap: int,
                  workers: int, speed_scale: float = 1.0) -> list[dict]:
    jobs = [(s, kinds[s % len(kinds)], n_bg, outage_p, checkpoints, min_gap, speed_scale) for s in seeds]
    if workers <= 1:
        out = [collect_episode(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            out = list(ex.map(collect_episode, jobs, chunksize=4))
    return [r for part in out for r in part]


def to_arrays(rows: list[dict]):
    X = np.array([r["x"] for r in rows], dtype=np.float32)
    y = np.array([r["y"] for r in rows], dtype=int)
    g = np.array([r["seed"] for r in rows])
    s = np.array([r["s"] for r in rows])
    gl = np.array([r["gap_len"] for r in rows])
    return X, y, g, s, gl


# ---- models ---------------------------------------------------------------------------------------
def make_models(cfg: dict, seed: int):
    from sklearn.ensemble import GradientBoostingClassifier, IsolationForest
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    hgb = GradientBoostingClassifier(random_state=seed, **cfg["gbt"])
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=cfg.get("logreg_C", 1.0)))
    iso = IsolationForest(n_estimators=200, random_state=seed, contamination="auto")
    return hgb, lr, iso


def cols(names: list[str]) -> list[int]:
    return [FEATURES.index(n) for n in names]


def recall_at_fpr(val_scores, val_y, test_scores, test_y, fpr: float):
    neg = val_scores[val_y == 0]
    thr = float(np.quantile(neg, 1 - fpr)) if len(neg) else 1.0
    pos_rate = float((test_scores[test_y == 1] > thr).mean()) if (test_y == 1).any() else float("nan")
    fp_rate = float((test_scores[test_y == 0] > thr).mean()) if (test_y == 0).any() else float("nan")
    return pos_rate, fp_rate, thr


def evaluate_split(name: str, models: dict, val, test, checkpoints: list[int], fpr: float, rule_T: int) -> dict:
    from sklearn.metrics import average_precision_score, roc_auc_score
    Xv, yv, gv, sv, glv = val
    Xt, yt, gt, st, glt = test
    out = {}
    for s in checkpoints:
        mv, mt = sv == s, st == s
        row = {"n_test": int(mt.sum()), "n_pos_test": int(yt[mt].sum()), "n_test_episodes": int(len(np.unique(gt[mt])))}
        # rule baseline: flag when the silence so far has reached T (T chosen on validation)
        flag = float(s >= rule_T)
        row["rule_gap_length"] = {"recall": flag if (yt[mt] == 1).any() else float("nan"),
                                  "fpr": flag if (yt[mt] == 0).any() else float("nan"), "threshold_T": rule_T}
        for mname, fn in models.items():
            sc_v, sc_t = fn(Xv[mv]), fn(Xt[mt])
            if (yt[mt] == 1).any() and (yt[mt] == 0).any():
                idx = np.flatnonzero(mt)
                gtm = gt[mt]

                def pr(ix):
                    return average_precision_score(yt[mt][ix], sc_t[ix]) if yt[mt][ix].any() else None

                def au(ix):
                    return roc_auc_score(yt[mt][ix], sc_t[ix]) if 0 < yt[mt][ix].sum() < len(ix) else None

                def rc(ix):
                    return recall_at_fpr(sc_v, yv[mv], sc_t[ix], yt[mt][ix], fpr)[0]
                row[mname] = {"pr_auc": cluster_bootstrap(pr, gtm), "roc_auc": cluster_bootstrap(au, gtm),
                              "recall_at_fpr": cluster_bootstrap(rc, gtm),
                              "test_fpr": recall_at_fpr(sc_v, yv[mv], sc_t, yt[mt], fpr)[1], "target_fpr": fpr}
        out[str(s)] = row
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Train and evaluate the AIS gap anomaly detector")
    ap.add_argument("--config", default="configs/anomaly.json")
    ap.add_argument("--out", default="models/anomaly")
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    assert_disjoint_splits(cfg["splits"])
    t0 = time.perf_counter()
    cp, mg = cfg["checkpoints"], cfg["min_gap"]
    ds = {}
    for split, spec in cfg["splits"].items():
        seeds = list(range(spec["first_seed"], spec["first_seed"] + spec["n"]))
        ds[split] = build_dataset(seeds, cfg["kinds"], spec.get("n_background", 20), spec.get("outage_probability", 0.3), cp, mg, args.workers,
                               spec.get("runner_speed_scale", 1.0))
    digest = canonical_digest({k: [(r["seed"], r["s"], r["y"], [round(v, 4) for v in r["x"]]) for r in v] for k, v in ds.items()})
    A = {k: to_arrays(v) for k, v in ds.items()}
    Xtr, ytr = A["train"][0], A["train"][1]

    hgb, lr, iso = make_models(cfg, cfg["seed"])
    hgb.fit(Xtr, ytr)
    lr.fit(Xtr, ytr)
    iso.fit(Xtr[ytr == 0])                                    # unsupervised: fit on innocent gaps only
    full = {"gbt": lambda X: hgb.predict_proba(X)[:, 1], "logreg": lambda X: lr.predict_proba(X)[:, 1],
            "isolation_forest": lambda X: -iso.score_samples(X)}
    # rule baseline threshold: the innocent gap length exceeded by only target_fpr of innocent gaps (validation)
    inno = A["val"][4][A["val"][1] == 0]
    rule_T = int(np.ceil(np.quantile(inno, 1 - cfg["target_fpr"]))) if len(inno) else max(cp)
    results = {"rule_T": rule_T}
    for split in ("val", "test_iid", "test_shift", "test_mimic"):
        if split in A:
            results[split] = evaluate_split(split, full, A["val"], A[split], cp, cfg["target_fpr"], rule_T)

    # ablations: feature groups (GBT), evaluated on the iid test split
    abl = {}
    for label, names in {"kinematic_only": KINEMATIC + SILENCE, "context_only": CONTEXT + SILENCE, "no_silence_feature": KINEMATIC + CONTEXT}.items():
        c = cols(names)
        m = make_models(cfg, cfg["seed"])[0].fit(Xtr[:, c], ytr)
        fn = {label: (lambda X, m=m, c=c: m.predict_proba(X[:, c])[:, 1])}
        Xv, yv, gv, sv, glv = A["val"]
        Xt, yt, gt, st, glt = A["test_iid"]
        abl[label] = evaluate_split("test_iid", {label: fn[label]}, (Xv, yv, gv, sv, glv), (Xt, yt, gt, st, glt), cp, cfg["target_fpr"], rule_T)
    results["ablations_test_iid"] = abl
    abl_m = {}
    if "test_mimic" in A:
        for label, names in {"kinematic_only": KINEMATIC + SILENCE, "all_features": FEATURES}.items():
            c = cols(names)
            m = make_models(cfg, cfg["seed"])[0].fit(Xtr[:, c], ytr)
            abl_m[label] = evaluate_split("test_mimic", {label: (lambda X, m=m, c=c: m.predict_proba(X[:, c])[:, 1])}, A["val"], A["test_mimic"], cp, cfg["target_fpr"], rule_T)
    results["ablations_test_mimic"] = abl_m

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prov = provenance(cfg, digest)
    prov["sizes"] = {k: {"rows": len(v), "positives": int(sum(r["y"] for r in v))} for k, v in ds.items()}
    exported = None
    if not args.no_export:
        exported = export_onnx(hgb, A["test_iid"][0], out / "anomaly.onnx")
    write_json(out / "metrics.json", {"provenance": prov, "features": FEATURES, "results": results, "onnx": exported,
                                      "train_seconds": round(time.perf_counter() - t0, 1)})
    print(json.dumps({"rule_T": rule_T, "sizes": prov["sizes"], "onnx": exported, "digest": digest[:16]}, indent=1))
    for s in cp:
        r = results["test_iid"][str(s)]
        g = r.get("gbt", {})
        if g:
            print(f"s={s}: gbt recall@{cfg['target_fpr']:.0%}FPR {g['recall_at_fpr'][0]:.2f} [{g['recall_at_fpr'][1]:.2f},{g['recall_at_fpr'][2]:.2f}] "
                  f"pr_auc {g['pr_auc'][0]:.2f}; rule recall {r['rule_gap_length']['recall']}")


def export_onnx(model, X_check: np.ndarray, path: Path) -> dict:
    """Export to ONNX and verify parity with scikit-learn on held-out rows."""
    import onnxruntime as ort
    from skl2onnx import convert_sklearn
    from skl2onnx.common.data_types import FloatTensorType
    onx = convert_sklearn(model, initial_types=[("features", FloatTensorType([None, len(FEATURES)]))],
                          options={id(model): {"zipmap": False}}, target_opset=17)
    path.write_bytes(onx.SerializeToString())
    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    p_onnx = sess.run(None, {"features": X_check.astype(np.float32)})[1][:, 1]
    p_sk = model.predict_proba(X_check.astype(np.float32))[:, 1]
    diff = float(np.max(np.abs(p_onnx - p_sk)))
    return {"path": str(path), "bytes": path.stat().st_size, "max_abs_prob_diff": diff, "parity_ok": diff < 1e-4, "rows_checked": int(len(X_check))}


if __name__ == "__main__":
    main()
