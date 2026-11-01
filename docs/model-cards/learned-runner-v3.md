# Model card: learned Runner, rules v3 (self-play search)

**Status:** four trained policies (generations A-D), evaluated on locked held-out seeds. **Not deep reinforcement learning.** Results and interpretation are in [RULES_V3.md](../RULES_V3.md); this card records what each artifact is. **Date:** 2026-11-01. **License:** policy files, card and results CC BY 4.0 (code is Apache-2.0).
**Files:** `models/runner_v3/` (A), `models/runner_v3_patched/` (B), `models/runner_v3_sweep/` (C), `models/runner_v3_vs_sweep/` (D); each holds `runner_policy.json` (11 numbers) and `metrics.json` (per-generation history, best-response Hunters, held-out results, provenance). Configs: `configs/runner_v3*.json`. Code: `python/darkfleet/ml/runner_v3.py`, `arms_race_v3.py`.

## What it is
An 11-parameter policy (`LearnedRunnerV3`) that sees only its own role-filtered observation: whether to make the transfer at all; how many ticks before reaching the zone to go dark (or spoof) and how many after finishing to stay hidden; the spoof offset; where in the zone to stop and how fast to creep (the zone allows up to 1 m/s); a lateral detour on the way in; speed; whether to run at full speed afterwards. The parameters are searched with the cross-entropy method (population 24-32, 5-6 elites, 8-12 generations per round, 6-8 fresh seeds per candidate per generation, 20 background vessels), reward = the engine's Runner score (completed transfer and delivery 100; delivery without transfer 20; wrongly or thinly accused 25-50; correctly identified 0). After each round a best-response Hunter is searched (24 random configurations of the scripted Hunter's thresholds, scored on 20 fresh seeds) and added to the pool.

| Generation | Config | Pool | Rounds x generations |
|---|---|---|---|
| A | `runner_v3.json` | `pf_greedy`, `pf_audit` | 3 x 8 |
| B | `runner_v3_patched.json` | adds `pf_audit_jump` | 3 x 8 |
| C | `runner_v3_sweep.json` | adds `pf_audit_sweep` | 3 x 8 |
| D | `runner_v3_vs_sweep.json` | `pf_audit_sweep` only | 2 x 12 |

No ONNX export: the policy is 11 numbers plus its loader. The parity check is replaced by tests that the file loads into the same policy, that a policy plays a byte-identical replay twice, and that every evaluation replay re-simulates cleanly.

## Data lineage
No dataset; trained online in the simulator. Training seeds are 40000+100g+i, best-response search seeds 60000-60019, held-out seeds 50000-50059 (60 seeds, 20 background vessels). None overlaps the development seeds (1-40) or the benchmark seeds (5000-5529). The Hunters in the pool and the evaluation set are scripted bots that share code with each other.

## Findings (details and table in RULES_V3.md)
Under rules v3 the same search that converged to honest driving under rules v2 finds and exploits deception: spoofing after the single early audit (A), a long dark window that outruns the particle filter (B), and a small spoof offset that evades the jump trigger and the AIS-to-radar association (D). Each is a blind spot in a scripted Hunter, patched or patchable with a small change; the searched Hunter `tuned` (stationary gate 2.5 km) halves D's score. Generation C shows a pool average can hide a policy that only beats weak opponents.

## Limitations
- Tiny, hand-designed policy class; no reactive behaviour (it ignores everything it sees except its own position and progress).
- One search seed per generation; no repeated-seed variance estimate of the training itself. Results are on 60 held-out seeds.
- The Hunters are my own bots. A Runner "winning" shows a gap in the bots, not that the game favours the Runner; a Hunter "winning" shows the patch, not that no exploit remains.
- No human or language-model Hunter was in any training pool; the language-model Hunter was evaluated against these policies separately (RULES_V3.md).
- Provenance in each `metrics.json` records the git commit and a dirty flag; the four searches ran on a working tree with uncommitted changes, so the flag reads dirty (commit `0c56c3a2b2f3` for generation A). **Generation A was re-run from the committed code (`6d7c775`) and reproduced exactly:** identical parameters, dataset digest and all held-out results. B, C and D were not re-run.

## Reproduce
`PYTHONPATH=build/python:python python -m darkfleet.ml.runner_v3 --config configs/runner_v3.json --out models/runner_v3` (about 8 minutes on 12 workers; 22 workers ran out of memory once on this machine). The table: `python -m darkfleet.ml.arms_race_v3`.
