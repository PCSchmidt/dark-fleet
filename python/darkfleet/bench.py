"""DarkFleet-Bench v1: seeded scenarios for Hunter agents, with baselines, ablations and CIs.

    python -m darkfleet.bench --seeds heldout --out docs/bench          # the published run
    python -m darkfleet.bench --quick --workers 1 --out /tmp/bench      # small smoke run

Protocol
  * Tiers (scenario families): standard, honest, spoof, decoy_rich, injection_laced. Each tier has a
    fixed seed list; ``dev`` seeds (1-20) are for developing agents, ``heldout`` seeds (5000-5029)
    are for the published run and were not used to tune anything.
  * Every agent plays every (tier, seed, repeat) against the same scripted Runner. Every replay is
    re-verified by re-simulation.
  * An episode is a *success* when the agent does the right thing for the tier: in the honest tier
    that is not accusing anyone; in every other tier it is a correct, supported identification.
  * Rates carry Wilson 95% intervals; means carry percentile-bootstrap 95% intervals over episodes;
    differences against the reference agent are paired by (tier, seed) and bootstrapped.

Not measured, and said so in the output: calibration (the agents emit no confidence), cost in dollars
(no priced model is used), and weather/cloud tiers (that mechanic does not exist yet).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

BENCH_VERSION = "darkfleet-bench-1"
SCHEMA_VERSION = "bench-result.v1"

TIERS = {
    "standard":        {"runner": "dark_run", "n_background": 20, "inject": False, "note": "dark-running tanker, normal traffic"},
    "honest":          {"runner": "honest",   "n_background": 20, "inject": False, "note": "nothing to find: accusing anyone is wrong"},
    "spoof":           {"runner": "spoof",    "n_background": 20, "inject": False, "note": "broadcasts a false position"},
    "decoy_rich":      {"runner": "dark_run", "n_background": 45, "inject": False, "note": "more than twice the background traffic"},
    "injection_laced": {"runner": "dark_run", "n_background": 20, "inject": True,  "note": "every background vessel name is an injection payload"},
}
SEEDS = {
    "dev":     {t: list(range(1, 21)) for t in TIERS},
    "heldout": {t: list(range(5000 + 100 * i, 5000 + 100 * i + 30)) for i, t in enumerate(TIERS)},
}
EXTRA_TIERS = {"standard_injected": {**TIERS["standard"], "inject": True}}   # paired check only; not a bench tier
ACCUSED = {"correct_supported", "insufficient_support", "wrong_identity", "unfounded_claim"}
FALSE_ACCUSATION = {"wrong_identity", "unfounded_claim"}

AGENTS = {
    "random":             "Random SAR taskings over a generous region; claims two random visible contacts at a random time (3 repeats).",
    "dr_rule":            "Dead-reckoning tracker, tasks SAR at the estimate when the target is silent, accuses on a SAR chain after 6 silent ticks.",
    "pf_greedy":          "Particle-filter tracker with the same tasking and accusation rules (the reference).",
    "pf_audit":           "pf_greedy plus one early SAR audit of the target's AIS position (detects spoofing).",
    "case_officer":       "The D3 Case Officer, scripted specialists, a human-equivalent auto-approver, no model.",
    "case_officer_noaudit": "Ablation: Case Officer without the audit pass.",
    "case_officer_nodark":  "Ablation: Case Officer without the dark-evidence rule (may accuse after any silence).",
}
REFERENCE = "pf_greedy"
REPEATS = {"random": 3}


# ---- agents ---------------------------------------------------------------------------------------
class RandomHunter:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.done = False

    def act(self, obs: dict) -> list[dict]:
        from .policies import action
        t, out = obs["tick"], []
        if obs["status"]["state"] != "running" or self.done:
            return out
        res, cs = obs["resources"], obs["contacts"]
        if res["sar_tasks_remaining"] > 0 and not res["pending_taskings"] and self.rng.random() < 0.08:
            out.append(action("hunter", t, "task_sensor", {"sensor": "sar", "x_m": self.rng.uniform(-150000, 150000),
                                                           "y_m": self.rng.uniform(-80000, 80000)}))
        if t >= 20 and cs and self.rng.random() < 0.02:
            pick = self.rng.choice(cs)
            ev = [pick["contact_id"]] + [c["contact_id"] for c in self.rng.sample(cs, min(2, len(cs)))]
            self.done = True
            out.append(action("hunter", t, "file_claim", {"contact_id": pick["contact_id"], "evidence": ev}))
        return out


def _payload(seed: int) -> str:
    from .agent.injection import payloads
    ps = payloads()
    return ps[seed % len(ps)]


def _rename(scenario: dict, payload: str) -> None:
    for v in scenario["entities"]["vessels"]:
        if not v["is_runner"]:
            v["name"] = payload


@dataclass
class Episode:
    agent: str
    tier: str
    seed: int
    rep: int


def run_episode(ep: Episode) -> dict:
    """Play one episode. Module-level so it can run in worker processes."""
    import json as _json

    import darkfleet_core as core

    from .episode import make_scenario, play
    from .policies import HunterBot, HunterConfig, RunnerPolicy

    tier = TIERS.get(ep.tier) or EXTRA_TIERS[ep.tier]
    t0 = time.perf_counter()
    tool_calls = tool_errors = None
    if ep.agent.startswith("case_officer"):
        from .agent.case_officer import CaseOfficer
        from .agent.env import HunterEnv
        from .agent.run import autopilot
        rename = (lambda i, p=_payload(ep.seed): p) if tier["inject"] else None
        env = HunterEnv(ep.seed, tier["runner"], n_background=tier["n_background"], rename_background=rename)
        kwargs = {"case_officer_noaudit": {"audit": False}, "case_officer_nodark": {"dark_evidence_ticks": 0}}.get(ep.agent, {})
        res = autopilot(env, CaseOfficer(env.host, **kwargs))
        tot = env.host.trace.totals()
        tool_calls, tool_errors = tot["tool_calls"], tot["tool_errors"]
        outcome, verified = res["outcome"], res["verification"]["code"] == "OK"
        replay = _json.loads(env.world.replay())
    else:
        scenario = make_scenario(ep.seed, tier["n_background"], brief="profile")
        if tier["inject"]:
            _rename(scenario, _payload(ep.seed))
        seed_rep = ep.seed * 10 + ep.rep
        if ep.agent == "random":
            hunter = RandomHunter(seed_rep)
        else:
            cfg = {"dr_rule": dict(tracker="dr", require_dark_ticks=6),
                   "pf_greedy": dict(tracker="pf", require_dark_ticks=6),
                   "pf_audit": dict(tracker="pf", require_dark_ticks=6, audit_ais=True)}[ep.agent]
            hunter = HunterBot(HunterConfig(pf_seed=seed_rep, **cfg))
        r = play(scenario, RunnerPolicy(kind=tier["runner"], seed=ep.seed), hunter)
        outcome, verified, replay = r.outcome, r.verified["code"] == "OK", r.replay
    wall_ms = (time.perf_counter() - t0) * 1000.0
    oc = outcome["outcome"]
    sar_used = sum(1 for i in replay["inputs"] if "task_sensor" in _json.dumps(i) and '"hunter"' in _json.dumps(i))
    success = (oc not in ACCUSED) if ep.tier == "honest" else (oc == "correct_supported")
    return {"agent": ep.agent, "tier": ep.tier, "seed": ep.seed, "rep": ep.rep, "outcome": oc,
            "hunter_score": outcome["hunter_score"], "success": bool(success), "accused": oc in ACCUSED,
            "false_accusation": oc in FALSE_ACCUSATION, "end_tick": replay["n_ticks"], "sar_used": sar_used,
            "tool_calls": tool_calls, "tool_errors": tool_errors, "wall_ms": round(wall_ms, 1),
            "replay_verified": bool(verified), "final_state_hash": replay["final_state_hash"]}


# ---- statistics -----------------------------------------------------------------------------------
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def boot_mean(x: list[float], n_boot: int = 4000, seed: int = 7) -> tuple[float, float, float]:
    a = np.asarray(x, dtype=float)
    if a.size == 0:
        return (float("nan"),) * 3
    rng = np.random.default_rng(seed)
    means = a[rng.integers(0, a.size, (n_boot, a.size))].mean(axis=1)
    return float(a.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _rate(rows: list[dict], key: str) -> dict:
    k, n = sum(1 for r in rows if r[key]), len(rows)
    p, lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": p, "ci95": [lo, hi]}


def _mean(rows: list[dict], key: str) -> dict:
    m, lo, hi = boot_mean([r[key] for r in rows])
    return {"mean": m, "ci95": [lo, hi]}


def summarize_group(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["success"] and r["tier"] != "honest"]
    calls = sum(r["tool_calls"] or 0 for r in rows)
    errs = sum(r["tool_errors"] or 0 for r in rows)
    return {
        "episodes": len(rows),
        "success": _rate(rows, "success"),
        "false_accusation": _rate(rows, "false_accusation"),
        "accused": _rate(rows, "accused"),
        "hunter_score": _mean(rows, "hunter_score"),
        "time_to_identification_ticks": _mean(ok, "end_tick") if ok else None,
        "sar_taskings_used": _mean(rows, "sar_used"),
        "tool_call_validity": (1.0 - errs / calls) if calls else None,
        "wall_ms_per_episode": _mean(rows, "wall_ms"),
        "replays_verified": sum(1 for r in rows if r["replay_verified"]),
    }


def paired_vs(rows: list[dict], agent: str, ref: str) -> dict:
    def table(a):
        d: dict[tuple, list] = {}
        for r in rows:
            if r["agent"] == a:
                d.setdefault((r["tier"], r["seed"]), []).append(r)
        return d
    A, B = table(agent), table(ref)
    keys = sorted(set(A) & set(B))
    out = {}
    for label, key in (("hunter_score", "hunter_score"), ("success", "success")):
        diffs = [float(np.mean([x[key] for x in A[k]]) - np.mean([x[key] for x in B[k]])) for k in keys]
        m, lo, hi = boot_mean(diffs, seed=11)
        out[label] = {"mean_diff": m, "ci95": [lo, hi], "pairs": len(keys), "ci_excludes_zero": bool(lo > 0 or hi < 0)}
    return out


# ---- run ------------------------------------------------------------------------------------------
def build_jobs(seed_set: dict[str, list[int]], agents: list[str]) -> list[Episode]:
    return [Episode(a, t, s, rep) for a in agents for t in TIERS for s in seed_set[t] for rep in range(REPEATS.get(a, 1))]


def run(seed_set: dict[str, list[int]], agents: list[str], workers: int) -> list[dict]:
    jobs = build_jobs(seed_set, agents)
    if workers <= 1:
        rows = [run_episode(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            rows = list(ex.map(run_episode, jobs, chunksize=4))
    rows.sort(key=lambda r: (r["agent"], r["tier"], r["seed"], r["rep"]))
    return rows


def summarize(rows: list[dict], agents: list[str]) -> dict:
    summary = {}
    for a in agents:
        mine = [r for r in rows if r["agent"] == a]
        summary[a] = {"overall": summarize_group(mine),
                      "by_tier": {t: summarize_group([r for r in mine if r["tier"] == t]) for t in TIERS}}
    paired = {a: paired_vs(rows, a, REFERENCE) for a in agents if a != REFERENCE and REFERENCE in agents}
    ablations = {a: paired_vs(rows, a, "case_officer") for a in agents
                 if a.startswith("case_officer_") and "case_officer" in agents}
    return {"summary": summary, "paired_vs_reference": paired, "paired_ablations_vs_case_officer": ablations}


def leaderboard_md(res: dict) -> str:
    agents = list(res["summary"])
    order = sorted(agents, key=lambda a: -res["summary"][a]["overall"]["success"]["rate"])
    pct = lambda r: f"{100 * r['rate']:.0f}% [{100 * r['ci95'][0]:.0f}-{100 * r['ci95'][1]:.0f}]"
    lines = [f"# DarkFleet-Bench leaderboard ({res['bench_version']}, seeds: {res['seed_set']}, rules {res['rules_version']})", "",
             "Success = correct supported identification (honest tier: no accusation). Brackets are 95% intervals (Wilson for rates). "
             "Episodes per agent per tier: " + ", ".join(f"{t} {len(res['seeds'][t])}" for t in TIERS) + ".", "",
             "| Agent | Overall success | " + " | ".join(TIERS) + " | False accusations | Mean score [95% CI] | Ticks to ID | SAR used | ms/episode |",
             "|---|---|" + "---|" * (len(TIERS) + 5)]
    for a in order:
        s = res["summary"][a]
        o = s["overall"]
        t2i = o["time_to_identification_ticks"]
        t2i_txt = "-" if not t2i else "%.0f" % t2i["mean"]
        lines.append(f"| {a} | {pct(o['success'])} | " + " | ".join(pct(s['by_tier'][t]['success']) for t in TIERS) +
                     f" | {pct(o['false_accusation'])} | {o['hunter_score']['mean']:.1f} [{o['hunter_score']['ci95'][0]:.1f}, {o['hunter_score']['ci95'][1]:.1f}]"
                     f" | {t2i_txt} | {o['sar_taskings_used']['mean']:.1f} | {o['wall_ms_per_episode']['mean']:.0f} |")
    if res["paired_vs_reference"]:
        lines += ["", f"## Paired difference vs `{REFERENCE}` (same tier and seed)", "",
                  "| Agent | Score diff [95% CI] | Success diff [95% CI] | Distinguishable from zero? |", "|---|---|---|---|"]
        for a in order:
            p = res["paired_vs_reference"].get(a)
            if p:
                sd, ss = p["hunter_score"], p["success"]
                lines.append(f"| {a} | {sd['mean_diff']:+.1f} [{sd['ci95'][0]:+.1f}, {sd['ci95'][1]:+.1f}] | {ss['mean_diff']:+.2f} [{ss['ci95'][0]:+.2f}, {ss['ci95'][1]:+.2f}] | "
                             f"{'score yes' if sd['ci_excludes_zero'] else 'score no'}, {'success yes' if ss['ci_excludes_zero'] else 'success no'} |")
    if res.get("paired_ablations_vs_case_officer"):
        lines += ["", "## Ablations: paired difference vs `case_officer`", "",
                  "| Ablation | Score diff [95% CI] | Success diff [95% CI] |", "|---|---|---|"]
        for a, p in res["paired_ablations_vs_case_officer"].items():
            sd, ss = p["hunter_score"], p["success"]
            lines.append(f"| {a} | {sd['mean_diff']:+.1f} [{sd['ci95'][0]:+.1f}, {sd['ci95'][1]:+.1f}] | {ss['mean_diff']:+.2f} [{ss['ci95'][0]:+.2f}, {ss['ci95'][1]:+.2f}] |")
    lines += ["", "Not measured: calibration (agents emit no confidence), dollar cost (no priced model), weather/cloud tiers (mechanic not built)."]
    return "\n".join(lines) + "\n"


def injection_paired(seeds: list[int], agents: tuple[str, ...] = ("pf_greedy", "case_officer"), workers: int = 1) -> dict:
    """Same standard-tier seeds, with and without injection payloads as vessel names.

    Names are display text only: no agent here reads them to decide anything, so outcomes should
    be identical. The check is exactly that, and that the final state hash is unchanged.
    """
    jobs = [Episode(a, t, s, 0) for a in agents for t in ("standard", "standard_injected") for s in seeds]
    if workers <= 1:
        rows = [run_episode(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            rows = list(ex.map(run_episode, jobs, chunksize=2))
    out = {}
    for a in agents:
        clean = {r["seed"]: r for r in rows if r["agent"] == a and r["tier"] == "standard"}
        inj = {r["seed"]: r for r in rows if r["agent"] == a and r["tier"] == "standard_injected"}
        out[a] = {"seeds": len(seeds),
                  "same_outcome": sum(clean[s]["outcome"] == inj[s]["outcome"] for s in seeds),
                  "same_final_state_hash": sum(clean[s]["final_state_hash"] == inj[s]["final_state_hash"] for s in seeds),
                  "success_clean": sum(clean[s]["success"] for s in seeds),
                  "success_injected": sum(inj[s]["success"] for s in seeds)}
    return out


def stable_digest(rows: list[dict]) -> str:
    """Digest of everything deterministic in the rows (excludes wall-clock timing)."""
    keep = [{k: v for k, v in r.items() if k != "wall_ms"} for r in rows]
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description="DarkFleet-Bench v1")
    ap.add_argument("--seeds", choices=["dev", "heldout"], default="heldout")
    ap.add_argument("--quick", action="store_true", help="2 seeds per tier (smoke test)")
    ap.add_argument("--agents", default=",".join(AGENTS))
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--out", default="docs/bench")
    ap.add_argument("--from-json", default="", help="recompute summary and leaderboard from a saved result (no episodes are run)")
    ap.add_argument("--injection-paired", action="store_true", help="also run the paired clean-vs-injected check on the standard seeds")
    args = ap.parse_args()
    if args.from_json:
        res = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        res.update(summarize(res["episodes"], list(res["agents"])))
        Path(args.from_json).write_text(json.dumps(res, indent=1), encoding="utf-8")
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "LEADERBOARD.md").write_text(leaderboard_md(res), encoding="utf-8")
        print(leaderboard_md(res))
        return
    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    unknown = [a for a in agents if a not in AGENTS]
    if unknown:
        raise SystemExit(f"unknown agent(s): {unknown}")
    seed_set = {t: (s[:2] if args.quick else s) for t, s in SEEDS[args.seeds].items()}
    t0 = time.perf_counter()
    rows = run(seed_set, agents, args.workers)
    res = {"schema_version": SCHEMA_VERSION, "bench_version": BENCH_VERSION, "rules_version": "df-rules-2",
           "seed_set": args.seeds + (" (quick)" if args.quick else ""), "seeds": seed_set,
           "tiers": TIERS, "agents": {a: AGENTS[a] for a in agents}, "reference_agent": REFERENCE,
           "not_measured": ["calibration", "dollar cost", "weather and cloud tiers"],
           "episodes": rows, "digest": stable_digest(rows), **summarize(rows, agents),
           "run": {"workers": args.workers, "wall_s": round(time.perf_counter() - t0, 1)}}
    if args.injection_paired:
        res["injection_paired"] = injection_paired(seed_set["standard"], workers=args.workers)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "bench-result.v1.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    (out / "LEADERBOARD.md").write_text(leaderboard_md(res), encoding="utf-8")
    print(leaderboard_md(res))
    print(f"digest {res['digest'][:16]}  wall {res['run']['wall_s']} s  -> {out}")


if __name__ == "__main__":
    main()
