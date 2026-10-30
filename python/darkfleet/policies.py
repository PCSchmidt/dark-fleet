"""Scripted Runner policies and a baseline Hunter bot.

Everything here consumes only role-filtered observation documents and returns action
documents (schemas/action.v1.json). No truth access.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .tracker import DeadReckoning, ParticleFilter, PFConfig

SCHEMA = "1.1.0"


def action(role: str, tick: int, action_type: str, params: dict) -> dict:
    return {"schema_version": SCHEMA, "role": role, "tick": tick, "action_type": action_type, "params": params}


def _bearing_deg(dx: float, dy: float) -> float:
    b = math.degrees(math.atan2(dx, dy))
    b = b % 360.0
    return 0.0 if b >= 360.0 else b


# --------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------
@dataclass
class RunnerPolicy:
    """Scripted Runner. ``kind`` is one of:

    honest    AIS on for the whole run; steer straight to the delivery point.
    dark_run  go dark for a window and make one heading dogleg while dark.
    spoof     broadcast a constant offset position for the whole run.
    """

    kind: str = "dark_run"
    seed: int = 0
    max_speed_mps: float = 8.5
    rng: random.Random = field(init=False)

    def __post_init__(self):
        self.rng = random.Random(self.seed)
        self.t_dark = self.rng.randint(10, 24)
        self.dark_len = self.rng.randint(40, 70)
        self.dogleg_start = self.t_dark + self.rng.randint(4, 14)
        self.dogleg_len = self.rng.randint(8, 16)
        self.dogleg_deg = self.rng.choice([-1, 1]) * self.rng.uniform(40, 100)
        self.speed = self.max_speed_mps * self.rng.uniform(0.8, 0.95)
        self._ais_mode = "on"

    def act(self, obs: dict) -> list[dict]:
        t = obs["tick"]
        own, mission = obs["own"], obs["mission"]["delivery"]
        out: list[dict] = []
        want = "on"
        if self.kind == "dark_run" and self.t_dark <= t < self.t_dark + self.dark_len:
            want = "dark"
        if self.kind == "spoof":
            want = "spoof"
        if want != self._ais_mode:
            params = {"mode": want}
            if want == "spoof":
                params.update({"offset_x_m": 20000.0, "offset_y_m": -10000.0})
            out.append(action("runner", t, "set_ais_mode", params))
            self._ais_mode = want
        if t % 3 == 0:
            course = _bearing_deg(mission["x_m"] - own["x_m"], mission["y_m"] - own["y_m"])
            if self.kind == "dark_run" and self.dogleg_start <= t < self.dogleg_start + self.dogleg_len:
                course = (course + self.dogleg_deg) % 360.0
            out.append(action("runner", t, "set_course_speed", {"course_deg": course, "speed_mps": self.speed}))
        return out


# --------------------------------------------------------------------------------------
# Hunter
# --------------------------------------------------------------------------------------
@dataclass
class HunterConfig:
    tick_s: float = 300.0
    tracker: str = "pf"            # "pf" or "dr": which estimate guides SAR tasking
    n_particles: int = 3000
    pf_seed: int = 0
    silent_ticks_before_tasking: int = 2
    min_ticks_between_tasks: int = 6
    claim_min_chain: int = 2
    gate_margin_m: float = 5000.0
    claim_when_chain_matches_estimate: bool = True


class HunterBot:
    """Baseline Hunter: track the target, task SAR where it should be, link SAR contacts
    into a chain, and file a claim when a chain agrees with the tracker."""

    def __init__(self, cfg: HunterConfig | None = None):
        self.cfg = cfg or HunterConfig()
        self.dr = DeadReckoning(self.cfg.tick_s)
        self.pf = ParticleFilter(self.cfg.tick_s, PFConfig(n_particles=self.cfg.n_particles, seed=self.cfg.pf_seed))
        self.last_task_tick = -10**9
        self.claimed = False

    # tracker access for evaluation
    def estimate(self) -> tuple[float, float] | None:
        return (self.pf if self.cfg.tracker == "pf" else self.dr).estimate()

    def _target_silent_for(self, obs: dict) -> int:
        fix = self.dr.last_fix_tick()
        return obs["tick"] - fix if fix is not None else 0

    def act(self, obs: dict) -> list[dict]:
        self.dr.update(obs)
        self.pf.update(obs)
        t = obs["tick"]
        out: list[dict] = []
        if obs["status"]["state"] != "running" or self.claimed:
            return out

        res = obs["resources"]
        est = self.estimate()

        chain = self._best_chain(obs, est)
        if chain and len(chain) >= self.cfg.claim_min_chain:
            self.claimed = True
            out.append(action("hunter", t, "file_claim", {"contact_id": chain[-1], "evidence": chain}))
            return out

        if (
            est is not None
            and res["sar_tasks_remaining"] > 0
            and not res["pending_taskings"]
            and self._target_silent_for(obs) >= self.cfg.silent_ticks_before_tasking
            and t - self.last_task_tick >= self.cfg.min_ticks_between_tasks
        ):
            self.last_task_tick = t
            out.append(action("hunter", t, "task_sensor", {"sensor": "sar", "x_m": est[0], "y_m": est[1]}))
        return out

    # -- evidence linking ---------------------------------------------------------------
    def _best_chain(self, obs: dict, est: tuple[float, float] | None) -> list[str] | None:
        """Link SAR contacts from different passes that are kinematically consistent with one
        vessel of the target's class, and that sit near the tracker's estimate."""
        if est is None:
            return None
        sar = [c for c in obs["contacts"] if c["source"] == "sar" and c["size_class"] == "large"]
        if len(sar) < 2:
            return None
        v_max = 8.5
        sar.sort(key=lambda c: obs["tick"] - c["age_ticks"])
        best: list[dict] = []
        for i, a in enumerate(sar):
            chain = [a]
            for b in sar[i + 1 :]:
                last = chain[-1]
                ta, tb = obs["tick"] - last["age_ticks"], obs["tick"] - b["age_ticks"]
                if tb <= ta:
                    continue
                reach = v_max * (tb - ta) * self.cfg.tick_s + 3 * (last["sigma_m"] + b["sigma_m"])
                if math.hypot(b["x_m"] - last["x_m"], b["y_m"] - last["y_m"]) <= reach:
                    chain.append(b)
            if len(chain) > len(best):
                best = chain
        if len(best) < 2:
            return None
        if self.cfg.claim_when_chain_matches_estimate:
            gate = (self.pf.spread_m() if self.cfg.tracker == "pf" else 0.0) * 2 + self.cfg.gate_margin_m
            last = best[-1]
            if math.hypot(last["x_m"] - est[0], last["y_m"] - est[1]) > max(gate, 15000.0):
                return None
        return [c["contact_id"] for c in best]
