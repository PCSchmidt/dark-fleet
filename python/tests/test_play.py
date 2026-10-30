"""D2: profile briefing, bot target locking and spoof audit, and the playable session/server."""
import json
import threading
import urllib.error
import urllib.request

import jsonschema
import pytest

import darkfleet_core as core
from darkfleet.episode import make_scenario, play
from darkfleet.play.server import App, serve
from darkfleet.play.session import Session, new_session
from darkfleet.policies import HunterBot, HunterConfig, RunnerPolicy

CANARY = "RUNNER-CANARY"


def _validator(schemas, name):
    return jsonschema.Draft202012Validator(schemas[name])


# ---- profile briefing ---------------------------------------------------------------------------
def test_profile_scenario_validates_and_briefs_without_mmsi(schemas):
    s = make_scenario(7, brief="profile")
    _validator(schemas, "scenario").validate(s)
    w = core.World(json.dumps(s))
    obs = json.loads(w.observe("hunter"))
    _validator(schemas, "observation").validate(obs)
    assert "target_mmsi" not in obs["brief"]
    assert obs["brief"]["last_known"]["sigma_m"] > 0


def test_legacy_brief_is_unchanged(schemas):
    s = make_scenario(7)  # default brief="mmsi"
    assert "brief" not in s["mission"]
    obs = json.loads(core.World(json.dumps(s)).observe("hunter"))
    _validator(schemas, "observation").validate(obs)
    assert obs["brief"]["target_mmsi"] == "999000001"


def test_legacy_scenarios_are_byte_identical_to_before_the_option_existed():
    # the profile draw happens last, so the base scenario is identical apart from the brief block
    a, b = make_scenario(11), make_scenario(11, brief="profile")
    b["mission"].pop("brief")
    assert a == b


def test_bad_brief_mode_is_rejected():
    with pytest.raises(Exception):
        core.generate_scenario(1, 20, 144, 6, "nope")


# ---- bot --------------------------------------------------------------------------------------
def test_bot_locks_the_true_target_from_the_profile_brief():
    hits = 0
    for seed in range(1, 9):
        h = HunterBot(HunterConfig(pf_seed=seed, n_particles=800))
        play(make_scenario(seed, brief="profile"), RunnerPolicy("honest", seed=seed), h)
        hits += h.locked_mmsi == "999000001"
    assert hits >= 7  # nearest-to-last-known is a heuristic; it can miss, and the evaluation reports how often


def test_bot_with_audit_detects_a_spoofed_position_and_wins():
    wins = 0
    detected = 0
    for seed in range(1, 7):
        h = HunterBot(HunterConfig(pf_seed=seed, n_particles=800, audit_ais=True))
        r = play(make_scenario(seed, brief="profile"), RunnerPolicy("spoof", seed=seed), h)
        assert r.verified["code"] == "OK"
        detected += h.audit_result == "spoof_suspected"
        wins += r.outcome["outcome"] == "correct_supported"
    assert detected >= 4 and wins >= 4


def test_audit_does_not_accuse_an_honest_runner():
    for seed in range(1, 7):
        h = HunterBot(HunterConfig(pf_seed=seed, n_particles=800, audit_ais=True))
        r = play(make_scenario(seed, brief="profile"), RunnerPolicy("honest", seed=seed), h)
        assert h.audit_result != "spoof_suspected"
        assert r.outcome["outcome"] != "wrong_identity"


# ---- session: role filtering and debrief ------------------------------------------------------
def _walk(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v)
    else:
        yield x


def test_hunter_session_never_exposes_truth_before_the_end(schemas):
    s = new_session("hunter", seed=3, runner_kind="dark_run")
    val = _validator(schemas, "observation")
    for _ in range(60):
        st = s.state()
        val.validate(st["observation"])
        text = json.dumps(st)
        assert CANARY not in text
        assert "true_track" not in text and "debrief" not in text
        assert "own" not in st["observation"] and "mission" not in st["observation"]
        with pytest.raises(PermissionError):
            s.replay_text()
        s.step(1)


