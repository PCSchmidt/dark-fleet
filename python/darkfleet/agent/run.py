"""Drive an episode with the Case Officer as the Hunter (agent-as-player, auto-approving).

Human-in-the-loop is the default in the game; this autopilot exists for evaluation and for the
replay-determinism check (spike S16): the recorded inputs, not the agent, are authoritative.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

from .case_officer import CaseOfficer
from .env import HunterEnv


def autopilot(env: HunterEnv, officer: CaseOfficer | None = None, max_ticks: int = 144) -> dict:
    officer = officer or CaseOfficer(env.host)
    approved = 0
    while not env.ended and env.tick < max_ticks:
        report = officer.recommend()
        for p in report["proposals"]:
            if p["status"] == "pending" and not env.ended:
                env.host.approve(p["id"])
                approved += 1
        env.step(1)
    return {"outcome": env.outcome(), "approved": approved, "tool_calls": env.host.trace.totals()["tool_calls"],
            "verification": env.verify()}


def main() -> None:
    """Spike S16 (scaled): agent-in-the-loop runs, each replay verified by re-simulation from inputs only.

        python -m darkfleet.agent.run --n 150 --out docs/spike-results/S16.json
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--first-seed", type=int, default=3000)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    kinds = ["dark_run", "honest", "spoof"]
    outcomes: dict[str, Counter] = {k: Counter() for k in kinds}
    mismatches, ok, calls = [], 0, 0
    for i in range(args.n):
        kind, seed = kinds[i % 3], args.first_seed + i
        r = autopilot(HunterEnv(seed, kind))
        outcomes[kind][r["outcome"]["outcome"]] += 1
        calls += r["tool_calls"]
        if r["verification"]["code"] == "OK":
            ok += 1
        else:
            mismatches.append({"seed": seed, "kind": kind, "verification": r["verification"]})
    res = {"runs": args.n, "verified_ok": ok, "mismatches": mismatches, "tool_calls_per_run": calls / args.n,
           "outcomes_by_runner": {k: dict(v) for k, v in outcomes.items()},
           "note": "Replays are re-simulated from recorded inputs only; the agent trace is not used. Scaled down from the S16 target of 1,000 runs."}
    print(json.dumps(res, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
