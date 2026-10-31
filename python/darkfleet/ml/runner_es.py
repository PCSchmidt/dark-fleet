"""Learned Runner by self-play search (ML.md section 3), scaled to what this project can verify.

This is NOT deep reinforcement learning. The Runner is a 17-parameter evasion policy (when to go dark, a second dark
window, a spoof window, a lateral detour, a dogleg, speed). Parameters are searched with the cross-entropy method
against a pool of Hunters, then a best-response Hunter is searched (random search over its configuration), added
to the pool, and the Runner is retrained. That is a small population-based self-play loop whose weaknesses are
stated in the model card: a tiny policy class, hand-built Hunters, and a simulator the Runner can exploit.

Reward = the engine's Runner score (delivered 100, escaped accusation partial credit, correct identification 0).

    python -m darkfleet.ml.runner_es --config configs/runner.json --out models/runner
"""
from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from .common import canonical_digest, load_config, provenance, write_json

# name: (low, high, scripted-dark_run-like initial value)
PARAMS = {
    "dark1_start": (4.0, 60.0, 16.0), "dark1_len": (0.0, 90.0, 55.0),
    "dark2_start": (40.0, 130.0, 100.0), "dark2_len": (0.0, 40.0, 0.0),
    "dogleg_offset": (-10.0, 30.0, 8.0), "dogleg_len": (0.0, 30.0, 12.0), "dogleg_deg": (-150.0, 150.0, 70.0),
    "speed_frac": (0.65, 1.0, 0.88), "detour_km": (-60.0, 60.0, 0.0),
    "spoof_start": (0.0, 120.0, 0.0), "spoof_len": (0.0, 70.0, 0.0), "spoof_mag_km": (0.0, 35.0, 20.0), "spoof_angle_deg": (0.0, 360.0, 300.0),
    "pause_every": (0.0, 40.0, 0.0), "pause_len": (0.0, 6.0, 0.0), "turn_jitter_deg": (0.0, 30.0, 0.0), "dark2_dogleg_deg": (-90.0, 90.0, 0.0),
}
NAMES = list(PARAMS)
LO = np.array([PARAMS[n][0] for n in NAMES])
HI = np.array([PARAMS[n][1] for n in NAMES])
INIT = np.array([PARAMS[n][2] for n in NAMES])
V_MAX = 8.5


class LearnedRunner:
    """Parametric Runner. Uses only its own role-filtered observation (position, delivery point)."""

    def __init__(self, theta, seed: int = 0):
        self.p = dict(zip(NAMES, np.clip(np.asarray(theta, float), LO, HI)))
        self.mode = "on"
        self.start = None
        self.rng = np.random.default_rng(seed)

    def _mode_at(self, t: int) -> str:
        p = self.p
        if p["dark1_len"] >= 1 and p["dark1_start"] <= t < p["dark1_start"] + p["dark1_len"]:
            return "dark"
        if p["dark2_len"] >= 1 and p["dark2_start"] <= t < p["dark2_start"] + p["dark2_len"]:
            return "dark"
        if p["spoof_len"] >= 1 and p["spoof_start"] <= t < p["spoof_start"] + p["spoof_len"]:
            return "spoof"
        return "on"

    def act(self, obs: dict) -> list[dict]:
        from ..policies import _bearing_deg, action
        p, t = self.p, obs["tick"]
        own, goal = obs["own"], obs["mission"]["delivery"]
        if self.start is None:
            self.start = (own["x_m"], own["y_m"])
        out = []
        want = self._mode_at(t)
        if want != self.mode:
            params = {"mode": want}
            if want == "spoof":
                a = math.radians(p["spoof_angle_deg"])
                params.update({"offset_x_m": p["spoof_mag_km"] * 1000.0 * math.sin(a), "offset_y_m": p["spoof_mag_km"] * 1000.0 * math.cos(a)})
            out.append(action("runner", t, "set_ais_mode", params))
            self.mode = want
        if t % 3 == 0:
            sx, sy = self.start
            gx, gy = goal["x_m"], goal["y_m"]
            L = math.hypot(gx - sx, gy - sy) or 1.0
            ux, uy = (gx - sx) / L, (gy - sy) / L
            f = ((own["x_m"] - sx) * ux + (own["y_m"] - sy) * uy) / L
            fa = min(1.0, max(0.0, f) + 0.12)                       # aim a bit ahead along the (curved) route
            lat = p["detour_km"] * 1000.0 * math.sin(math.pi * fa)
            tx, ty = sx + ux * L * fa - uy * lat, sy + uy * L * fa + ux * lat
            if fa >= 1.0:
                tx, ty = gx, gy
            course = _bearing_deg(tx - own["x_m"], ty - own["y_m"])
            ds = p["dark1_start"] + p["dogleg_offset"]
            if p["dogleg_len"] >= 1 and ds <= t < ds + p["dogleg_len"]:
                course += p["dogleg_deg"]
            if p["dark2_len"] >= 1 and p["dark2_start"] <= t < p["dark2_start"] + p["dark2_len"]:
                course += p["dark2_dogleg_deg"]
            if p["turn_jitter_deg"] > 0:
                course += float(self.rng.normal(0, p["turn_jitter_deg"] / 3.0))
            speed = V_MAX * p["speed_frac"]
            if p["pause_every"] >= 8 and p["pause_len"] >= 1 and (t % int(p["pause_every"])) < int(p["pause_len"]):
                speed *= 0.5
            out.append(action("runner", t, "set_course_speed", {"course_deg": course % 360.0, "speed_mps": min(speed, V_MAX)}))
        return out


