"""D1 behaviour from Python: schema conformance of generated data, replay verification (US-3),
tamper detection, tracker unit behaviour, and the import boundary."""
import json
import math
import os

import jsonschema
import pytest

core = pytest.importorskip("darkfleet_core", reason="build the darkfleet_core module first")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PKG = os.path.join(ROOT, "python", "darkfleet")

from darkfleet.episode import default_play, make_scenario, play  # noqa: E402
from darkfleet.policies import HunterBot, HunterConfig, RunnerPolicy  # noqa: E402
from darkfleet.tracker import DeadReckoning, ParticleFilter, PFConfig  # noqa: E402


def validator(schemas, name):
    return jsonschema.Draft202012Validator(schemas[name])


def test_generated_scenarios_validate(schemas):
    v = validator(schemas, "scenario")
    for seed in (1, 2, 3, 50, 999):
        v.validate(make_scenario(seed))


def test_generator_is_deterministic_and_seed_sensitive():
    a, b = core.generate_scenario(5), core.generate_scenario(5)
    assert a == b
    assert core.generate_scenario(6) != a


def test_episode_observations_and_actions_conform_to_schemas(schemas):
    ov, av = validator(schemas, "observation"), validator(schemas, "action")
    sc = make_scenario(11)
    w = core.World(json.dumps(sc))
    runner, hunter = RunnerPolicy("dark_run", seed=11), HunterBot(HunterConfig(pf_seed=11, n_particles=500))
    for _ in range(60):
        if w.ended:
            break
        obs_r, obs_h = json.loads(w.observe("runner")), json.loads(w.observe("hunter"))
        ov.validate(obs_r)
        ov.validate(obs_h)
        for a in runner.act(obs_r) + hunter.act(obs_h):
            av.validate(a)
            w.submit_action(json.dumps(a))
        w.step(1)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_every_recorded_episode_verifies(seed):
    r = default_play(seed)
    assert r.verified["code"] == "OK", r.verified
    assert r.verified["outcome"] == r.outcome["outcome"]


def test_replay_tamper_detection_from_python():
    r = default_play(3)
    scenario_text = json.dumps(r.scenario)

    naive = json.loads(json.dumps(r.replay))
    naive["result"]["hunter_score"] = 9999.0
    out = json.loads(core.verify_replay(scenario_text, json.dumps(naive)))
    assert out["code"] == "E_HASH"

    other = dict(r.scenario, seed="7777")
    out = json.loads(core.verify_replay(json.dumps(other), json.dumps(r.replay)))
    assert out["code"] == "E_HASH"

    out = json.loads(core.verify_replay(scenario_text, "[]"))
    assert out["code"] == "E_SCHEMA"
    with pytest.raises(ValueError, match="E_SCHEMA"):
        core.verify_replay(scenario_text, "{broken")


def test_hunter_never_sees_mission_or_truth_strings():
    sc = make_scenario(21)
    secrets = [v["id"] for v in sc["entities"]["vessels"]] + [v["secret_note"] for v in sc["entities"]["vessels"] if "secret_note" in v]
    w = core.World(json.dumps(sc))
    runner, hunter = RunnerPolicy("dark_run", seed=21), HunterBot(HunterConfig(pf_seed=21, n_particles=500))
    while not w.ended:
        obs_r, obs_h_text = json.loads(w.observe("runner")), w.observe("hunter")
        for s in secrets:
            assert s not in obs_h_text
        obs_h = json.loads(obs_h_text)
        assert "mission" not in obs_h and "own" not in obs_h
        for a in runner.act(obs_r) + hunter.act(obs_h):
            w.submit_action(json.dumps(a))
        w.step(1)


