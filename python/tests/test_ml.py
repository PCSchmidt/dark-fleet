"""D5: anomaly detector, claim calibrator, learned Runner. Skipped when the ML extras are not installed."""
import json
import os

import numpy as np
import pytest

pytest.importorskip("sklearn")
pytest.importorskip("onnxruntime")
pytest.importorskip("skl2onnx")

from darkfleet.ml import anomaly, calibrator, common, runner_es  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def _metrics(model):
    p = os.path.join(ROOT, "models", model, "metrics.json")
    if not os.path.exists(p):
        pytest.skip(f"{p} not present")
    return json.load(open(p, encoding="utf-8"))


# ---- common --------------------------------------------------------------------------------------
def test_ece_and_reliability():
    p = np.array([0.1] * 50 + [0.9] * 50)
    y = np.array([0] * 45 + [1] * 5 + [0] * 5 + [1] * 45)
    assert common.ece(p, y) == pytest.approx(0.0, abs=1e-9)                  # perfectly calibrated bins
    assert common.ece(np.full(100, 0.99), np.array([1] * 60 + [0] * 40)) == pytest.approx(0.39, abs=1e-9)
    rel = common.reliability(p, y)
    assert [r["n"] for r in rel] == [50, 50]


def test_cluster_bootstrap_resamples_groups():
    g = np.repeat(np.arange(20), 5)
    x = np.repeat(np.arange(20, dtype=float), 5)
    m, lo, hi = common.cluster_bootstrap(lambda ix: float(x[ix].mean()), g, n_boot=200)
    assert lo <= m <= hi and hi - lo > 1.0                                  # wide: only 20 independent groups


def test_overlapping_splits_are_refused():
    ok = {"train": {"first_seed": 0, "n": 100}, "test": {"first_seed": 100, "n": 50}}
    common.assert_disjoint_splits(ok)
    with pytest.raises(ValueError):
        common.assert_disjoint_splits({"train": {"first_seed": 0, "n": 100}, "val": {"first_seed": 50, "n": 10}})


@pytest.mark.parametrize("name", ["anomaly", "calibrator"])
def test_committed_configs_have_disjoint_splits_and_avoid_dev_seeds(name):
    cfg = common.load_config(os.path.join(ROOT, "configs", f"{name}.json"))
    common.assert_disjoint_splits(cfg["splits"])
    assert all(v["first_seed"] > 40 for v in cfg["splits"].values())


def test_provenance_records_everything_needed_to_reproduce():
    prov = common.provenance({"a": 1}, "abc")
    assert {"config", "config_hash", "dataset_digest", "git", "versions"} <= set(prov)
    assert prov["config_hash"] == common.canonical_digest({"a": 1}) and prov["git"]["commit"]


# ---- anomaly detector --------------------------------------------------------------------------------
def test_gap_features_need_history_and_are_label_free():
    lk = {"x_m": 0.0, "y_m": 0.0}
    assert anomaly.features([(0, 0, 0), (1, 1, 1)], lk, 6, 0) is None
    hist = [(t, 2000.0 * t, 500.0 * t) for t in range(10)]
    f = anomaly.features(hist, lk, 6, 1)
    assert len(f) == len(anomaly.FEATURES) and f[anomaly.FEATURES.index("silence_ticks")] == 6.0
    assert f[anomaly.FEATURES.index("straightness")] == pytest.approx(1.0)
    assert "mmsi" not in " ".join(anomaly.FEATURES) and "name" not in anomaly.FEATURES


def test_gap_dataset_labels_only_the_runners_dark_window():
    rows = anomaly.build_dataset(list(range(100, 112)), ["dark_run", "honest"], 20, 0.3, [6, 12], 3, workers=1)
    assert rows and all(len(r["x"]) == len(anomaly.FEATURES) for r in rows)
    pos = [r for r in rows if r["y"] == 1]
    assert pos and all(r["kind"] == "dark_run" and r["gap_len"] >= 40 for r in pos)       # runner dark windows are 40-70 ticks
    assert not any(r["y"] == 1 for r in rows if r["kind"] == "honest")


def test_anomaly_onnx_matches_sklearn_and_published_metrics_are_consistent():
    m = _metrics("anomaly")
    assert m["onnx"]["parity_ok"] and m["onnx"]["max_abs_prob_diff"] < 1e-4
    prov = m["provenance"]
    assert prov["config_hash"] == common.canonical_digest(prov["config"]) and prov["dataset_digest"]
    seeds = {k: (v["first_seed"], v["first_seed"] + v["n"]) for k, v in prov["config"]["splits"].items()}
    spans = sorted(seeds.values())
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))                              # splits do not overlap
    assert not any(a <= s < b for a, b in spans for s in range(1, 41))                      # dev seeds never used


