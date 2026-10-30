"""Spike S14: does the citation checker catch unsupported claims?

Builds a labelled set of agent-style findings from real episode ledgers and scores the checker.
Labels are fixed by construction (a good claim is built from ledger facts; a bad one is a
deliberate corruption), so this measures whether the *implementation* matches the rule table. It does
not measure how an LLM's own mistakes are distributed; that needs real agent outputs.

    python -m darkfleet.agent.s14 --out docs/spike-results/S14.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict

from .citations import CitationChecker, V_MAX_MPS
from .env import HunterEnv
from .ledger import EvidenceLedger
from .run import autopilot

GOOD_KINDS = ["position_ok", "identity_ok", "silent_ok", "size_ok", "chain_ok", "colocated_ok"]
BAD_KINDS = ["position_bad", "identity_bad", "silent_bad", "chain_bad", "fabricated_id", "out_of_role_id",
             "uncited", "assertion_not_cited"]


def _fake_id(rng: random.Random) -> str:
    return "c-%08x" % rng.getrandbits(32)


def collect(seeds: list[int]) -> list[tuple[EvidenceLedger, list[str]]]:
    """(hunter ledger, contact ids the *Runner* saw) for each seed after an agent-played episode."""
    out = []
    for s in seeds:
        env = HunterEnv(s, "dark_run")
        runner_ids: set[str] = set()
        # play with the Case Officer, harvesting the Runner's view of contact ids as we go
        from .case_officer import CaseOfficer
        officer = CaseOfficer(env.host)
        while not env.ended and env.tick < 60:
            rep = officer.recommend()
            for p in rep["proposals"]:
                if p["status"] == "pending" and not env.ended:
                    env.host.approve(p["id"])
            runner_ids |= {c["contact_id"] for c in env.runner_obs()["contacts"]}
            env.step(1)
        out.append((env.host.ledger, sorted(runner_ids)))
    return out


def _gen(kind: str, led: EvidenceLedger, runner_ids: list[str], rng: random.Random):
    ais = [r for r in led.contacts.values() if r.source == "ais"]
    sar = [r for r in led.contacts.values() if r.source == "sar"]
    allr = list(led.contacts.values())
    if kind in ("position_ok", "position_bad"):
        r = rng.choice(allr)
        x, y, _ = r.last_position()
        dx = 0.0 if kind == "position_ok" else rng.choice([-1, 1]) * rng.uniform(6000, 15000)
        return {"text": "position", "cites": [r.contact_id],
                "assertions": [{"type": "position", "contact": r.contact_id, "x_m": x + dx, "y_m": y, "tol_m": 500.0}]}
    if kind in ("identity_ok", "identity_bad"):
        r = rng.choice(ais)
        mmsi = r.claimed_mmsi
        if kind == "identity_bad":
            mmsi = rng.choice([o.claimed_mmsi for o in ais if o.claimed_mmsi != r.claimed_mmsi] or ["999999999"])
        return {"text": "identity", "cites": [r.contact_id], "assertions": [{"type": "identity", "contact": r.contact_id, "mmsi": mmsi}]}
    if kind in ("silent_ok", "silent_bad"):
        cand = [r for r in ais if led.tick - r.last_tick() >= 1]
        if not cand:
            return None
        r = rng.choice(cand)
        n = led.tick - r.last_tick() + (0 if kind == "silent_ok" else rng.randint(3, 9))
        return {"text": "silent", "cites": [r.contact_id],
                "assertions": [{"type": "silent", "contact": r.contact_id, "at_least_ticks": n, "as_of_tick": led.tick}]}
    if kind == "size_ok":
        if not sar:
            return None
        r = rng.choice(sar)
        return {"text": "size", "cites": [r.contact_id], "assertions": [{"type": "size_class", "contact": r.contact_id, "size_class": r.size_class}]}
    if kind in ("chain_ok", "chain_bad"):
        pairs = []
        for i, a in enumerate(sar):
            for b in sar[i + 1:]:
                (ta, (xa, ya, sa)), (tb, (xb, yb, sb)) = (a.last_tick(), a.last_position()), (b.last_tick(), b.last_position())
                if ta == tb:
                    feasible = False
                else:
                    t1, t2 = sorted([(ta, xa, ya, sa), (tb, xb, yb, sb)])
                    feasible = math.hypot(t2[1] - t1[1], t2[2] - t1[2]) <= V_MAX_MPS * (t2[0] - t1[0]) * led.tick_s + 3 * (t1[3] + t2[3])
                pairs.append((a, b, feasible))
        want = kind == "chain_ok"
        cand = [p for p in pairs if p[2] == want]
        if not cand:
            return None
        a, b, _ = rng.choice(cand)
        return {"text": "chain", "cites": [a.contact_id, b.contact_id], "assertions": [{"type": "chain", "contacts": [a.contact_id, b.contact_id]}]}
    if kind == "colocated_ok":
        cand = []
        for s in sar:
            ts, (xs, ys, ss) = s.last_tick(), s.last_position()
            for a in ais:
                for t, (x, y, _) in a.history.items():
                    if abs(t - ts) <= 1 and math.hypot(x - xs, y - ys) <= 2000.0:
                        cand.append((a, s))
        if not cand:
            return None
        a, s = rng.choice(cand)
        return {"text": "colocated", "cites": [a.contact_id, s.contact_id],
                "assertions": [{"type": "colocated", "ais": a.contact_id, "sar": s.contact_id, "tol_m": 3000.0}]}
    if kind == "fabricated_id":
        fid = _fake_id(rng)
        return {"text": "fabricated", "cites": [fid], "assertions": [{"type": "identity", "contact": fid, "mmsi": "999000001"}]}
    if kind == "out_of_role_id":
        cand = [i for i in runner_ids if led.get(i) is None]
        if not cand:
            return None
        rid = rng.choice(cand)
        return {"text": "out of role", "cites": [rid], "assertions": [{"type": "position", "contact": rid, "x_m": 0.0, "y_m": 0.0, "tol_m": 500.0}]}
    if kind == "uncited":
        r = rng.choice(ais)
        return {"text": "uncited", "cites": [], "assertions": [{"type": "identity", "contact": r.contact_id, "mmsi": r.claimed_mmsi}]}
    if kind == "assertion_not_cited":
        a, b = rng.sample(ais, 2)
        return {"text": "mismatched cite", "cites": [a.contact_id], "assertions": [{"type": "identity", "contact": b.contact_id, "mmsi": b.claimed_mmsi}]}
    raise ValueError(kind)


def build_labelled_set(seeds: list[int], n: int = 500, rng_seed: int = 14) -> tuple[list[dict], list[CitationChecker]]:
    rng = random.Random(rng_seed)
    data = collect(seeds)
    checkers = [CitationChecker(led) for led, _ in data]
    kinds = GOOD_KINDS + BAD_KINDS
    items: list[dict] = []
    guard = 0
    while len(items) < n and guard < n * 40:
        guard += 1
        kind = kinds[len(items) % len(kinds)]
        i = rng.randrange(len(data))
        f = _gen(kind, data[i][0], data[i][1], rng)
        if f is None:
            continue
        items.append({"kind": kind, "good": kind in GOOD_KINDS, "ledger": i, "finding": f})
    return items, checkers


def evaluate(items: list[dict], checkers: list[CitationChecker]) -> dict:
    tp = fp = fn = tn = 0
    per = defaultdict(Counter)
    for it in items:
        status = checkers[it["ledger"]].check_finding(it["finding"])["status"]
        flagged = status != "verified"
        per[it["kind"]]["n"] += 1
        per[it["kind"]]["flagged"] += flagged
        if it["good"]:
            fp += flagged
            tn += not flagged
        else:
            tp += flagged
            fn += not flagged
    bad, good = tp + fn, tn + fp
    return {
        "n": len(items), "n_bad": bad, "n_good": good,
        "recall_bad": tp / bad if bad else None,
        "precision_flagged": tp / (tp + fp) if tp + fp else None,
        "false_block_rate_on_good": fp / good if good else None,
        "per_kind": {k: {"n": v["n"], "flagged": v["flagged"], "rate": v["flagged"] / v["n"]} for k, v in sorted(per.items())},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    items, checkers = build_labelled_set(list(range(2000, 2020)))
    res = evaluate(items, checkers)
    res["episodes"] = 20
    res["seeds"] = [2000, 2019]
    print(json.dumps(res, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
