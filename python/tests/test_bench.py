"""DarkFleet-Bench: statistics, protocol invariants, schema and reproducibility."""
import json
import math
import os

import jsonschema
import numpy as np
import pytest

from darkfleet import bench

SMALL = {t: [s[0]] for t, s in bench.SEEDS["dev"].items()}
AGENTS = ["random", "dr_rule", "case_officer"]


def test_wilson_interval_behaves():
    p, lo, hi = bench.wilson(0, 10)
    assert p == 0 and lo == 0 and 0.2 < hi < 0.35
    p, lo, hi = bench.wilson(50, 100)
    assert p == 0.5 and lo < 0.5 < hi and hi - lo < 0.21
    assert math.isnan(bench.wilson(0, 0)[0])


def test_bootstrap_mean_interval_contains_the_mean_and_shrinks_with_n():
    rng = np.random.default_rng(0)
    small, big = list(rng.normal(10, 5, 20)), list(rng.normal(10, 5, 400))
    m1, lo1, hi1 = bench.boot_mean(small)
    m2, lo2, hi2 = bench.boot_mean(big)
    assert lo1 <= m1 <= hi1 and lo2 <= m2 <= hi2 and (hi2 - lo2) < (hi1 - lo1)
    assert bench.boot_mean([3.0] * 10)[1:] == (3.0, 3.0)


def test_seed_sets_are_disjoint_and_fixed():
    dev = {s for v in bench.SEEDS["dev"].values() for s in v}
    held = [s for v in bench.SEEDS["heldout"].values() for s in v]
    assert not (dev & set(held)) and len(held) == len(set(held)) == 150
    assert set(bench.SEEDS["heldout"]) == set(bench.TIERS)
    # development seeds used in D1-D3 tuning (1-40) never appear in the held-out set
    assert not set(range(1, 41)) & set(held)


@pytest.fixture(scope="module")
def small_run():
    rows = bench.run(SMALL, AGENTS, workers=1)
    return rows


def test_every_agent_plays_every_tier_and_every_replay_verifies(small_run):
    rows = small_run
    for a in AGENTS:
        reps = bench.REPEATS.get(a, 1)
        assert len([r for r in rows if r["agent"] == a]) == len(bench.TIERS) * reps
    assert all(r["replay_verified"] for r in rows)
    assert all(r["tool_calls"] is not None for r in rows if r["agent"] == "case_officer")
    assert all(r["tool_calls"] is None for r in rows if r["agent"] != "case_officer")


def test_success_definition_per_tier(small_run):
    for r in small_run:
        if r["tier"] == "honest":
            assert r["success"] == (not r["accused"])
        else:
            assert r["success"] == (r["outcome"] == "correct_supported")
    # nobody can win by accusing the innocent
    assert not any(r["success"] and r["false_accusation"] for r in small_run)


def test_random_baseline_is_bad(small_run):
    rnd = [r for r in small_run if r["agent"] == "random" and r["tier"] != "honest"]
    assert sum(r["success"] for r in rnd) / len(rnd) < 0.2


def test_result_document_matches_the_schema_and_is_reproducible(small_run, tmp_path):
    rows2 = bench.run(SMALL, AGENTS, workers=1)
    assert bench.stable_digest(small_run) == bench.stable_digest(rows2)    # S19: same config, same results
    res = {"schema_version": bench.SCHEMA_VERSION, "bench_version": bench.BENCH_VERSION, "rules_version": "df-rules-2",
           "seed_set": "test", "seeds": SMALL, "tiers": bench.TIERS, "agents": {a: bench.AGENTS[a] for a in AGENTS},
           "reference_agent": bench.REFERENCE, "not_measured": ["calibration"], "episodes": small_run,
           "digest": bench.stable_digest(small_run), **bench.summarize(small_run, AGENTS), "run": {"workers": 1, "wall_s": 0.0}}
    schema = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..", "schemas", "bench-result.v1.json"), encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(json.loads(json.dumps(res)))
    md = bench.leaderboard_md({**res, "paired_vs_reference": {}})
    assert "case_officer" in md and "Not measured" in md


def test_paired_comparison_of_an_agent_with_itself_is_zero(small_run):
    p = bench.paired_vs(small_run, "case_officer", "case_officer")
    assert p["hunter_score"]["mean_diff"] == 0 and not p["hunter_score"]["ci_excludes_zero"]


def test_unknown_agent_is_rejected():
    with pytest.raises(SystemExit):
        import sys
        old = sys.argv
        sys.argv = ["bench", "--agents", "nonexistent", "--quick", "--workers", "1", "--out", os.devnull]
        try:
            bench.main()
        finally:
            sys.argv = old


def test_published_result_file_is_valid_and_consistent():
    root = os.path.join(os.path.dirname(__file__), "..", "..")
    res = json.load(open(os.path.join(root, "docs", "bench", "bench-result.v1.json"), encoding="utf-8"))
    schema = json.load(open(os.path.join(root, "schemas", "bench-result.v1.json"), encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(res)
    assert res["rules_version"] == "df-rules-2" and res["seed_set"] == "heldout"
    assert all(e["replay_verified"] for e in res["episodes"])
    assert res["digest"] == bench.stable_digest(res["episodes"])         # the file has not been edited by hand
    held = {s for v in res["seeds"].values() for s in v}
    assert not held & set(range(1, 41))                                   # held-out seeds never used for tuning
    s19 = json.load(open(os.path.join(root, "docs", "spike-results", "S19.json"), encoding="utf-8"))
    assert s19["identical"] and s19["digests"][0] == res["digest"]
