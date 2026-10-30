"""D2 bot evaluation under the profile briefing (no MMSI given to the Hunter).

For each Runner policy and each Hunter configuration (audit off / on): outcome counts, lock
accuracy (did the bot infer the true target from the last-known area?), audit verdicts and mean
scores. Every episode is replay-verified.

    python -m darkfleet.evaluate_d2 --seeds dev --out docs/results/d2_dev.json
    python -m darkfleet.evaluate_d2 --seeds heldout --out docs/results/d2_heldout.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

from .episode import make_scenario, play
from .policies import HunterBot, HunterConfig, RunnerPolicy

DEV = list(range(1, 21))
HELDOUT = list(range(1000, 1060))
TRUE_MMSI = "999000001"


def cell(seeds: list[int], kind: str, audit: bool) -> dict:
    outcomes: Counter = Counter()
    audits: Counter = Counter()
    locked = 0
    verified = 0
    scores = []
    for seed in seeds:
        bot = HunterBot(HunterConfig(pf_seed=seed, audit_ais=audit))
        r = play(make_scenario(seed, brief="profile"), RunnerPolicy(kind, seed=seed), bot)
        outcomes[r.outcome["outcome"]] += 1
        audits[bot.audit_result or "not_run"] += 1
        locked += bot.locked_mmsi == TRUE_MMSI
        verified += r.verified["code"] == "OK"
        scores.append(r.outcome["hunter_score"])
    n = len(seeds)
    return {"runner": kind, "audit": audit, "n": n, "outcomes": dict(outcomes), "audit_results": dict(audits),
            "lock_correct": locked, "replays_verified": verified, "mean_hunter_score": round(sum(scores) / n, 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", choices=["dev", "heldout"], default="dev")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    seeds = DEV if args.seeds == "dev" else HELDOUT
    rows = []
    for kind in ("dark_run", "honest", "spoof"):
        for audit in (False, True):
            row = cell(seeds, kind, audit)
            rows.append(row)
            print(json.dumps(row), flush=True)
    out = {"seeds": args.seeds, "seed_list": [seeds[0], seeds[-1]], "brief": "profile", "rows": rows,
           "all_replays_verified": all(r["replays_verified"] == r["n"] for r in rows)}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
