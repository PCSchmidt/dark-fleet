"""Episode runner (harness). Allowed to use evaluation truth for scoring and traces."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import darkfleet_core as core
from darkfleet_core import evaluation as truth

from .policies import HunterBot, HunterConfig, RunnerPolicy


@dataclass
class EpisodeResult:
    scenario: dict
    replay: dict
    outcome: dict
    verified: dict
    trace: list[dict] = field(default_factory=list)


def make_scenario(seed: int, n_background: int = 20, duration_ticks: int = 144, sar_tasks: int = 6) -> dict:
    return json.loads(core.generate_scenario(seed, n_background, duration_ticks, sar_tasks))


def play(scenario: dict, runner: RunnerPolicy, hunter: HunterBot, record_trace: bool = False) -> EpisodeResult:
    w = core.World(json.dumps(scenario))
    trace: list[dict] = []
    while not w.ended:
        obs_r = json.loads(w.observe("runner"))
        obs_h = json.loads(w.observe("hunter"))
        for a in runner.act(obs_r):
            w.submit_action(json.dumps(a))
        for a in hunter.act(obs_h):
            w.submit_action(json.dumps(a))
        if record_trace:
            t = json.loads(truth.runner_truth(w))
            est = hunter.estimate()
            trace.append({"tick": w.tick, "truth": (t["x_m"], t["y_m"]), "estimate": est})
        w.step(1)
    replay_text = w.replay()
    verified = json.loads(core.verify_replay(json.dumps(scenario), replay_text))
    return EpisodeResult(scenario, json.loads(replay_text), json.loads(w.outcome()), verified, trace)


def default_play(seed: int, runner_kind: str = "dark_run", tracker: str = "pf", sar_tasks: int = 6) -> EpisodeResult:
    scenario = make_scenario(seed, sar_tasks=sar_tasks)
    return play(scenario, RunnerPolicy(kind=runner_kind, seed=seed), HunterBot(HunterConfig(tracker=tracker, pf_seed=seed)))
