# Model card: learned Runner (self-play search)

**Status:** trained by population-based search with two rounds of best-response Hunters; evaluated on locked seeds against seen and unseen Hunters. **Not deep reinforcement learning, and the main finding is a problem with the game's incentives, not a strong Runner.** **Date:** 2026-10-31.
**Files:** `models/runner/runner_policy.json` (17 numbers), `models/runner/metrics.json` (all results, per-generation history, best-response Hunters), `configs/runner.json`.
**License:** policy file, card and results CC BY 4.0 (recorded choice; code is Apache-2.0).

## What it is
A 17-parameter evasion policy (`LearnedRunner`): when to go dark (two windows), a spoof window and offset, a dogleg, a lateral detour along the route, speed, pauses and heading jitter. It sees only its own role-filtered observation. The parameters are searched with the cross-entropy method (population 24, 5 elites, 10 generations per round, 6 fresh seeds per candidate per generation, 20 background vessels), reward = the engine's Runner score (delivered 100; accused wrongly or thinly 25-50; correctly identified 0).

**Self-play loop.** Round 0: train against {pf_greedy, pf_audit}. Then search a best-response Hunter (24 random configurations of the bot's thresholds, scored on 20 fresh seeds) and add it to the pool. Round 1: retrain from the round-0 policy against the enlarged pool, then search a second best response.

**ONNX is not applicable.** The policy is 17 numbers plus the loader in `runner_es.py`; there is no network to export. The parity check required by the checklist is replaced by: the JSON loads into the same policy (tested), a given policy plays a byte-identical replay twice (tested), and every evaluation replay re-simulates cleanly (all verified).

## Data lineage
No dataset: the policy is trained online in the simulator. Training seeds are 40000+100g+i (generation g, i < 6), best-response search seeds 60000-60019, held-out seeds 50000-50059 (60 seeds, 20 background vessels; 30 of them also with 45 vessels). None overlaps the development seeds (1-40) or the benchmark seeds (5000-5429).

## Results (held-out; n = 60 per cell; mean Runner score with 95% bootstrap interval)
Hunters `pf_greedy`, `pf_audit` were in training; `dr_rule`, `pf_audit_tuned` (different thresholds) and `case_officer` were not; `best_response_r0/r1` are the searched adaptive Hunters.
| Hunter | shortest path (honest) | scripted dark-run | random AIS | **learned** |
|---|---|---|---|---|
| pf_greedy (seen) | 100.0 | 13.3 [6.7, 20.8] | 13.8 | **100.0 [100, 100]** |
| pf_audit (seen) | 100.0 | 12.5 [5.8, 20.4] | 11.2 | **100.0** |
| dr_rule (unseen) | 99.2 | 28.3 [17.1, 39.6] | 12.9 | **99.2 [97.5, 100]** |
| pf_audit_tuned (unseen) | 100.0 | 12.1 | 10.0 | **98.3 [95.0, 100]** |
| case_officer (unseen) | 100.0 | 11.7 | 14.6 | **100.0** |
| best_response_r0 | 99.2 | 12.5 | 8.3 | **99.2 [97.5, 100]** |
| best_response_r1 (searched against the final policy) | 28.3 [25.4, 32.5] | 10.8 | 4.6 | **63.3 [51.7, 74.2]** |
With 45 background vessels the learned policy scores 96.7 against all four fixed Hunters. Delivery rate against the fixed Hunters is 98-100% and identification rate 0-2%; against `best_response_r1` delivery is 57% [44, 68] and identification 30%.

## The finding: honesty dominates
The learned policy converged to **almost no deception**: its first dark window lasts about 2 ticks and its spoof window about 8 (the search's mean sits just above the 1-tick threshold at which a window counts), and the other parameters sit near plain shortest-path driving. It scores about 100 against every fixed Hunter, matching the "shortest path" baseline and far above the scripted dark-running Runner (about 12). Under rules v2 an innocent Runner is never successfully accused, and nothing in the mission requires going dark, so the best thing a Runner can do in this game is **not break any rule**.

Consequences, stated plainly:
1. **This is a game-design flaw the learning exposed, not a clever Runner.** The Runner role has no reason to hide. A Runner that must perform a rule-breaking act to complete its mission (for example a required loitering rendezvous, `FR-007` ship-to-ship transfer) would make deception worth learning. I recommend that as the next rules change (v3) and have not made it.
2. **The "exploitability" result is partly an artefact.** The only Hunter that dents the learned Runner is `best_response_r1`, which accuses early and without the dark-evidence rule (`require_dark_ticks = 0`). It wins only because the learned policy's few ticks of dark and spoof technically count as violations; against the strictly honest shortest-path Runner the same Hunter scores -23 (it accuses an innocent). A fully honest Runner would be immune.
3. **The self-play loop ran as designed but found nothing deeper.** The best-response Hunter moved from late accusation in round 0 (`require_dark_ticks = 9`) to early accusation in round 1 (`0`), and the Runner retrained against the enlarged pool. I did not evaluate the round-0 policy separately, so I cannot say the second round made the Runner more robust. No mixed strategy appeared because the game applies no pressure toward one.

## Limitations
- Policy class is tiny and hand-designed; it cannot represent reactive behaviour (it ignores what it sees except its own position).
- Hunters are bots with three thresholds searched at random (24 candidates); a stronger best response (learning, planning) might beat the learned Runner.
- Exploitability is estimated from one best-response search per round, evaluated on 60 seeds.
- Evaluation Hunters share a code base with the training Hunters; "unseen" means different thresholds or logic, not a different kind of agent.
- No human or language-model Hunter was tested.
- Runner reward is the engine score only; no explicit penalty for being observed.

## Reproduce
`PYTHONPATH=build/python:python python -m darkfleet.ml.runner_es --config configs/runner.json --out models/runner` (about 75 minutes on 22 workers). Provenance (config hash, git commit, library versions) is in `metrics.json`.
