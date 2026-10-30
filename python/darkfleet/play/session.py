"""A human-playable session: one human role against a bot, driven through the same
role-filtered observation/action documents as everything else.

The session object is the *harness* side. What it returns to the client while the episode runs
is the human's role-filtered observation plus derived aids built from that same observation.
Truth (the Runner's true track, scoring detail) is only released after the episode has ended.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

import numpy as np

import darkfleet_core as core
from darkfleet_core import evaluation as truth

from ..episode import make_scenario
from ..policies import HunterBot, HunterConfig, RunnerPolicy

RUNNER_KINDS = ("dark_run", "honest", "spoof")


@dataclass
class Session:
    role: str                      # the human's role: "hunter" or "runner"
    seed: int
    runner_kind: str = "dark_run"  # the bot Runner's behaviour when the human plays Hunter
    scenario: dict = field(init=False)
    world: object = field(init=False)
    bot: object = field(init=False)
    analyst: HunterBot | None = field(init=False, default=None)
    track: list = field(init=False, default_factory=list)   # true Runner track, kept server-side
    rejections: list = field(init=False, default_factory=list)

    def __post_init__(self):
        if self.role not in ("hunter", "runner"):
            raise ValueError("role must be 'hunter' or 'runner'")
        if self.runner_kind not in RUNNER_KINDS:
            raise ValueError(f"runner_kind must be one of {RUNNER_KINDS}")
        self.scenario = make_scenario(self.seed, brief="profile")
        self.world = core.World(json.dumps(self.scenario))
        if self.role == "hunter":
            self.bot = RunnerPolicy(kind=self.runner_kind, seed=self.seed)
            # Analyst aid: a tracker fed only with the human's own observations (never acts).
            self.analyst = HunterBot(HunterConfig(pf_seed=self.seed, n_particles=1500))
        else:
            self.bot = HunterBot(HunterConfig(pf_seed=self.seed, audit_ais=True))
        self._record_truth()

    # -- helpers ------------------------------------------------------------------------
    def _obs(self, role: str) -> dict:
        return json.loads(self.world.observe(role))

    def _record_truth(self) -> None:
        t = json.loads(truth.runner_truth(self.world))
        self.track.append([self.world.tick, t["x_m"], t["y_m"]])

    def _feed_analyst(self, obs: dict) -> dict | None:
        a = self.analyst
        if a is None:
            return None
        a._ingest_ais(obs)
        o = a._resolve_brief(obs)
        a.dr.update(o)
        a.pf.update(o)
        est = a.pf.estimate()
        if est is None:
            return {"locked": a.locked_mmsi, "estimate": None, "spread_m": None, "particles": []}
        n = len(a.pf.x)
        idx = np.random.default_rng(0).choice(n, size=min(250, n), replace=False)
        return {"locked": a.locked_mmsi, "estimate": list(est), "spread_m": a.pf.spread_m(),
                "dead_reckoning": a.dr.estimate(),
                "particles": [[float(a.pf.x[i]), float(a.pf.y[i])] for i in idx]}

    # -- API ----------------------------------------------------------------------------
    def state(self) -> dict:
        obs = self._obs(self.role)
        out = {"role": self.role, "seed": self.seed, "ended": bool(self.world.ended),
               "duration_ticks": self.world.duration_ticks, "tick_s": self.scenario["time"]["tick_s"],
               "observation": obs, "rejections": self.rejections[-20:]}
        if self.role == "hunter":
            out["analyst"] = self._analyst_cache
        if self.world.ended:
            out["debrief"] = self.debrief()
        return out

    _analyst_cache: dict | None = None

    def refresh_aids(self) -> None:
        if self.role == "hunter":
            self._analyst_cache = self._feed_analyst(self._obs("hunter"))

    def act(self, actions: list[dict]) -> list[dict]:
        """Submit the human's actions. Returns one result per action."""
        results = []
        for a in actions:
            a = {**a, "role": self.role, "tick": self.world.tick, "schema_version": "1.1.0"}
            code = self.world.submit_action(json.dumps(a))
            results.append({"action_type": a.get("action_type"), "code": code})
            if code != "OK":
                self.rejections.append({"tick": self.world.tick, "action_type": a.get("action_type"), "code": code})
        return results

    def step(self, n: int = 1) -> None:
        for _ in range(max(1, min(int(n), 144))):
            if self.world.ended:
                break
            bot_role = "runner" if self.role == "hunter" else "hunter"
            for a in self.bot.act(self._obs(bot_role)):
                self.world.submit_action(json.dumps(a))
            self.world.step(1)
            self._record_truth()
            self.refresh_aids()

    def debrief(self) -> dict:
        replay_text = self.world.replay()
        verified = json.loads(core.verify_replay(json.dumps(self.scenario), replay_text))
        d = {"outcome": json.loads(self.world.outcome()), "verification": verified,
             "true_track": self.track, "replay_hash": json.loads(replay_text)["content_hash"],
             "delivery": self.scenario["mission"]["delivery"]}
        if self.role == "hunter" and self.bot is not None:
            d["runner_policy"] = self.runner_kind
        if self.role == "runner":
            d["hunter_notes"] = list(getattr(self.bot, "notes", []))
        return d

    def replay_text(self) -> str:
        if not self.world.ended:
            raise PermissionError("replay is released when the episode has ended")
        return self.world.replay()


def new_session(role: str, seed: int | None = None, runner_kind: str | None = None) -> Session:
    rng = random.Random()
    seed = seed if seed is not None else rng.randint(1, 2**31 - 1)
    kind = runner_kind or rng.choice(RUNNER_KINDS)
    s = Session(role=role, seed=seed, runner_kind=kind)
    s.refresh_aids()
    return s