def test_runner_session_never_exposes_hunter_data(schemas):
    s = new_session("runner", seed=4)
    val = _validator(schemas, "observation")
    for _ in range(40):
        st = s.state()
        val.validate(st["observation"])
        assert "brief" not in st["observation"] and "resources" not in st["observation"]
        assert "analyst" not in st
        s.step(1)


def test_human_actions_are_validated_by_the_engine():
    s = new_session("hunter", seed=5, runner_kind="honest")
    ok = s.act([{"action_type": "task_sensor", "params": {"sensor": "sar", "x_m": 0.0, "y_m": 0.0}}])
    assert ok[0]["code"] == "OK"
    bad = s.act([{"action_type": "task_sensor", "params": {"sensor": "eo", "x_m": 0.0, "y_m": 0.0}}])
    assert bad[0]["code"] != "OK"
    forbidden = s.act([{"action_type": "set_course_speed", "params": {"course_deg": 0, "speed_mps": 5}}])
    assert forbidden[0]["code"] != "OK"  # the Hunter cannot steer the Runner
    assert s.rejections


def test_full_hunter_episode_ends_with_a_verified_debrief():
    s = new_session("hunter", seed=6, runner_kind="honest")
    s.step(144)
    st = s.state()
    assert st["ended"]
    d = st["debrief"]
    assert d["verification"]["code"] == "OK"
    assert d["runner_policy"] == "honest"
    assert len(d["true_track"]) > 10
    assert json.loads(s.replay_text())["content_hash"] == d["replay_hash"]


def test_full_runner_episode_vs_bot_hunter():
    s = new_session("runner", seed=8)
    obs = s.state()["observation"]
    d = obs["mission"]["delivery"]
    import math
    course = math.degrees(math.atan2(d["x_m"] - obs["own"]["x_m"], d["y_m"] - obs["own"]["y_m"])) % 360
    s.act([{"action_type": "set_course_speed", "params": {"course_deg": course, "speed_mps": 6.0}}])
    s.step(144)
    st = s.state()
    assert st["ended"] and st["debrief"]["verification"]["code"] == "OK"


def test_invalid_session_arguments():
    with pytest.raises(ValueError):
        Session(role="referee", seed=1)
    with pytest.raises(ValueError):
        Session(role="hunter", seed=1, runner_kind="teleport")


# ---- HTTP -----------------------------------------------------------------------------------
@pytest.fixture()
def http_server():
    srv = serve(0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_http_flow_and_guards(http_server):
    assert _call(http_server, "GET", "/api/state")[0] == 409           # no session yet
    code, page = _call(http_server, "GET", "/")
    assert code == 200 and "<canvas" in page
    code, body = _call(http_server, "POST", "/api/new", {"role": "hunter", "seed": 2, "runner_kind": "dark_run"})
    assert code == 200 and CANARY not in body
    assert _call(http_server, "POST", "/api/new", {"role": "referee"})[0] == 400
    code, body = _call(http_server, "POST", "/api/step", {"n": 3})
    assert code == 200 and json.loads(body)["observation"]["tick"] == 3
    assert _call(http_server, "POST", "/api/step", {"n": "x"})[0] == 400
    assert _call(http_server, "POST", "/api/act", {"actions": "nope"})[0] == 400
    assert _call(http_server, "GET", "/api/replay")[0] == 403          # not until the end
    assert _call(http_server, "GET", "/nope")[0] == 404
    code, body = _call(http_server, "POST", "/api/step", {"n": 144})
    assert json.loads(body)["ended"]
    assert _call(http_server, "GET", "/api/replay")[0] == 200
    assert _call(http_server, "POST", "/api/act", {"actions": []})[0] == 409  # ended


def test_app_handler_is_a_pure_dispatch():
    app = App()
    status, _, _ = app.handle("POST", "/api/new", {"role": "runner", "seed": 9})
    assert status == 200