# ---- hunters --------------------------------------------------------------------------------------
HUNTERS = {
    "pf_greedy": dict(kind="bot", tracker="pf", require_dark_ticks=6),
    "pf_audit": dict(kind="bot", tracker="pf", require_dark_ticks=6, audit_ais=True),
    "dr_rule": dict(kind="bot", tracker="dr", require_dark_ticks=6),
    "pf_audit_tuned": dict(kind="bot", tracker="pf", require_dark_ticks=4, audit_ais=True, audit_tick=12, lock_radius_m=25000.0,
                           silent_ticks_before_tasking=3, min_ticks_between_tasks=5),
    "case_officer": dict(kind="officer"),
}


def make_hunter(spec: dict, seed: int):
    from ..policies import HunterBot, HunterConfig
    s = {k: v for k, v in spec.items() if k != "kind"}
    return HunterBot(HunterConfig(pf_seed=seed, n_particles=s.pop("n_particles", 1500), **s))


def play_one(job: tuple) -> dict:
    theta, hunter_name, hunter_spec, seed, n_bg = job
    import darkfleet_core as core

    from ..episode import make_scenario, play
    spec = hunter_spec or HUNTERS[hunter_name]
    runner = LearnedRunner(theta, seed)
    if spec["kind"] == "officer":
        from ..agent.case_officer import CaseOfficer
        from ..agent.env import HunterEnv
        from ..agent.run import autopilot
        env = HunterEnv(seed, "honest", n_background=n_bg)
        # drive the learned runner instead of the scripted one
        env.runner = runner
        res = autopilot(env, CaseOfficer(env.host))
        oc, verified, ticks = res["outcome"], res["verification"]["code"] == "OK", env.tick
    else:
        r = play(make_scenario(seed, n_bg, brief="profile"), runner, make_hunter(spec, seed))
        oc, verified, ticks = r.outcome, r.verified["code"] == "OK", r.replay["n_ticks"]
    return {"hunter": hunter_name, "seed": seed, "outcome": oc["outcome"], "runner_score": oc["runner_score"],
            "hunter_score": oc["hunter_score"], "delivered": oc["outcome"] == "runner_delivered",
            "identified": oc["outcome"] == "correct_supported", "verified": bool(verified), "ticks": ticks}


