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
    lock_radius_m: float = 20000.0   # profile briefing: AIS vessel nearest the last-known area becomes the target
    audit_ais: bool = False          # spend one SAR pass early to check the target's AIS against reality
    audit_tick: int = 8
    audit_gate_m: float = 4000.0
    spoof_gate_m: float = 10000.0


class HunterBot:
    """Baseline Hunter: track the target, task SAR where it should be, link SAR contacts
    into a chain, and file a claim when a chain agrees with the tracker."""

    def __init__(self, cfg: HunterConfig | None = None):
        self.cfg = cfg or HunterConfig()
        self.dr = DeadReckoning(self.cfg.tick_s)
        self.pf = ParticleFilter(self.cfg.tick_s, PFConfig(n_particles=self.cfg.n_particles, seed=self.cfg.pf_seed))
        self.last_task_tick = -10**9
        self.claimed = False
        self.locked_mmsi: str | None = None
        self.ais_hist: dict[str, dict[int, tuple[float, float]]] = {}
        self.audit_exec: int | None = None
        self.audit_requested = False
        self.audit_result: str | None = None      # None | consistent | spoof_suspected | inconclusive
        self.spoof_offset: tuple[float, float] | None = None
        self.spoof_contacts: dict[str, tuple[int, float, float]] = {}
        self.pending_execs: set[int] = set()
        self.notes: list[str] = []

    # tracker access for evaluation
    def estimate(self) -> tuple[float, float] | None:
        return (self.pf if self.cfg.tracker == "pf" else self.dr).estimate()

    def _target_silent_for(self, obs: dict) -> int:
        fix = self.dr.last_fix_tick()
        return obs["tick"] - fix if fix is not None else 0

    # -- identification (profile briefing) ---------------------------------------------
    def _ingest_ais(self, obs: dict) -> None:
        for c in obs["contacts"]:
            if c["source"] == "ais":
                self.ais_hist.setdefault(c["claimed_mmsi"], {})[obs["tick"] - c["age_ticks"]] = (c["x_m"], c["y_m"])

    def _resolve_brief(self, obs: dict) -> dict:
        """Give the trackers a brief with a target MMSI. Under a profile briefing the bot must
        infer it: the AIS vessel nearest the last-known area (within ``lock_radius_m``)."""
        brief = obs.get("brief")
        if not brief or "target_mmsi" in brief:
            return obs
        if self.locked_mmsi is None:
            lk = brief["last_known"]
            best, best_d = None, self.cfg.lock_radius_m
            for c in obs["contacts"]:
                if c["source"] != "ais" or c["age_ticks"] > 1:
                    continue
                d = math.hypot(c["x_m"] - lk["x_m"], c["y_m"] - lk["y_m"])
                if d < best_d:
                    best, best_d = c["claimed_mmsi"], d
            self.locked_mmsi = best
        if self.locked_mmsi is None:
            return obs
        return {**obs, "brief": {**brief, "target_mmsi": self.locked_mmsi}}

    def _target_fix_near(self, tick: int) -> tuple[float, float] | None:
        h = self.ais_hist.get(self.locked_mmsi or "", {})
        if not h:
            return None
        k = min(h, key=lambda s: (abs(s - tick), s))
        return h[k] if abs(k - tick) <= 3 else None

    def _sar_large_at(self, obs: dict, tick: int) -> list[dict]:
        return [c for c in obs["contacts"] if c["source"] == "sar" and c["size_class"] == "large"
                and obs["tick"] - c["age_ticks"] == tick]

    def _ais_explains(self, x: float, y: float, tick: int) -> bool:
        for hist in self.ais_hist.values():
            for s in (tick - 1, tick, tick + 1):
                p = hist.get(s)
                if p and math.hypot(p[0] - x, p[1] - y) <= self.cfg.audit_gate_m:
                    return True
        return False

    def _audit_step(self, obs: dict) -> None:
        """Evaluate the early AIS audit pass once its results are visible."""
        if self.audit_exec is None or self.audit_result is not None:
            return
        t = obs["tick"]
        if t < self.audit_exec:
            return
        fix = self._target_fix_near(self.audit_exec)
        large = self._sar_large_at(obs, self.audit_exec)
        if fix is None:
            self.audit_result = "inconclusive"
            return
        if any(math.hypot(c["x_m"] - fix[0], c["y_m"] - fix[1]) <= self.cfg.audit_gate_m for c in large):
            self.audit_result = "consistent"
            self.notes.append(f"t={t}: audit pass found a large vessel at the target's AIS position")
            return
        unexplained = [c for c in large if not self._ais_explains(c["x_m"], c["y_m"], self.audit_exec)]
        if not unexplained:
            self.audit_result = "inconclusive"
            self.notes.append(f"t={t}: audit pass found nothing conclusive")
            return
        c = min(unexplained, key=lambda c: math.hypot(c["x_m"] - fix[0], c["y_m"] - fix[1]))
        self.spoof_offset = (c["x_m"] - fix[0], c["y_m"] - fix[1])
        self.spoof_contacts[c["contact_id"]] = (self.audit_exec, c["x_m"], c["y_m"])
        self.audit_result = "spoof_suspected"
        self.notes.append(f"t={t}: no vessel under the AIS position; unexplained large vessel at offset "
                          f"({self.spoof_offset[0]:.0f}, {self.spoof_offset[1]:.0f}) m")

    def _spoof_step(self, obs: dict, res: dict) -> list[dict]:
        """Follow the vessel at AIS position + estimated offset; claim on a two-pass SAR chain."""
        t = obs["tick"]
        out: list[dict] = []
        for e in sorted(self.pending_execs):
            if e <= t and self.spoof_offset is not None:
                fix = self._target_fix_near(e)
                if fix is None:
                    continue
                px, py = fix[0] + self.spoof_offset[0], fix[1] + self.spoof_offset[1]
                for c in self._sar_large_at(obs, e):
                    if math.hypot(c["x_m"] - px, c["y_m"] - py) <= self.cfg.spoof_gate_m:
                        self.spoof_contacts[c["contact_id"]] = (e, c["x_m"], c["y_m"])
        ordered = sorted(self.spoof_contacts.items(), key=lambda kv: kv[1][0])
        if len({v[0] for _, v in ordered}) >= 2:
            ids = [cid for cid, _ in ordered]
            self.claimed = True
            out.append(action("hunter", t, "file_claim", {"contact_id": ids[-1], "evidence": ids}))
            return out
        fix = self._target_fix_near(t)
        if (fix is not None and self.spoof_offset is not None and res["sar_tasks_remaining"] > 0
                and not res["pending_taskings"] and t - self.last_task_tick >= 4):
            self.last_task_tick = t
            out.append(action("hunter", t, "task_sensor",
                              {"sensor": "sar", "x_m": fix[0] + self.spoof_offset[0],
                               "y_m": fix[1] + self.spoof_offset[1]}))
        return out

    def act(self, obs: dict) -> list[dict]:
        self._ingest_ais(obs)
        obs = self._resolve_brief(obs)
        self.dr.update(obs)
        self.pf.update(obs)
        t = obs["tick"]
        out: list[dict] = []
        if obs["status"]["state"] != "running" or self.claimed:
            return out

        res = obs["resources"]
        for p in res["pending_taskings"]:
            self.pending_execs.add(p["exec_tick"])
        if self.audit_requested and self.audit_exec is None and res["pending_taskings"]:
            self.audit_exec = res["pending_taskings"][-1]["exec_tick"]
        self._audit_step(obs)
        if self.audit_result == "spoof_suspected":
            return self._spoof_step(obs, res)

        if (self.cfg.audit_ais and not self.audit_requested and self.locked_mmsi is not None
                and t >= self.cfg.audit_tick and res["sar_tasks_remaining"] > 0 and not res["pending_taskings"]):
            fix = self._target_fix_near(t)
            if fix is not None and t - max(self.ais_hist[self.locked_mmsi]) <= 1:
                self.audit_requested = True
                self.last_task_tick = t
                out.append(action("hunter", t, "task_sensor", {"sensor": "sar", "x_m": fix[0], "y_m": fix[1]}))
                return out
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
