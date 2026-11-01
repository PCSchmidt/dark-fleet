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

    honest    AIS on for the whole run; steer straight to the delivery point (under rules v3: does no transfer).
    dark_run  go dark for a window and make one heading dogleg while dark (v3: go dark around the transfer instead).
    spoof     broadcast a constant offset position for the whole run.
    visible   rules v3 only: do the transfer with AIS on throughout.

    Under rules v3 (the scenario carries ``mission.transfer``) every kind except ``honest`` steers to the zone, stops in it
    until the transfer is complete, then continues to the delivery point.
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
        # drawn after the legacy values so rules-v2 behaviour is unchanged
        self.lead_ticks = self.rng.randint(5, 9)   # v3 dark_run: go dark this many ticks before reaching the zone
        self.tail_ticks = self.rng.randint(3, 7)   # ... and come back this many ticks after leaving it
        self._left_tick: int | None = None

    def _act_transfer(self, obs: dict, tr: dict) -> list[dict]:
        t = obs["tick"]
        own, mission = obs["own"], obs["mission"]["delivery"]
        done = tr["progress_ticks"] >= tr["required_ticks"]
        if done and self._left_tick is None:
            self._left_tick = t
        d = math.hypot(tr["x_m"] - own["x_m"], tr["y_m"] - own["y_m"])
        want = "on"
        if self.kind == "spoof":
            want = "spoof"
        elif self.kind == "dark_run":
            before = not done and d <= self.speed * 300.0 * self.lead_ticks
            after = done and t - (self._left_tick or t) < self.tail_ticks
            if before or after:
                want = "dark"
        out: list[dict] = []
        if want != self._ais_mode:
            params = {"mode": want}
            if want == "spoof":
                params.update({"offset_x_m": 20000.0, "offset_y_m": -10000.0})
            out.append(action("runner", t, "set_ais_mode", params))
            self._ais_mode = want
        if done:
            course, speed = _bearing_deg(mission["x_m"] - own["x_m"], mission["y_m"] - own["y_m"]), self.speed
        elif d <= 2000.0:
            course, speed = own["course_deg"], 0.0   # loiter in the zone
        else:
            course, speed = _bearing_deg(tr["x_m"] - own["x_m"], tr["y_m"] - own["y_m"]), self.speed
        if abs(speed - own["speed_mps"]) > 1e-9 or t % 3 == 0 or d > 2000.0:
            out.append(action("runner", t, "set_course_speed", {"course_deg": course, "speed_mps": speed}))
        return out

    def act(self, obs: dict) -> list[dict]:
        t = obs["tick"]
        tr = obs["mission"].get("transfer")
        if tr is not None and self.kind != "honest":
            return self._act_transfer(obs, tr)
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
    require_dark_ticks: int = 0      # rules v2: only accuse after the target was silent this long (0 = off, D1/D2 behaviour)


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
        self.max_silent = 0
        self.lock_dist_m: float | None = None
        self.lock_margin_m: float | None = None
        self.area: dict | None = None          # rules v3: where a transfer is expected (from the brief)
        self.cluster_tasks = 0

    # tracker access for evaluation
    def estimate(self) -> tuple[float, float] | None:
        return (self.pf if self.cfg.tracker == "pf" else self.dr).estimate()

    def _target_silent_for(self, obs: dict) -> int:
        fix = self.dr.last_fix_tick()
        return obs["tick"] - fix if fix is not None else 0

    # -- identification (profile briefing) ---------------------------------------------
    def _ingest_ais(self, obs: dict) -> None:
        if self.area is None:
            self.area = (obs.get("brief") or {}).get("transfer_area")
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
            dists = []
            for c in obs["contacts"]:
                if c["source"] != "ais" or c["age_ticks"] > 1:
                    continue
                d = math.hypot(c["x_m"] - lk["x_m"], c["y_m"] - lk["y_m"])
                dists.append(d)
                if d < best_d:
                    best, best_d = c["claimed_mmsi"], d
            self.locked_mmsi = best
            if best is not None:   # how ambiguous was the lock? (used by the claim calibrator)
                dists.sort()
                self.lock_dist_m = dists[0]
                self.lock_margin_m = (dists[1] - dists[0]) if len(dists) > 1 else 50000.0
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

    def _spoof_collect(self, obs: dict) -> None:
        """Add SAR detections that sit where AIS position + the estimated offset predicts."""
        t = obs["tick"]
        for e in sorted(self.pending_execs):
            if e <= t and self.spoof_offset is not None:
                fix = self._target_fix_near(e)
                if fix is None:
                    continue
                px, py = fix[0] + self.spoof_offset[0], fix[1] + self.spoof_offset[1]
                for c in self._sar_large_at(obs, e):
                    if math.hypot(c["x_m"] - px, c["y_m"] - py) <= self.cfg.spoof_gate_m:
                        self.spoof_contacts[c["contact_id"]] = (e, c["x_m"], c["y_m"])

    def _spoof_step(self, obs: dict, res: dict) -> list[dict]:
        """Follow the vessel at AIS position + estimated offset; claim on a two-pass SAR chain."""
        t = obs["tick"]
        out: list[dict] = []
        self._spoof_collect(obs)
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

        fix_t = self.dr.last_fix_tick()
        if fix_t is not None:
            self.max_silent = max(self.max_silent, t - fix_t)
        res = obs["resources"]
        for p in res["pending_taskings"]:
            self.pending_execs.add(p["exec_tick"])
        if self.audit_requested and self.audit_exec is None and res["pending_taskings"]:
            self.audit_exec = res["pending_taskings"][-1]["exec_tick"]
        self._audit_step(obs)
        if self.area is not None:
            return self._act_v3(obs, res)
        if self.audit_result == "spoof_suspected":
            return self._spoof_step(obs, res)

        out = self._audit_request(obs, res)
        if out:
            return out
        est = self.estimate()

        chain = self._best_chain(obs, est)
        if self.cfg.require_dark_ticks and self.max_silent < self.cfg.require_dark_ticks:
            chain = None  # nothing yet suggests this vessel has done anything wrong
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

    def _audit_request(self, obs: dict, res: dict) -> list[dict]:
        t = obs["tick"]
        if (self.cfg.audit_ais and not self.audit_requested and self.locked_mmsi is not None
                and t >= self.cfg.audit_tick and res["sar_tasks_remaining"] > 0 and not res["pending_taskings"]):
            fix = self._target_fix_near(t)
            if fix is not None and t - max(self.ais_hist[self.locked_mmsi]) <= 1:
                self.audit_requested = True
                self.last_task_tick = t
                return [action("hunter", t, "task_sensor", {"sensor": "sar", "x_m": fix[0], "y_m": fix[1]})]
        return []

    # -- rules v3: catch the transfer --------------------------------------------------------
    # The mission now requires the Runner to loiter in a zone. A claim is grounded once the target has been *seen* in the
    # same place twice, at least ``grounds_ticks`` apart, inside the briefed area, with at least one of the sightings a SAR
    # detection (the adjudicator needs a detection in the zone). Two sightings at one spot imply it stayed there.
    STATIONARY_GATE_M = 1000.0   # two sightings this close, grounds_ticks apart, are one stationary vessel
    EXPLAIN_GATE_M = 800.0   # a SAR detection is another ship's only if its AIS sits this close (3 sigma of both sensors)

    def _off(self) -> tuple[float, float]:
        return self.spoof_offset or (0.0, 0.0)

    def _in_area(self, x: float, y: float) -> bool:
        a = self.area
        return math.hypot(x - a["x_m"], y - a["y_m"]) <= a["radius_m"] + 5000.0

    def target_sightings(self, obs: dict) -> list[dict]:
        """Everything attributable to the target: its (de-spoofed) AIS track, and the large SAR contacts it explains."""
        t, off = obs["tick"], self._off()
        hist = self.ais_hist.get(self.locked_mmsi or "", {})
        ais_ticks = sorted(hist)
        pts = [{"tick": k, "x": hist[k][0] + off[0], "y": hist[k][1] + off[1], "src": "ais", "cid": None} for k in ais_ticks]
        gate = self.cfg.spoof_gate_m if self.spoof_offset else self.cfg.audit_gate_m
        for c in obs["contacts"]:
            if c["source"] != "sar" or c["size_class"] != "large":
                continue
            tc = t - c["age_ticks"]
            near = [k for k in ais_ticks if abs(k - tc) <= 1]
            if near:   # the target was broadcasting then: a detection counts only if it sits under that position
                k = min(near, key=lambda k: abs(k - tc))
                if math.hypot(c["x_m"] - hist[k][0] - off[0], c["y_m"] - hist[k][1] - off[1]) > gate:
                    continue
            else:      # the target was silent: the detection must be reachable from its last fix and not another ship's AIS
                prior = [k for k in ais_ticks if k < tc]
                if not prior:
                    continue
                k0 = prior[-1]
                reach = 8.5 * (tc - k0) * self.cfg.tick_s + 3.0 * c["sigma_m"] + 2000.0
                if math.hypot(c["x_m"] - hist[k0][0] - off[0], c["y_m"] - hist[k0][1] - off[1]) > reach:
                    continue
                if any(abs(s - tc) <= 1 and math.hypot(p[0] - c["x_m"], p[1] - c["y_m"]) <= self.EXPLAIN_GATE_M
                       for m, h in self.ais_hist.items() if m != self.locked_mmsi for s, p in h.items()):
                    continue
            pts.append({"tick": tc, "x": c["x_m"], "y": c["y_m"], "src": "sar", "cid": c["contact_id"]})
        return pts

    def _cross_track_m(self, x: float, y: float) -> float:
        """Distance from (x, y) to the line through the target's last two AIS fixes: a loiterer that sits on the target's
        heading is a better candidate than one off to the side."""
        hist = self.ais_hist.get(self.locked_mmsi or "", {})
        ticks = sorted(hist)
        if len(ticks) < 2:
            return 0.0
        last = ticks[-1]
        old = [k for k in ticks if last - k >= 3]
        if not old:
            return 0.0
        (x1, y1), (x0, y0) = hist[last], hist[old[-1]]
        n = math.hypot(x1 - x0, y1 - y0)
        if n < 1.0:
            return 0.0
        return abs((x1 - x0) * (y0 + self._off()[1] - y) - (x0 + self._off()[0] - x) * (y1 - y0)) / n

    def _stationary_pair(self, pts: list[dict]) -> tuple[dict, dict] | None:
        need = self.area["grounds_ticks"]
        inside = [p for p in pts if self._in_area(p["x"], p["y"])]
        best = None
        for s in (p for p in inside if p["src"] == "sar"):
            for q in inside:
                if q is s:
                    continue
                a, b = (s, q) if s["tick"] <= q["tick"] else (q, s)
                span = b["tick"] - a["tick"]
                if span >= need and math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= self.STATIONARY_GATE_M:
                    key = (round(self._cross_track_m(b["x"], b["y"]) / 1500.0), -span)   # nearer the heading line first, then longer
                    if best is None or key < best[0]:
                        best = (key, a, b)
        return None if best is None else (best[1], best[2])

    def v3_evidence(self, obs: dict) -> list[str] | None:
        """Contact ids to file, or None if the target has not yet been seen to loiter."""
        if self.area is None or self.locked_mmsi is None:
            return None
        pts = self.target_sightings(obs)
        pair = self._stationary_pair(pts)
        if pair is None:
            return None
        a, b = pair
        sar = sorted((p for p in pts if p["src"] == "sar" and math.hypot(p["x"] - b["x"], p["y"] - b["y"]) <= self.STATIONARY_GATE_M),
                     key=lambda p: p["tick"])
        ids = [p["cid"] for p in sar]
        ais = [c for c in obs["contacts"] if c["source"] == "ais" and c["claimed_mmsi"] == self.locked_mmsi]
        if ais:
            ids.append(ais[0]["contact_id"])
        ids = list(dict.fromkeys(ids))
        return ids if len(ids) >= 2 else None

    def v3_next_task(self, obs: dict, res: dict) -> tuple[float, float, str] | None:
        t = obs["tick"]
        if res["sar_tasks_remaining"] < 1 or res["pending_taskings"] or t - self.last_task_tick < 4:
            return None
        hist = self.ais_hist.get(self.locked_mmsi or "", {})
        if not hist:
            return None
        off = self._off()
        ais_ticks = sorted(hist)
        last_ais = ais_ticks[-1]
        sar = [p for p in self.target_sightings(obs) if p["src"] == "sar"]
        if t - last_ais <= 1:      # broadcasting: if it has stopped in the area, put a SAR pass on it
            x, y = hist[last_ais][0] + off[0], hist[last_ais][1] + off[1]
            recent = [hist[k] for k in ais_ticks if k >= t - 3]
            stopped = (len(recent) >= 3 and self._in_area(x, y)
                       and all(math.hypot(r[0] + off[0] - x, r[1] + off[1] - y) <= 1500.0 for r in recent))
            if stopped and self.cluster_tasks < 2 and not any(math.hypot(p["x"] - x, p["y"] - y) <= self.STATIONARY_GATE_M for p in sar):
                self.cluster_tasks += 1
                return x, y, "target has stopped in the briefed area"
            return None
        silent = t - last_ais
        if silent < self.cfg.silent_ticks_before_tasking:
            return None
        recent_sar = [p for p in sar if p["tick"] > last_ais and t - p["tick"] <= self.area["grounds_ticks"] + 3 and self._in_area(p["x"], p["y"])]
        if recent_sar:             # a sighting inside the area: look again at the same spot after the grounds interval
            c = max(recent_sar, key=lambda p: p["tick"])
            if t + 3 >= c["tick"] + self.area["grounds_ticks"]:
                return c["x"], c["y"], "second look at a vessel seen in the area"
            return None
        est = self.estimate()
        return None if est is None else (est[0], est[1], "target silent; tracker estimate")

    def _act_v3(self, obs: dict, res: dict) -> list[dict]:
        t = obs["tick"]
        out = self._audit_request(obs, res)
        if out:
            return out
        ids = self.v3_evidence(obs)
        if ids:
            self.claimed = True
            return [action("hunter", t, "file_claim", {"contact_id": ids[-1], "evidence": ids})]
        task = self.v3_next_task(obs, res)
        if task is None:
            return []
        self.last_task_tick = t
        return [action("hunter", t, "task_sensor", {"sensor": "sar", "x_m": task[0], "y_m": task[1]})]

    # -- evidence linking ---------------------------------------------------------------
    def _best_chain(self, obs: dict, est: tuple[float, float] | None) -> list[str] | None:
        if self.area is not None:
            return self.v3_evidence(obs)
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