# ---- baselines ------------------------------------------------------------------------------------
def baseline_thetas() -> dict[str, np.ndarray]:
    def with_(**kw):
        v = INIT.copy()
        for k, x in kw.items():
            v[NAMES.index(k)] = x
        return v
    return {
        "shortest_path": with_(dark1_len=0, dogleg_len=0, speed_frac=0.95),
        "scripted_dark_run": INIT.copy(),
        "random_ais": with_(dark1_start=10, dark1_len=20, dark2_start=70, dark2_len=25, dogleg_len=0, spoof_start=40, spoof_len=20, spoof_mag_km=8, turn_jitter_deg=10),
    }


def evaluate(pool, theta, hunters: list[str], seeds: list[int], n_bg: int, ex, extra_specs: dict | None = None) -> dict:
    jobs = [(list(map(float, theta)), h, (extra_specs or {}).get(h), s, n_bg) for h in hunters for s in seeds]
    rows = list(ex.map(play_one, jobs, chunksize=2)) if ex else [play_one(j) for j in jobs]
    return rows


def summarize(rows: list[dict]) -> dict:
    from ..bench import boot_mean, wilson
    out = {}
    for h in sorted({r["hunter"] for r in rows}):
        rr = [r for r in rows if r["hunter"] == h]
        m, lo, hi = boot_mean([r["runner_score"] for r in rr])
        k = sum(r["delivered"] for r in rr)
        ki = sum(r["identified"] for r in rr)
        out[h] = {"n": len(rr), "runner_value": {"mean": m, "ci95": [lo, hi]}, "delivery_rate": wilson(k, len(rr)),
                  "identification_rate": wilson(ki, len(rr)), "hunter_value": float(np.mean([r["hunter_score"] for r in rr])),
                  "verified": sum(r["verified"] for r in rr)}
    return out


# ---- training -------------------------------------------------------------------------------------
def cem(ex, cfg: dict, pool: list[str], specs: dict, rng: np.random.Generator, start: np.ndarray, gen0: int, log: list) -> np.ndarray:
    mean, std = start.copy(), (HI - LO) * cfg["init_std_frac"]
    best, best_f = start.copy(), -1e9
    for g in range(cfg["generations"]):
        seeds = [40000 + (gen0 + g) * 100 + i for i in range(cfg["seeds_per_candidate"])]
        pop = [np.clip(mean + std * rng.standard_normal(len(mean)), LO, HI) for _ in range(cfg["population"] - 1)] + [mean.copy()]
        jobs = [(list(map(float, th)), h, specs.get(h), s, 20) for th in pop for h in pool for s in seeds]
        res = list(ex.map(play_one, jobs, chunksize=4))
        per = len(pool) * len(seeds)
        fit = np.array([np.mean([r["runner_score"] for r in res[i * per:(i + 1) * per]]) for i in range(len(pop))])
        elite = np.argsort(-fit)[: cfg["elite"]]
        mean = cfg["smooth"] * mean + (1 - cfg["smooth"]) * np.mean([pop[i] for i in elite], axis=0)
        std = np.maximum(cfg["smooth"] * std + (1 - cfg["smooth"]) * np.std([pop[i] for i in elite], axis=0), (HI - LO) * cfg["min_std_frac"])
        if fit[elite[0]] > best_f:
            best_f, best = float(fit[elite[0]]), pop[elite[0]].copy()
        log.append({"gen": gen0 + g, "best": float(fit.max()), "mean": float(fit.mean()), "elite_mean": float(fit[elite].mean()),
                    "pool": list(pool)})
        print(f"  gen {gen0 + g}: best {fit.max():.1f} mean {fit.mean():.1f}", flush=True)
    return mean if cfg.get("return_mean", True) else best


