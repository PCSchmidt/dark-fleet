"""Learned Runner for rules v3 (the mission needs a loitering transfer), by the same self-play search as runner_es.

Rules v2 gave a Runner no reason to deceive: the search converged to honest driving (docs/model-cards/learned-runner.md).
Under v3 the Runner can complete its mission only by loitering in a zone, which a Hunter can catch, so hiding is worth
something. The policy has 11 parameters (whether to transfer at all, when to go dark or spoof around the zone, where in the
zone to stop and how fast to creep, a lateral detour on the way in, speed). The search is the same cross-entropy method
against a pool of Hunters plus a searched best-response Hunter.

    python -m darkfleet.ml.runner_v3 --config configs/runner_v3.json --out models/runner_v3
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
from .runner_es import HUNTERS, make_hunter, summarize

# name: (low, high, initial). The initial point is the scripted visible transfer: AIS on, stop at the centre, no creep.
PARAMS = {
    "p_transfer": (0.0, 1.0, 1.0),          # > 0.5: do the transfer at all (otherwise deliver straight and take the small payout)
    "dark_lead": (0.0, 30.0, 0.0),          # ticks before reaching the zone to go dark (< 1: never)
    "dark_tail": (0.0, 24.0, 0.0),          # ticks after finishing the transfer to stay dark
    "use_spoof": (0.0, 1.0, 0.0),           # > 0.5: spoof instead of going dark inside that window
    "spoof_mag_km": (0.0, 35.0, 20.0), "spoof_angle_deg": (0.0, 360.0, 300.0),
    "stop_offset_m": (0.0, 2300.0, 0.0),    # where to stop, measured from the zone centre toward the way in
    "creep_mps": (0.0, 1.0, 0.0),           # loiter speed (the zone allows up to 1.0)
    "detour_km": (-30.0, 30.0, 0.0),        # lateral bow on the way to the zone
    "speed_frac": (0.65, 1.0, 0.88),
    "hurry_after": (0.0, 1.0, 0.0),         # > 0.5: run at full speed once the transfer is done
}
NAMES = list(PARAMS)
LO = np.array([PARAMS[n][0] for n in NAMES])
HI = np.array([PARAMS[n][1] for n in NAMES])
INIT = np.array([PARAMS[n][2] for n in NAMES])
V_MAX = 8.5


class LearnedRunnerV3:
    """Parametric Runner. Uses only its own role-filtered observation."""

    def __init__(self, theta, seed: int = 0):
        self.p = dict(zip(NAMES, np.clip(np.asarray(theta, float), LO, HI)))
        self.mode, self.start, self.done_tick = "on", None, None

    def act(self, obs: dict) -> list[dict]:
        from ..policies import _bearing_deg, action
        p, t = self.p, obs["tick"]
        own, goal, tr = obs["own"], obs["mission"]["delivery"], obs["mission"].get("transfer")
        if self.start is None:
            self.start = (own["x_m"], own["y_m"])
        out: list[dict] = []
        speed = V_MAX * p["speed_frac"]
        transfer = tr is not None and p["p_transfer"] > 0.5
        want, course, v = "on", _bearing_deg(goal["x_m"] - own["x_m"], goal["y_m"] - own["y_m"]), speed
        if transfer:
            done = tr["progress_ticks"] >= tr["required_ticks"]
            if done and self.done_tick is None:
                self.done_tick = t
            sx, sy = self.start
            dx, dy = tr["x_m"] - sx, tr["y_m"] - sy
            L = math.hypot(dx, dy) or 1.0
            ux, uy = dx / L, dy / L
            stop = (tr["x_m"] - ux * p["stop_offset_m"], tr["y_m"] - uy * p["stop_offset_m"])
            d_zone = math.hypot(tr["x_m"] - own["x_m"], tr["y_m"] - own["y_m"])
            if p["dark_lead"] >= 1:
                active = (not done and d_zone <= speed * 300.0 * p["dark_lead"]) or (done and t - self.done_tick < p["dark_tail"])
                if active:
                    want = "spoof" if p["use_spoof"] > 0.5 else "dark"
            if done:
                v = V_MAX if p["hurry_after"] > 0.5 else speed
            else:
                f = max(0.0, min(1.0, ((own["x_m"] - sx) * ux + (own["y_m"] - sy) * uy) / L))
                bow = p["detour_km"] * 1000.0 * math.sin(math.pi * f)
                aim = (stop[0] - uy * bow, stop[1] + ux * bow)
                if math.hypot(stop[0] - own["x_m"], stop[1] - own["y_m"]) <= 1500.0:   # at the stop point: loiter
                    course, v = _bearing_deg(tr["x_m"] - own["x_m"], tr["y_m"] - own["y_m"]), p["creep_mps"]
                else:
                    course = _bearing_deg(aim[0] - own["x_m"], aim[1] - own["y_m"])
        if want != self.mode:
            params = {"mode": want}
            if want == "spoof":
                a = math.radians(p["spoof_angle_deg"])
                params.update({"offset_x_m": p["spoof_mag_km"] * 1000.0 * math.sin(a), "offset_y_m": p["spoof_mag_km"] * 1000.0 * math.cos(a)})
            out.append(action("runner", t, "set_ais_mode", params))
            self.mode = want
        out.append(action("runner", t, "set_course_speed", {"course_deg": course % 360.0, "speed_mps": min(v, V_MAX)}))
        return out


def load_policy(path: str | Path, seed: int = 0) -> LearnedRunnerV3:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return LearnedRunnerV3([d["params"][n] for n in NAMES], seed)


# ---- episodes -------------------------------------------------------------------------------------
def play_one(job: tuple) -> dict:
    theta, hunter_name, hunter_spec, seed, n_bg = job
    from ..episode import make_scenario, play
    spec = hunter_spec or HUNTERS[hunter_name]
    runner = LearnedRunnerV3(theta, seed)
    if spec["kind"] == "officer":
        from ..agent.case_officer import CaseOfficer
        from ..agent.env import HunterEnv
        from ..agent.run import autopilot
        env = HunterEnv(seed, "honest", n_background=n_bg, transfer=True, analyst_cfg=spec.get("analyst_cfg"))
        env.runner = runner
        res = autopilot(env, CaseOfficer(env.host))
        oc, verified, ticks = res["outcome"], res["verification"]["code"] == "OK", env.tick
    else:
        r = play(make_scenario(seed, n_bg, brief="profile", transfer=True), runner, make_hunter(spec, seed))
        oc, verified, ticks = r.outcome, r.verified["code"] == "OK", r.replay["n_ticks"]
    name = oc["outcome"]
    return {"hunter": hunter_name, "seed": seed, "outcome": name, "runner_score": oc["runner_score"], "hunter_score": oc["hunter_score"],
            "delivered": name in ("runner_delivered", "delivered_without_transfer"), "completed": name == "runner_delivered",
            "identified": name == "correct_supported", "verified": bool(verified), "ticks": ticks}


def baseline_thetas() -> dict[str, np.ndarray]:
    def with_(**kw):
        v = INIT.copy()
        for k, x in kw.items():
            v[NAMES.index(k)] = x
        return v
    return {
        "no_transfer": with_(p_transfer=0.0, speed_frac=0.95),
        "visible_transfer": INIT.copy(),
        "scripted_dark": with_(dark_lead=7, dark_tail=5),
        "scripted_spoof": with_(dark_lead=30, dark_tail=24, use_spoof=1.0),
    }


def evaluate(theta, hunters: list[str], seeds: list[int], n_bg: int, ex, specs: dict | None = None) -> list[dict]:
    jobs = [(list(map(float, theta)), h, (specs or {}).get(h), s, n_bg) for h in hunters for s in seeds]
    return list(ex.map(play_one, jobs, chunksize=2)) if ex else [play_one(j) for j in jobs]


def summarize_v3(rows: list[dict]) -> dict:
    out = summarize(rows)
    for h, v in out.items():
        rr = [r for r in rows if r["hunter"] == h]
        v["completion_rate"] = sum(r["completed"] for r in rr) / len(rr)
        v["outcomes"] = {o: sum(1 for r in rr if r["outcome"] == o) for o in sorted({r["outcome"] for r in rr})}
    return out


def cem(ex, cfg: dict, pool: list[str], specs: dict, rng: np.random.Generator, start: np.ndarray, gen0: int, log: list) -> np.ndarray:
    mean, std = start.copy(), (HI - LO) * cfg["init_std_frac"]
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
        log.append({"gen": gen0 + g, "best": float(fit.max()), "mean": float(fit.mean()), "elite_mean": float(fit[elite].mean()), "pool": list(pool)})
        print(f"  gen {gen0 + g}: best {fit.max():.1f} mean {fit.mean():.1f}", flush=True)
    return mean


def best_response(ex, cfg: dict, theta: np.ndarray, rng: np.random.Generator) -> tuple[dict, list]:
    space = {"silent_ticks_before_tasking": [1, 2, 3], "min_ticks_between_tasks": [3, 4, 6], "audit_ais": [True, False], "audit_tick": [4, 8, 12],
             "lock_radius_m": [15000.0, 20000.0, 30000.0], "stationary_gate_m": [800.0, 1000.0, 1500.0, 2500.0],
             "explain_gate_m": [500.0, 800.0, 1500.0]}
    if cfg.get("search_audit_jump"):   # added after the first run, kept optional so that run still reproduces
        space["audit_jump"] = [True, False]
    if cfg.get("search_sweep_area"):
        space["sweep_area"] = [True, False]
    cands = [{"kind": "bot", "tracker": "pf", **{k: v[int(rng.integers(len(v)))] for k, v in space.items()}} for _ in range(cfg["br_candidates"])]
    seeds = [60000 + i for i in range(cfg["br_seeds"])]
    jobs = [(list(map(float, theta)), f"br{i}", c, s, 20) for i, c in enumerate(cands) for s in seeds]
    res = list(ex.map(play_one, jobs, chunksize=4))
    scores = [np.mean([r["hunter_score"] for r in res[i * len(seeds):(i + 1) * len(seeds)]]) for i in range(len(cands))]
    order = np.argsort(-np.array(scores))
    return cands[int(order[0])], [{"spec": cands[i], "hunter_value": float(scores[i])} for i in order[:5]]


def main() -> None:
    ap = argparse.ArgumentParser(description="Train the rules-v3 learned Runner by self-play search")
    ap.add_argument("--config", default="configs/runner_v3.json")
    ap.add_argument("--out", default="models/runner_v3")
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
            theta = cem(ex, cfg, pool, specs, rng, theta, rnd * cfg["generations"], history)
            br_spec, br_top = best_response(ex, cfg, theta, rng)
            name = f"best_response_r{rnd}"
            specs[name] = br_spec
            pool.append(name)
            rounds.append({"round": rnd, "theta": dict(zip(NAMES, map(float, theta))), "best_response": br_spec, "best_response_top5": br_top})
        ho_seeds = [50000 + i for i in range(cfg["heldout_seeds"])]
        eval_hunters = cfg["eval_hunters"] + [n for n in pool if n.startswith("best_response")]
        results = {}
        for name, th in {**baseline_thetas(), "learned": theta}.items():
            results[name] = summarize_v3(evaluate(th, [h for h in eval_hunters if h in HUNTERS or h in specs], ho_seeds, 20, ex, specs))
        ho_shift = {"learned_decoy_rich": summarize_v3(evaluate(theta, [h for h in cfg["eval_hunters"] if h != "case_officer"],
                                                                ho_seeds[: cfg["heldout_seeds"] // 2], 45, ex, specs))}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    digest = canonical_digest({"theta": list(map(float, theta)), "rounds": rounds})
    prov = provenance(cfg, digest)
    write_json(out / "runner_policy.json", {"format": "darkfleet-learned-runner-v3", "params": dict(zip(NAMES, map(float, theta))),
                                            "bounds": {n: [PARAMS[n][0], PARAMS[n][1]] for n in NAMES}, "provenance": prov})
    write_json(out / "metrics.json", {"provenance": prov, "train_pool": cfg["train_pool"], "rounds": rounds, "history": history,
                                      "heldout": results, "heldout_shift": ho_shift, "eval_hunters": eval_hunters,
                                      "train_seconds": round(time.perf_counter() - t0, 1)})
    for name, r in results.items():
        print(name, {h: round(v["runner_value"]["mean"], 1) for h, v in r.items()})


if __name__ == "__main__":
    main()
