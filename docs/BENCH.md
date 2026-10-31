# DarkFleet-Bench v1 - methodology, results and what they do not show

**Gate:** D4 (see [SPEC.md](./SPEC.md) section 14). **Date:** 2026-10-31. **Bench version:** `darkfleet-bench-1`, rules `df-rules-2`.
**License:** results and data CC BY 4.0 (see `LICENSE-DOCS`); code Apache-2.0.
**Status:** v1 benchmark built and run on held-out seeds. **No language model was evaluated.** The only "agent" is the scripted Case Officer. The headline is therefore a baseline-and-harness result, not an LLM result.

Raw results: [bench/bench-result.v1.json](./bench/bench-result.v1.json) (schema: [`schemas/bench-result.v1.json`](../schemas/bench-result.v1.json)). Leaderboard: [bench/LEADERBOARD.md](./bench/LEADERBOARD.md).

## Reproduce (one command)
```
PYTHONPATH=build/python:python python -m darkfleet.bench --seeds heldout --out docs/bench
```
About six minutes on a 24-core machine (1,350 episodes). `--quick` runs two seeds per tier as a smoke test; `--agents a,b` selects agents; `--from-json` recomputes the tables from a saved result without playing anything. On Windows use `;` in `PYTHONPATH`.

## Protocol
- **Task:** the Hunter side of Dark Fleet against a scripted Runner, profile briefing (class and last-known area, no MMSI), 144 ticks of 5 minutes, six SAR taskings.
- **Tiers** (30 held-out seeds each): `standard` (dark-running tanker), `honest` (nothing to find: accusing anyone is wrong), `spoof` (false AIS position), `decoy_rich` (45 background vessels instead of 20), `injection_laced` (every background vessel name is a prompt-injection payload).
- **Seeds:** development seeds 1-40 were used for all tuning in D1-D3. The held-out seeds (5000-5029, 5100-5129, ...) are disjoint from them and were run once the code was frozen.
- **Success:** a correct, supported identification; in the `honest` tier, not accusing anyone. Scores are the engine's Hunter score (rules v2).
- **Statistics:** rates with Wilson 95% intervals; means with percentile-bootstrap 95% intervals over episodes; differences are paired by (tier, seed) and bootstrapped. Where an interval includes zero the table says "no".
- **Integrity:** every episode's replay is re-simulated from recorded inputs; 1,350 of 1,350 verified.
- **Agents:** `random` (3 repeats), `dr_rule` (dead reckoning), `pf_greedy` (particle filter, the reference), `pf_audit` (adds a one-pass AIS audit), `case_officer` (D3 agent, auto-approving, no model), and two ablations `case_officer_noaudit` and `case_officer_nodark` (no dark-evidence rule).

## Results (held-out, n = 30 per tier per agent)
| Agent | Overall success | standard | honest | spoof | decoy_rich | injection_laced | False accusations | Mean score [95% CI] |
|---|---|---|---|---|---|---|---|---|
| pf_audit | 84% [77-89] | 77% | 100% | 93% | 60% | 90% | 8% [5-13] | 76.9 [66.1, 87.2] |
| case_officer | 83% [77-88] | 73% | 100% | 93% | 60% | 90% | 8% [5-13] | 76.2 [65.7, 86.8] |
| case_officer_nodark | 79% [72-85] | 73% | 83% | 93% | 60% | 87% | 13% [8-19] | 74.1 [63.4, 85.1] |
| pf_greedy | 64% [56-71] | 73% | 100% | 0% | 57% | 90% | 11% [7-17] | 49.1 [38.2, 59.7] |
| case_officer_noaudit | 63% [55-71] | 73% | 100% | 0% | 57% | 87% | 11% [7-17] | 48.0 [37.0, 58.7] |
| dr_rule | 59% [51-67] | 63% | 100% | 0% | 53% | 80% | 7% [4-12] | 45.3 [35.3, 55.2] |
| random | 4% [2-6] | 0% | 17% | 0% | 1% | 0% | 78% [74-81] | -38.2 [-40.2, -35.9] |

Per-tier intervals (about +-17 points at n = 30) are in the leaderboard file. "Overall" pools tiers equally and is not a difficulty-weighted score.

Paired differences, same tier and seed:
| Comparison | Success diff [95% CI] | Score diff [95% CI] |
|---|---|---|
| pf_audit vs pf_greedy | +0.20 [+0.13, +0.27] | +27.8 [+18.3, +37.7] |
| case_officer vs pf_greedy | +0.19 [+0.12, +0.27] | +27.1 [+17.7, +37.2] |
| dr_rule vs pf_greedy | -0.05 [-0.08, -0.01] | -3.8 [-8.6, +0.5] (includes 0) |
| case_officer_noaudit vs case_officer | -0.20 [-0.27, -0.13] | -28.2 [-38.1, -18.7] |
| case_officer_nodark vs case_officer | -0.04 [-0.07, -0.01] | -2.1 [-4.6, -0.4] |

