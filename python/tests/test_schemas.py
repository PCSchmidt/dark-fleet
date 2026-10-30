"""Schema conformance: examples validate, bad documents are rejected."""
import copy

import jsonschema
import pytest


def validator(schema):
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def test_schemas_are_valid_json_schema(schemas):
    for schema in schemas.values():
        jsonschema.Draft202012Validator.check_schema(schema)


def test_example_scenario_validates(schemas, example_scenario):
    validator(schemas["scenario"]).validate(example_scenario)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.__setitem__("schema_version", "2.0.0"),
        lambda s: s["sensors"]["ais"].__setitem__("sigma_m", 0),
        lambda s: s["entities"]["vessels"][0].__setitem__("mmsi", "12"),
        lambda s: s["entities"]["vessels"][0].__setitem__("ais_mode", "teleport"),
        lambda s: s["entities"]["vessels"][0].__setitem__("course_deg", 360),
        lambda s: s.__setitem__("unexpected", 1),
        lambda s: s["entities"].__setitem__("vessels", []),
    ],
)
def test_bad_scenarios_rejected(schemas, example_scenario, mutate):
    bad = copy.deepcopy(example_scenario)
    mutate(bad)
    with pytest.raises(jsonschema.ValidationError):
        validator(schemas["scenario"]).validate(bad)


def test_action_schema(schemas):
    v = validator(schemas["action"])
    ok = {"schema_version": "1.0.0", "role": "runner", "tick": 0, "action_type": "set_ais_mode",
          "params": {"mode": "dark"}}
    v.validate(ok)
    for bad_params in ({}, {"mode": "bogus"}, {"mode": "dark", "extra": 1}):
        bad = dict(ok, params=bad_params)
        with pytest.raises(jsonschema.ValidationError):
            v.validate(bad)
    with pytest.raises(jsonschema.ValidationError):
        v.validate(dict(ok, action_type="teleport"))