# ---- trackers ----------------------------------------------------------------------------------
def _obs(tick, fixes=(), brief=True, sar=(), pending=(), sar_radius=40000.0):
    contacts = [{"contact_id": "c-00000000", "source": "ais", "claimed_mmsi": "999000001", "claimed_name": "X",
                 "x_m": x, "y_m": y, "sigma_m": 30.0, "age_ticks": age} for age, x, y in fixes]
    for age, x, y in sar:
        contacts.append({"contact_id": "c-11111111", "source": "sar", "size_class": "large", "x_m": x, "y_m": y,
                         "sigma_m": 150.0, "age_ticks": age})
    o = {"tick": tick, "contacts": contacts, "status": {"state": "running", "outcome": "none"},
         "resources": {"sar_tasks_remaining": 1, "sar_footprint_radius_m": sar_radius,
                       "pending_taskings": [{"exec_tick": e, "x_m": x, "y_m": y} for e, x, y in pending]}}
    if brief:
        o["brief"] = {"target_mmsi": "999000001", "target_class": "vlcc"}
    return o


def test_dead_reckoning_extrapolates_a_straight_track():
    dr = DeadReckoning(tick_s=300.0)
    for t in range(0, 10):
        dr.update(_obs(t, fixes=[(0, 1000.0 + 2000.0 * t, 5000.0)]))
    for t in range(10, 20):
        dr.update(_obs(t, fixes=[]))
    x, y = dr.estimate()
    # 2000 m per tick east, last fix at t=9 => at t=19 expect x ~ 1000 + 2000*19
    assert abs(x - (1000.0 + 2000.0 * 19)) < 400.0
    assert abs(y - 5000.0) < 100.0


def test_particle_filter_follows_ais_and_widens_in_the_dark():
    pf = ParticleFilter(300.0, PFConfig(n_particles=2000, seed=1))
    for t in range(0, 12):
        pf.update(_obs(t, fixes=[(0, 7.0 * 300.0 * t, 0.0)]))
        # the track runs due east at 7 m/s
    x0, _ = pf.estimate()
    assert abs(x0 - 7.0 * 300.0 * 11) < 300.0
    spread_on = pf.spread_m()
    for t in range(12, 40):
        pf.update(_obs(t, fixes=[]))
    assert pf.spread_m() > 5 * spread_on
    x1, _ = pf.estimate()
    assert abs(x1 - 7.0 * 300.0 * 39) < 15000.0


def test_particle_filter_negative_sar_information_moves_mass_out_of_the_footprint():
    pf = ParticleFilter(300.0, PFConfig(n_particles=3000, seed=2))
    for t in range(0, 8):
        pf.update(_obs(t, fixes=[(0, 7.0 * 300.0 * t, 0.0)]))
    for t in range(8, 20):
        pf.update(_obs(t, pending=[(20, 7.0 * 300.0 * 20, 0.0)]))
    est = pf.estimate()
    before = pf.mass_within(7.0 * 300.0 * 20, 0.0, 40000.0)
    pf.update(_obs(20, pending=[(20, 7.0 * 300.0 * 20, 0.0)]))  # pass at tick 20, nothing seen
    after = pf.mass_within(7.0 * 300.0 * 20, 0.0, 40000.0)
    assert before > 0.8 and after < before


def test_particle_filter_uses_a_sar_detection():
    pf = ParticleFilter(300.0, PFConfig(n_particles=3000, seed=3))
    for t in range(0, 8):
        pf.update(_obs(t, fixes=[(0, 7.0 * 300.0 * t, 0.0)]))
    truth_x = 7.0 * 300.0 * 20
    for t in range(8, 20):
        pf.update(_obs(t, pending=[(20, truth_x, 0.0)]))
    pf.update(_obs(20, sar=[(0, truth_x + 500.0, 100.0)], pending=[(20, truth_x, 0.0)]))
    ex, ey = pf.estimate()
    assert math.hypot(ex - truth_x, ey) < 4000.0
    assert pf.spread_m() < 8000.0


# ---- boundaries ------------------------------------------------------------------------------------
def test_agent_side_modules_cannot_reach_truth():
    """tracker.py and policies.py may only consume observation documents: no import of the
    compiled core, and no reference to the evaluation (truth) interface."""
    import ast

    for name in ("tracker.py", "policies.py"):
        tree = ast.parse(open(os.path.join(PKG, name), encoding="utf8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith("darkfleet_core") for a in node.names), name
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("darkfleet_core"), name
            if isinstance(node, (ast.Name, ast.Attribute)):
                ident = node.id if isinstance(node, ast.Name) else node.attr
                assert ident not in ("runner_truth", "evaluation"), f"{name} references {ident}"
