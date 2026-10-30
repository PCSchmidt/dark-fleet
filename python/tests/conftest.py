import glob
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _find_module_dir():
    explicit = os.environ.get("DF_PYMODULE_DIR")
    if explicit:
        return explicit
    for pattern in ("build/python/darkfleet_core*", "build/**/darkfleet_core*.pyd", "build/**/darkfleet_core*.so"):
        hits = glob.glob(os.path.join(ROOT, pattern), recursive=True)
        if hits:
            return os.path.dirname(hits[0])
    return None


_dir = _find_module_dir()
if _dir:
    sys.path.insert(0, _dir)


@pytest.fixture(scope="session")
def schemas():
    out = {}
    for name in ("scenario", "observation", "action"):
        with open(os.path.join(ROOT, "schemas", f"{name}.v1.json"), encoding="utf8") as f:
            out[name] = json.load(f)
    return out


@pytest.fixture(scope="session")
def example_scenario():
    with open(os.path.join(ROOT, "schemas", "examples", "scenario_basic.json"), encoding="utf8") as f:
        return json.load(f)
