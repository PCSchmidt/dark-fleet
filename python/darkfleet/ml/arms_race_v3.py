"""The rules-v3 arms race in one table: each learned-Runner generation against each Hunter generation, same held-out seeds.

    python -m darkfleet.ml.arms_race_v3 --out docs/results/arms_race_v3.json

Runner generations (each trained by runner_v3.py against the Hunters that existed when it was trained):
  A  models/runner_v3            pool {pf_greedy, pf_audit}                       found: spoof after the one-off audit pass
  B  models/runner_v3_patched    adds pf_audit_jump                               found: ~30 ticks dark before the zone
  C  models/runner_v3_sweep      adds pf_audit_sweep                              (pool average: beats only the older Hunters)
  D  models/runner_v3_vs_sweep   trained against pf_audit_sweep alone             found: a ~4 km spoof offset
Hunters: the scripted ones, plus ``tuned`` (the best-response Hunter searched against generation D: audit, jump re-audit,
stationary gate 2.5 km), which is the only one that is not blind to generation D.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from .common import write_json
from .runner_v3 import NAMES, evaluate, summarize_v3

RUNNERS = {"A spoof-after-audit": "models/runner_v3", "B long dark": "models/runner_v3_patched",
           "C pool average": "models/runner_v3_sweep", "D small spoof": "models/runner_v3_vs_sweep"}
TUNED = {"kind": "bot", "tracker": "pf", "silent_ticks_before_tasking": 3, "min_ticks_between_tasks": 3, "audit_ais": True, "audit_tick": 8,
         "lock_radius_m": 15000.0, "stationary_gate_m": 2500.0, "explain_gate_m": 800.0, "audit_jump": True, "sweep_area": False}
HUNTERS = ["pf_greedy", "pf_audit", "pf_audit_jump", "pf_audit_sweep", "case_officer_sweep", "tuned"]
BASELINES = {"no transfer (always 20)": None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/results/arms_race_v3.json")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--n", type=int, default=60)
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[3]
    seeds = [50000 + i for i in range(args.n)]
    out: dict = {"seeds": [seeds[0], seeds[-1]], "n": args.n, "hunters": HUNTERS, "tuned_spec": TUNED, "runners": {}}
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for name, d in RUNNERS.items():
            pol = json.loads((root / d / "runner_policy.json").read_text(encoding="utf-8"))
            theta = [pol["params"][n] for n in NAMES]
            rows = evaluate(theta, HUNTERS, seeds, 20, ex, {"tuned": TUNED})
            s = summarize_v3(rows)
            out["runners"][name] = {"params": pol["params"], "results": {h: {"runner_score": v["runner_value"], "outcomes": v["outcomes"],
                                                                         "verified": v["verified"], "n": v["n"]} for h, v in s.items()}}
            print(name, {h: round(v["runner_value"]["mean"], 1) for h, v in s.items()}, flush=True)
    write_json(Path(args.out), out)


if __name__ == "__main__":
    main()
