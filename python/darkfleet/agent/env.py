"""Headless Hunter environment for agents: a world, a scripted Runner, and a tool host.

This is the agent-as-player surface used by the MCP server, the S16 determinism check and the
injection suite. The Runner policy acts before each step exactly as in the bot episodes.
"""
from __future__ import annotations

import json

import darkfleet_core as core

from ..episode import make_scenario
from ..policies import RunnerPolicy
from .tools import ToolHost


class HunterEnv:
    def __init__(self, seed: int, runner_kind: str = "dark_run", n_background: int = 20, duration_ticks: int = 144,
                 sar_tasks: int = 6, rename_background=None):
        self.seed, self.runner_kind = seed, runner_kind
        self.scenario = make_scenario(seed, n_background, duration_ticks, sar_tasks, brief="profile")
        if rename_background is not None:  # injection suite: put attacker-controlled text in vessel names
            i = 0
            for v in self.scenario["entities"]["vessels"]:
                if not v["is_runner"]:
                    v["name"] = rename_background(i)
                    i += 1
        self.world = core.World(json.dumps(self.scenario))
        self.runner = RunnerPolicy(kind=runner_kind, seed=seed)
        self.rejections: list[str] = []
        self.host = ToolHost(self.hunter_obs, self.submit, tick_s=self.scenario["time"]["tick_s"], seed=seed)

    # -- plumbing -----------------------------------------------------------------------
    @property
    def tick(self) -> int:
        return self.world.tick

    @property
    def ended(self) -> bool:
        return bool(self.world.ended)

    def hunter_obs(self) -> dict:
        return json.loads(self.world.observe("hunter"))

    def runner_obs(self) -> dict:
        """Used only to feed the scripted Runner and by tests; never exposed to the Hunter side."""
        return json.loads(self.world.observe("runner"))

    def submit(self, action: dict) -> dict:
        doc = {**action, "role": "hunter", "tick": self.world.tick, "schema_version": "1.1.0"}
        code = self.world.submit_action(json.dumps(doc))
        if code != "OK":
            self.rejections.append(f"t{self.world.tick} {action.get('action_type')}: {code}")
        return {"code": code}

    def step(self, n: int = 1) -> None:
        for _ in range(max(1, int(n))):
            if self.ended:
                break
            for a in self.runner.act(self.runner_obs()):
                self.world.submit_action(json.dumps(a))
            self.world.step(1)
            self.host.refresh()

    def outcome(self) -> dict:
        return json.loads(self.world.outcome())

    def verify(self) -> dict:
        return json.loads(core.verify_replay(json.dumps(self.scenario), self.world.replay()))