def best_response(ex, cfg: dict, theta: np.ndarray, rng: np.random.Generator) -> tuple[dict, list]:
    space = {"silent_ticks_before_tasking": [1, 2, 3, 4], "min_ticks_between_tasks": [3, 4, 6, 8], "require_dark_ticks": [0, 3, 6, 9],
             "audit_ais": [True, False], "audit_tick": [4, 8, 12, 16], "lock_radius_m": [15000.0, 20000.0, 30000.0], "gate_margin_m": [3000.0, 5000.0, 9000.0]}
    cands = [{"kind": "bot", "tracker": "pf", **{k: v[int(rng.integers(len(v)))] for k, v in space.items()}} for _ in range(cfg["br_candidates"])]
    seeds = [60000 + i for i in range(cfg["br_seeds"])]
    jobs = [(list(map(float, theta)), f"br{i}", c, s, 20) for i, c in enumerate(cands) for s in seeds]
    res = list(ex.map(play_one, jobs, chunksize=4))
    scores = [np.mean([r["hunter_score"] for r in res[i * len(seeds):(i + 1) * len(seeds)]]) for i in range(len(cands))]
    order = np.argsort(-np.array(scores))
    return cands[int(order[0])], [{"spec": cands[i], "hunter_value": float(scores[i])} for i in order[:5]]


def main() -> None:
    ap = argparse.ArgumentParser(description="Train the learned Runner by self-play search")
    ap.add_argument("--config", default="configs/runner.json")
    ap.add_argument("--out", default="models/runner")
    ap.add_argument("--workers", type=int, default=22)
    args = ap.parse_args()
    cfg = load_config(args.config)
    t0 = time.perf_counter()
    rng = np.random.default_rng(cfg["seed"])
    specs: dict = {}
    pool = list(cfg["train_pool"])
    theta = INIT.copy()
    history, rounds = [], []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for rnd in range(cfg["rounds"]):
            print(f"round {rnd}: pool {pool}", flush=True)
            theta = cem(ex, cfg, pool, specs, rng, theta if rnd else INIT.copy(), rnd * cfg["generations"], history)
            br_spec, br_top = best_response(ex, cfg, theta, rng)
            name = f"best_response_r{rnd}"
            specs[name] = br_spec
            pool.append(name)
            rounds.append({"round": rnd, "theta": dict(zip(NAMES, map(float, theta))), "best_response": br_spec, "best_response_top5": br_top})
        # held-out evaluation: seeds never used in training; Hunters partly unseen
        ho_seeds = [50000 + i for i in range(cfg["heldout_seeds"])]
        eval_hunters = cfg["eval_hunters"] + [n for n in pool if n.startswith("best_response")]
        results = {}
        for name, th in {**baseline_thetas(), "learned": theta}.items():
            rows = evaluate(None, th, [h for h in eval_hunters if h in HUNTERS or h in specs], ho_seeds, 20, ex, specs)
            results[name] = summarize(rows)
        ho_shift = {}
        rows = evaluate(None, theta, [h for h in cfg["eval_hunters"] if h != "case_officer"], ho_seeds[: cfg["heldout_seeds"] // 2], 45, ex, specs)
        ho_shift["learned_decoy_rich"] = summarize(rows)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    digest = canonical_digest({"theta": list(map(float, theta)), "rounds": rounds})
    prov = provenance(cfg, digest)
    write_json(out / "runner_policy.json", {"format": "darkfleet-learned-runner-v1", "params": dict(zip(NAMES, map(float, theta))),
                                            "bounds": {n: [PARAMS[n][0], PARAMS[n][1]] for n in NAMES}, "provenance": prov})
    write_json(out / "metrics.json", {"provenance": prov, "train_pool": cfg["train_pool"], "rounds": rounds, "history": history,
                                      "heldout": results, "heldout_shift": ho_shift, "eval_hunters": eval_hunters,
                                      "train_seconds": round(time.perf_counter() - t0, 1)})
    for name, r in results.items():
        print(name, {h: round(v["runner_value"]["mean"], 1) for h, v in r.items()})


def load_policy(path: str | Path, seed: int = 0) -> LearnedRunner:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return LearnedRunner([d["params"][n] for n in NAMES], seed)


if __name__ == "__main__":
    main()