def test_anomaly_onnx_runs_end_to_end():
    import onnxruntime as ort
    p = os.path.join(ROOT, "models", "anomaly", "anomaly.onnx")
    if not os.path.exists(p):
        pytest.skip("model not present")
    sess = ort.InferenceSession(p, providers=["CPUExecutionProvider"])
    x = np.zeros((3, len(anomaly.FEATURES)), dtype=np.float32)
    out = sess.run(None, {"features": x})[1]
    assert out.shape == (3, 2) and np.allclose(out.sum(axis=1), 1.0, atol=1e-5)


# ---- claim calibrator ---------------------------------------------------------------------------------
def test_calibration_maps_are_monotone_and_bounded():
    rng = np.random.default_rng(0)
    raw = rng.random(500)
    y = (rng.random(500) < raw).astype(int)
    for cal in (calibrator.fit_platt(raw, y), calibrator.fit_isotonic(raw, y)):
        q = calibrator.apply_calibration(cal, np.linspace(0, 1, 50))
        assert np.all(np.diff(q) >= -1e-9) and q.min() > 0 and q.max() < 1


def test_claim_features_match_the_declared_list_and_use_only_hunter_data():
    from darkfleet.agent.env import HunterEnv
    env = HunterEnv(1, "dark_run")
    env.step(30)
    obs = env.hunter_obs()
    ids = [c["contact_id"] for c in obs["contacts"]][:2]
    f = calibrator.claim_features(env.host.analyst, obs, ids[0], ids)
    assert len(f) == len(calibrator.CLAIM_FEATURES) and np.all(np.isfinite(f))
    assert {"outcome", "is_runner", "truth"}.isdisjoint(calibrator.CLAIM_FEATURES)


def test_calibrator_runtime_matches_training_artifacts():
    m = _metrics("calibrator")
    assert m["onnx"]["parity_ok"] and m["onnx"]["max_abs_calibrated_diff"] < 1e-4
    cc = calibrator.ClaimConfidence(os.path.join(ROOT, "models", "calibrator"))
    p = cc.predict_row(np.zeros(len(calibrator.CLAIM_FEATURES)))
    assert 0.0 < p < 1.0


def test_calibrator_beats_the_overconfident_baseline_on_held_out_data():
    r = _metrics("calibrator")["results"]["test_iid"]["models"]
    chosen = f"gbt_{_metrics('calibrator')['results']['chosen_calibration']}"
    assert r[chosen]["log_loss"][0] < r["overconfident_bot"]["log_loss"][0]
    assert r[chosen]["ece"][0] < r["overconfident_bot"]["ece"][0]


# ---- learned Runner ---------------------------------------------------------------------------------------
def test_runner_parameters_are_clipped_and_policy_is_legal():
    p = runner_es.LearnedRunner(np.full(len(runner_es.NAMES), 1e9))
    assert all(runner_es.LO[i] <= p.p[n] <= runner_es.HI[i] for i, n in enumerate(runner_es.NAMES))
    from darkfleet.episode import make_scenario, play
    from darkfleet.policies import HunterBot, HunterConfig
    for name, th in runner_es.baseline_thetas().items():
        r = play(make_scenario(7, brief="profile"), runner_es.LearnedRunner(th, 7), HunterBot(HunterConfig(pf_seed=7, n_particles=300)))
        assert r.verified["code"] == "OK", name


def test_runner_policy_is_deterministic_and_loads_from_json(tmp_path):
    from darkfleet.episode import make_scenario, play
    from darkfleet.policies import HunterBot, HunterConfig
    th = runner_es.baseline_thetas()["scripted_dark_run"]
    hashes = []
    for _ in range(2):
        r = play(make_scenario(9, brief="profile"), runner_es.LearnedRunner(th, 9), HunterBot(HunterConfig(pf_seed=9, n_particles=300)))
        hashes.append(r.replay["final_state_hash"])
    assert hashes[0] == hashes[1]
    path = tmp_path / "pol.json"
    common.write_json(path, {"params": dict(zip(runner_es.NAMES, map(float, th)))})
    assert runner_es.load_policy(path, 9).p == runner_es.LearnedRunner(th, 9).p


def test_published_runner_artifact_is_consistent():
    p = os.path.join(ROOT, "models", "runner", "runner_policy.json")
    if not os.path.exists(p):
        pytest.skip("model not present")
    pol = json.load(open(p, encoding="utf-8"))
    assert set(pol["params"]) == set(runner_es.NAMES)
    for n, v in pol["params"].items():
        assert pol["bounds"][n][0] <= v <= pol["bounds"][n][1]
    m = _metrics("runner")
    for name, per in m["heldout"].items():
        assert all(h["verified"] == h["n"] for h in per.values()), name                    # every replay verified
    cfg_seeds = range(50000, 50000 + m["provenance"]["config"]["heldout_seeds"])
    assert not set(cfg_seeds) & {40000 + g * 100 + i for g in range(40) for i in range(20)}  # held-out != training seeds