## What the results say
1. **The audit pass is the whole story for spoofing.** Without it the particle-filter bot and the Case Officer win 0 of 30 spoof episodes (they trust the broadcast position); with it they win 28 of 30. The no-audit Case Officer matches `pf_greedy` almost exactly (paired difference -0.01, interval touching zero).
2. **The scripted Case Officer is not a better analyst than the bot it wraps.** `case_officer` and `pf_audit` are within one percentage point of each other. This is by construction: it reuses the same tracker, audit and chain logic and adds a checked, explainable interface. The benchmark records no skill gain from "being an agent". This is a negative result for the idea that the agent layer improves play, with the caveat that no language model was involved.
3. **The dark-evidence rule matters for innocents.** Removing it raises false accusations from 8% to 13% and drops honest-tier success from 100% to 83% (5 of 30 honest runs wrongly accused), at a small paired cost overall.
4. **A particle filter is slightly better than dead reckoning at the decision level.** Success is higher by 5 points (interval excludes zero); the score difference is not distinguishable from zero. This agrees with the D1 tracker study at the position level.
5. **Dense traffic is harder, but I cannot prove the cause.** `decoy_rich` success is 53-60% against 63-77% in `standard`, and wrong-identity outcomes are more frequent (7-9 of 30 for the PF agents). The tiers use different seeds and the intervals overlap, so this is descriptive. A plausible reason is that picking the AIS vessel nearest the last-known area fails more often with more vessels; I did not test it.
6. **Random play is clearly bad:** 78% of random episodes end in a false accusation, so the scoring punishes guessing as intended.

## Injection
- **Tool layer (static suite, [spike-results/S17-static.json](./spike-results/S17-static.json)):** 125 payloads, 2,000 simulated compromised-agent attacks, 0 succeeded.
- **Paired utility under attack:** 30 standard-tier seeds with and without payloads as vessel names. `pf_greedy` and `case_officer` produced identical outcomes and identical final-state hashes in 30 of 30 pairs (success 22/30 in both). This is expected, not impressive: no agent here reads vessel names to decide anything, so there is nothing for an injection to redirect.
- **What is not measured:** whether a language model that reads these names is misled, adaptive attackers, and false refusals. The `injection_laced` tier exists so that a future model-driven agent has the scenario ready; for the present agents it is a control.

## Reproducibility (spike S19)
Three independent runs of the full held-out benchmark produced the same digest (`a389719070ae7a34...`, SHA-256 over every episode record except wall-clock time) with 1,350 episodes each. See [spike-results/S19.json](./spike-results/S19.json). The agents are deterministic given a seed; this does not test sampling variance because no sampled model is used. A clean-clone reproduction check is recorded in D4 evidence below.

## Limits (read before quoting any number)
- **No LLM or local model was evaluated** (spike S15 not done); no human arm (owner playtest pending); no calibration (the agents state no confidence); no dollar cost (no priced model); no weather or cloud tiers (mechanic not built).
- **One scripted Runner family** on one route geometry. Agents were developed against the same three Runner scripts they are tested on, though on disjoint seeds, so generalisation to a learned or human Runner is untested.
- **Tier comparisons use different seed sets**, and pooled success weights tiers equally; both are descriptive.
- **Timing is not a latency benchmark.** Episodes ran 22 at a time on one machine with multithreaded numpy; `ms/episode` in the leaderboard reflects that contention.
- **The random baseline samples a generous region** around the true route, which flatters it. It still scores 4%.
- The scoring function is the author's design and rules v2 was introduced during this project; scores are not comparable with earlier evidence files (rules v1).
- Sample sizes are modest (30 per tier). Differences of a few episodes are noise.

## D4 gate evidence
| Gate criterion | Status |
|---|---|
| Benchmark with baselines, ablations, held-out seeds, CIs | Done (above) |
| Agent results with CIs | Done for the scripted agent; **no model-driven agent exists** |
| Injection suite | Done at the tool layer (static); adaptive attacker and model susceptibility not done |
| Published results, including negative ones | Items 2 and the limits above are negative or null results |
| Reproducible with one command | Yes; three-run digest identical; clean-clone check below |
| Methodology write-up | This file |

Clean-clone reproduction: see the end of this file once recorded.
