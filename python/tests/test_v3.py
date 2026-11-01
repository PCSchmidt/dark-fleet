"""Rules v3: the mission requires a loitering transfer; the scripted Runner and Hunter, the Case Officer and the bench handle it."""
import json

import darkfleet_core as core
import pytest

from darkfleet import bench
from darkfleet.agent.case_officer import CaseOfficer
from darkfleet.agent.citations import CitationChecker
from darkfleet.agent.env import HunterEnv
from darkfleet.agent.ledger import EvidenceLedger
from darkfleet.agent.run import autopilot
from darkfleet.episode import make_scenario, play
from darkfleet.policies import HunterBot, HunterConfig, RunnerPolicy


def scen(seed, n_bg=20):
    return make_scenario(seed, n_bg, brief="profile", transfer=True)


def test_runner_kinds_under_v3():
    outcomes = {}
    for kind in ("visible", "dark_run", "spoof", "honest"):
        w = core.World(json.dumps(scen(2)))
        pol = RunnerPolicy(kind=kind, seed=2)
        while not w.ended:
            for a in pol.act(json.loads(w.observe("runner"))):
                w.submit_action(json.dumps(a))
            w.step(1)
        outcomes[kind] = json.loads(w.outcome())
    assert {k: o["outcome"] for k, o in outcomes.items()} == {
        "visible": "runner_delivered", "dark_run": "runner_delivered", "spoof": "runner_delivered", "honest": "delivered_without_transfer"}
    assert outcomes["honest"]["runner_score"] == 20.0 and outcomes["visible"]["runner_score"] == 100.0


def test_v2_scenarios_are_untouched_by_the_transfer_option():
    a = make_scenario(7, 20, brief="profile")
    b = make_scenario(7, 20, brief="profile", transfer=True)
    assert "transfer" not in a["mission"] and "transfer" in b["mission"]
    assert a["entities"]["vessels"] == b["entities"]["vessels"][:len(a["entities"]["vessels"])]


def test_hunter_never_sees_the_zone():
    w = core.World(json.dumps(scen(3)))
    h = json.loads(w.observe("hunter"))
    zone = scen(3)["mission"]["transfer"]
    assert "mission" not in h
    ta = h["brief"]["transfer_area"]
    assert (ta["x_m"], ta["y_m"]) != (zone["x_m"], zone["y_m"]) and ta["radius_m"] > zone["radius_m"]
    assert str(round(zone["x_m"])) not in json.dumps(h)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_scripted_hunter_catches_a_visible_transfer_and_never_accuses_the_innocent(seed):
    caught = play(scen(seed), RunnerPolicy(kind="visible", seed=seed), HunterBot(HunterConfig(pf_seed=seed)))
    assert caught.outcome["outcome"] == "correct_supported" and caught.verified["code"] == "OK"
    innocent = play(scen(seed), RunnerPolicy(kind="honest", seed=seed), HunterBot(HunterConfig(pf_seed=seed)))
    assert innocent.outcome["outcome"] == "delivered_without_transfer"          # no accusation
    assert innocent.outcome["hunter_score"] == 0.0


def test_spoofed_transfer_needs_the_audit_pass():
    plain = play(scen(2), RunnerPolicy(kind="spoof", seed=2), HunterBot(HunterConfig(pf_seed=2)))
    audited = play(scen(2), RunnerPolicy(kind="spoof", seed=2), HunterBot(HunterConfig(pf_seed=2, audit_ais=True)))
    assert plain.outcome["outcome"] != "correct_supported"
    assert audited.outcome["outcome"] == "correct_supported"


def test_case_officer_catches_a_visible_transfer_with_verified_findings():
    env = HunterEnv(2, "visible", transfer=True)
    officer = CaseOfficer(env.host)
    res = autopilot(env, officer)
    assert res["outcome"]["outcome"] == "correct_supported" and res["verification"]["code"] == "OK"


def test_stationary_assertion():
    ledger = EvidenceLedger(300.0)

    def obs(tick, contacts):
        return {"tick": tick, "contacts": contacts}

    sar = lambda cid, x, y, age: {"contact_id": cid, "source": "sar", "x_m": x, "y_m": y, "sigma_m": 150.0, "size_class": "large", "age_ticks": age}
    ledger.ingest(obs(10, [sar("c-a", 1000.0, 1000.0, 0)]))
    ledger.ingest(obs(17, [sar("c-b", 1300.0, 900.0, 0), sar("c-far", 40000.0, 0.0, 0)]))
    chk = CitationChecker(ledger)

    def holds(ids, ticks=6, move=1000.0):
        ok, _ = chk._check_assertion({"type": "stationary", "contacts": ids, "max_move_m": move, "min_ticks_apart": ticks}, set(ids))
        return ok

    assert holds(["c-a", "c-b"])
    assert not holds(["c-a", "c-b"], ticks=9)           # only 7 ticks apart
    assert not holds(["c-a", "c-far"])                  # far apart
    assert not holds(["c-a"])                           # a single sighting says nothing
    ok, why = chk._check_assertion({"type": "stationary", "contacts": ["c-a", "c-b"], "max_move_m": 1000.0, "min_ticks_apart": 6}, {"c-a"})
    assert not ok and "not cited" in why


def test_bench_quick_run_under_rules_v3():
    jobs = bench.build_jobs({t: [1] for t in bench.tiers_for(True)}, ["pf_audit"], transfer=True)
    rows = [bench.run_episode(j) for j in jobs]
    assert {r["tier"] for r in rows} == set(bench.V3_TIERS)
    assert all(r["replay_verified"] for r in rows)
    honest = next(r for r in rows if r["tier"] == "honest")
    assert honest["success"] and not honest["accused"]
