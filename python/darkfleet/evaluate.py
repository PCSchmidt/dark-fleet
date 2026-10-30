"""Evaluation harness: tracker comparison (Spike S18 precursor) and bot batch statistics.

Protocol (tracking):
  * Same scenario, same Runner policy, same observation stream for both trackers.
  * SAR taskings are scheduled by a rule that depends only on the dead-reckoning estimate,
    so neither tracker influences what data it receives.
  * Metrics are computed on ticks where the target's AIS has been silent for >= 3 ticks
    (momentary random drop-outs are excluded).
  * Dev seeds (1-40) were used to tune the filter; held-out seeds (1000-1059) were not.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter

import numpy as np

import darkfleet_core as core
from darkfleet_core import evaluation as truth

from .episode import default_play, make_scenario
from .policies import HunterBot, HunterConfig, RunnerPolicy, action
from .tracker import DeadReckoning, ParticleFilter, PFConfig

REGION_UNIFORM_EPS = 0.01
REGION_AREA_M2 = (500_000.0) ** 2
SILENT_MIN_TICKS = 3  # ignore momentary AIS drop-outs (report_prob < 1)
DEV_SEEDS = list(range(1, 41))
HELDOUT_SEEDS = list(range(1000, 1060))


def tracking_episode(seed: int, sar_tasks: int, n_particles: int = 3000, runner_kind: str = "dark_run") -> dict:
    scenario = make_scenario(seed, sar_tasks=sar_tasks)
    tick_s = scenario["time"]["tick_s"]
    runner = RunnerPolicy(kind=runner_kind, seed=seed)
    dr = DeadReckoning(tick_s)
    pf = ParticleFilter(tick_s, PFConfig(n_particles=n_particles, seed=seed))
    w = core.World(json.dumps(scenario))
    last_task = -10**9
    rows = []
    while not w.ended:
        obs_r = json.loads(w.observe("runner"))
        obs_h = json.loads(w.observe("hunter"))
        for a in runner.act(obs_r):
            w.submit_action(json.dumps(a))
        dr.update(obs_h)
        pf.update(obs_h)
        t = obs_h["tick"]
        res = obs_h["resources"]
        fix = dr.last_fix_tick()
        silent = t - fix if fix is not None else 0
        est = dr.estimate()
        if (est and res["sar_tasks_remaining"] > 0 and not res["pending_taskings"] and silent >= 2 and t - last_task >= 6):
            last_task = t
            w.submit_action(json.dumps(action("hunter", t, "task_sensor", {"sensor": "sar", "x_m": est[0], "y_m": est[1]})))
        if silent >= SILENT_MIN_TICKS and pf.initialized:
            tr = json.loads(truth.runner_truth(w))
            tx, ty = tr["x_m"], tr["y_m"]
            ex, ey = est
            px, py = pf.estimate()
            e_dr = math.hypot(ex - tx, ey - ty)
            e_pf = math.hypot(px - tx, py - ty)
            sig_dr = 1000.0 + 0.25 * 7.0 * silent * tick_s  # stated heuristic uncertainty for the baseline
            dens_dr = math.exp(-0.5 * (e_dr / sig_dr) ** 2) / (2 * math.pi * sig_dr**2)
            dens_pf = math.exp(pf.log_density_at(tx, ty))
            # Both densities are mixed with a 1% uniform component over the 500 km region so a single
            # catastrophic miss cannot dominate the mean (same treatment for both trackers).
            floor = REGION_UNIFORM_EPS / REGION_AREA_M2
            nll_dr = -math.log((1 - REGION_UNIFORM_EPS) * dens_dr + floor)
            nll_pf = -math.log((1 - REGION_UNIFORM_EPS) * dens_pf + floor)
            rows.append({"tick": t, "e_dr": e_dr, "e_pf": e_pf, "mass20_pf": pf.mass_within(tx, ty, 20000.0),
                         "hit20_dr": float(e_dr <= 20000.0), "nll_dr": nll_dr, "nll_pf": nll_pf})
        w.step(1)
    if not rows:
        return {"seed": seed, "n_dark": 0}
    arr = {k: np.array([r[k] for r in rows]) for k in rows[0] if k != "tick"}
    return {
        "seed": seed,
        "n_dark": len(rows),
        "mean_err_dr": float(arr["e_dr"].mean()),
        "mean_err_pf": float(arr["e_pf"].mean()),
        "final_err_dr": float(arr["e_dr"][-1]),
        "final_err_pf": float(arr["e_pf"][-1]),
        "mass20_pf": float(arr["mass20_pf"].mean()),
        "hit20_dr": float(arr["hit20_dr"].mean()),
        "nll_dr": float(arr["nll_dr"].mean()),
        "nll_pf": float(arr["nll_pf"].mean()),
    }


def bootstrap_ci(diffs: np.ndarray, n_boot: int = 5000, seed: int = 12345) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    n = len(diffs)
    means = np.array([diffs[rng.integers(0, n, n)].mean() for _ in range(n_boot)])
    return float(diffs.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def summarize(episodes: list[dict]) -> dict:
    eps = [e for e in episodes if e.get("n_dark", 0) > 0]
    out = {"n_episodes": len(episodes), "n_with_dark": len(eps)}
    for metric, a, b in (("mean_err", "mean_err_dr", "mean_err_pf"), ("final_err", "final_err_dr", "final_err_pf"),
                         ("nll", "nll_dr", "nll_pf")):
        d = np.array([e[a] - e[b] for e in eps])  # positive => PF better
        mean, lo, hi = bootstrap_ci(d)
        out[f"{metric}_dr_minus_pf"] = {"mean": mean, "ci95": [lo, hi], "pf_better_fraction": float((d > 0).mean())}
        out[f"{metric}_dr"] = float(np.mean([e[a] for e in eps]))
        out[f"{metric}_pf"] = float(np.mean([e[b] for e in eps]))
    out["pf_mass_within_20km"] = float(np.mean([e["mass20_pf"] for e in eps]))
    out["dr_within_20km_fraction"] = float(np.mean([e["hit20_dr"] for e in eps]))
    return out


def tracking_study(seeds: list[int], sar_tasks: int, n_particles: int = 3000) -> dict:
    return summarize([tracking_episode(s, sar_tasks, n_particles) for s in seeds])


def bot_batch(seeds: list[int], tracker: str = "pf", runner_kind: str = "dark_run") -> dict:
    outcomes: Counter = Counter()
    all_verified = True
    hunter_scores, runner_scores = [], []
    for s in seeds:
        r = default_play(s, runner_kind=runner_kind, tracker=tracker)
        outcomes[r.outcome["outcome"]] += 1
        all_verified &= r.verified["code"] == "OK"
        hunter_scores.append(r.outcome["hunter_score"])
        runner_scores.append(r.outcome["runner_score"])
    return {"tracker": tracker, "runner": runner_kind, "n": len(seeds), "outcomes": dict(outcomes),
            "all_replays_verified": all_verified,
            "mean_hunter_score": float(np.mean(hunter_scores)), "mean_runner_score": float(np.mean(runner_scores))}


def main() -> None:
    ap = argparse.ArgumentParser(description="Dark Fleet tracker/bot evaluation")
    ap.add_argument("--seeds", choices=["dev", "heldout"], default="heldout")
    ap.add_argument("--particles", type=int, default=3000)
    ap.add_argument("--skip-bots", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    seeds = DEV_SEEDS if args.seeds == "dev" else HELDOUT_SEEDS
    result = {"seeds": args.seeds, "n_seeds": len(seeds), "particles": args.particles,
              "tracking_passive": tracking_study(seeds, 0, args.particles),
              "tracking_sar": tracking_study(seeds, 6, args.particles)}
    if not args.skip_bots:
        result["bots"] = {
            "pf_guided_vs_dark_run": bot_batch(seeds, "pf", "dark_run"),
            "dr_guided_vs_dark_run": bot_batch(seeds, "dr", "dark_run"),
            "pf_guided_vs_honest": bot_batch(seeds[:20], "pf", "honest"),
        }
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
