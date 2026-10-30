"""The Python surface: engine output conforms to the published schemas, and the
role filter holds when driven from Python (Spike S1 on the live interface)."""
import json
import random

import jsonschema
import pytest

darkfleet_core = pytest.importorskip("darkfleet_core", reason="build the darkfleet_core module first")


def world(scenario):
    return darkfleet_core.World(json.dumps(scenario))


def test_observations_conform_to_schema(schemas, example_scenario):
    ov = jsonschema.Draft202012Validator(schemas["observation"])
    w = world(example_scenario)
    for _ in range(50):
        w.step(1)
        for role in ("hunter", "runner"):
            ov.validate(json.loads(w.observe(role)))


def test_typed_errors_from_python(example_scenario):
    w = world(example_scenario)
    act = {"schema_version": "1.0.0", "role": "hunter", "tick": 0, "action_type": "set_ais_mode",
           "params": {"mode": "dark"}}
    assert w.submit_action(json.dumps(act)) == "E_ROLE_FORBIDDEN"
    assert w.submit_action("not json") == "E_SCHEMA"
    act["role"] = "runner"
    assert w.submit_action(json.dumps(act)) == "OK"


def test_bad_scenario_raises_typed_error(example_scenario):
    bad = dict(example_scenario, schema_version="2.0.0")
    with pytest.raises(ValueError, match="E_VERSION"):
        world(bad)
    with pytest.raises(ValueError, match="E_SCHEMA"):
        darkfleet_core.World("{not json")


def test_determinism_from_python(example_scenario):
    a, b = world(example_scenario), world(example_scenario)
    a.step(120)
    b.step(120)
    assert a.state_hash() == b.state_hash()
    assert a.observe("hunter") == b.observe("hunter")


def test_python_fuzz_never_sees_truth_strings(schemas, example_scenario):
    """Drive random actions through the Python API; the Hunter document must never
    contain any truth id or secret note."""
    secrets = []
    for v in example_scenario["entities"]["vessels"]:
        secrets.append(v["id"])
        if "secret_note" in v:
            secrets.append(v["secret_note"])
    rng = random.Random(1234)
    w = world(example_scenario)
    for _ in range(100):
        act = {"schema_version": "1.0.0", "role": rng.choice(["hunter", "runner"]), "tick": w.tick,
               "action_type": rng.choice(["noop", "set_ais_mode", "set_course_speed", "task_sensor"]),
               "params": rng.choice([{"mode": "dark"}, {"mode": "on"}, {"course_deg": 10.0, "speed_mps": 3.0}, {}])}
        w.submit_action(json.dumps(act))
        w.step(1)
        text = w.observe("hunter")
        for s in secrets:
            assert s not in text
        assert '"own"' not in text
