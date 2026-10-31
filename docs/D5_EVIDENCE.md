# D5 Evidence - Machine-Learning Expansion

**Gate:** D5 (see [SPEC.md](./SPEC.md) section 14): "anomaly detector, learned Runner via self-play, claim calibrator, model cards | ML.md release checklist satisfied per model".
**Date:** 2026-10-31. **Training commit:** `1f76b07fa0cf` (clean working tree for code and configs).
**Status:** three models built, evaluated on locked seeds, documented; the checklist is met for each with the exceptions in the table. **Scope is smaller than the original plan** (no deep self-play, no sequence model). **Two of the three results are mainly findings about the simulator and the game, not capable models**; read "What D5 found" first.

## What D5 found
1. **Honesty dominates: the game gives a Runner no reason to deceive.** A learned Runner searched against Hunters converged to almost no deception and scores about 100 against every fixed Hunter, versus about 12 for the scripted dark-running Runner. Under rules v2 an innocent Runner can never be successfully accused, and nothing in the mission requires going dark. Only a best-response Hunter that accuses early dents it, and only because the policy still contains a couple of ticks of dark and spoof. **Recommendation: rules v3**, in which completing the mission requires a rule-breaking act (for example a required loitering rendezvous at a ship-to-ship transfer point, `FR-007`), so deception is worth learning and honest-but-unsuccessful play is not an equilibrium. This affects every earlier scenario and result, so I have not made it unasked. See [model-cards/learned-runner.md](./model-cards/learned-runner.md).
2. **The anomaly detector's near-perfect score is mostly a simulator artifact.** The Runner drives a straight line off the shipping lanes; location alone identifies it. Kinematic-only collapses to recall 0.58 when the Runner is slowed to ordinary speeds. See [model-cards/anomaly-detector.md](./model-cards/anomaly-detector.md).
3. **I leaked test data into training once and caught it.** The first calibrator run used overlapping seed ranges, which inflated its AUROC (0.92 reported, 0.87 true) and log loss (0.26 vs 0.34). It was found while writing the card, fixed, retrained, and the scripts now refuse overlapping splits (tested). The rest of the evidence uses the corrected run. See [model-cards/claim-calibrator.md](./model-cards/claim-calibrator.md).
4. **Calibration does not transfer across traffic density.** The released isotonic map is worse than the raw model when traffic doubles (ECE 0.067 vs 0.041).
5. **A calibrated claim gate gives a small, real gain.** `case_officer_cal` files claims only above 30% calibrated confidence: false accusations 6 vs 12 of 150 episodes, claim precision 87% vs 83% (intervals overlap), mean score +2.2 [+0.7, +4.0] on paired seeds, identical success count (125 of 150 both). It is slightly overconfident (mean stated confidence 0.92, accuracy 0.87).
6. **An unsupervised detector was useless.** The Isolation Forest scored recall 0 and PR-AUC 0.11-0.35 on the gap task. Kept as a negative result.

## ML.md section 8 checklist, per model
| Item | Anomaly detector | Claim calibrator | Learned Runner |
|---|---|---|---|
| 1. Config, seeds, commit, data hash recorded | Yes (`metrics.json` provenance: config hash, dataset digest, commit, clean flag, library versions) | Yes | Yes (digest covers policy and rounds; no dataset) |
| 2. One-command training reproduces metrics | **Yes: retrained from the clean commit, metrics identical** (same machine; other machines untested) | **Yes, identical** | **Yes, identical** (75 to 130 minutes depending on machine load) |
| 3. Held-out evaluation on locked seeds with CI | Yes (iid, density-and-outage shift, speed-mimic splits; episode-clustered bootstrap) | Yes (iid, dense-traffic shift) | Yes (60 seeds x 7 Hunters x 4 Runner policies, bootstrap and Wilson) |
| 4. ONNX export with parity test | Yes, max probability difference 2.1e-7 on 5,560 rows | Yes, trees 2.2e-7 and calibrated output 6.4e-6 on 674 rows | **Not applicable**: the policy is 17 numbers; replaced by load-and-replay tests |
| 5. Model card | [anomaly-detector.md](./model-cards/anomaly-detector.md) | [claim-calibrator.md](./model-cards/claim-calibrator.md) | [learned-runner.md](./model-cards/learned-runner.md) |
| 6. Negative and failed results documented | Yes (Isolation Forest; kinematic-only collapse; FPR above target at checkpoint 24) | Yes (leak and correction; shift; lock features add nothing) | Yes (honesty dominates; exploitability partly artifact) |

## Verification
| Check | Result |
|---|---|
| Python tests (ML tests: metrics, provenance, split guard, anomaly features and labels, ONNX end-to-end, calibrator runtime and artifact consistency, runner policy legality, determinism and JSON loading, published-artifact checks) | 95 Python tests pass in total (17 new in `test_ml.py`), none skipped, on Windows and Ubuntu in CI (commit `d8556d5`) |
| Existing agent and bench tests unchanged and passing | Yes (same run); 36 C++ test cases also pass |
| Earlier benchmark results unaffected by the new agent | **All 1,350 earlier episodes reproduced identically** in the 1,500-episode run |
| Every evaluation replay re-simulated | Runner evaluation 60/60 per cell; bench 1,500/1,500 |

## Design decisions worth knowing
- **Gradient boosting, not histogram boosting.** `skl2onnx` could not convert scikit-learn's `HistGradientBoostingClassifier` under the installed onnx version, so the plain `GradientBoostingClassifier` is used.
- **Parametric search, not PPO.** ML.md planned PPO self-play. No deep-learning stack is installed and, more importantly, finding 1 shows the reward structure, not the optimiser, is the limiting factor; a stronger optimiser would only learn honesty faster.
- **Labels from scripts.** Anomaly labels come from the Runner's script and calibrator labels from engine outcomes, never from anything a Hunter observes. MMSI and vessel name are not features.
- **Threshold 0.3 for the calibrated agent** was fixed from the payoff structure before any result was seen.
- **Dirty-tree flag** now covers code, configs and schemas only (docs edits do not count).

## Not done
- Sequence-model anomaly detector, deep self-play (PPO), Hunter planner learning, sensor-image models (ML.md sections 2, 3, 4, 6).
- The anomaly detector is not wired into any agent, and has not been tested against the learned Runner.
- No language model inside D5 itself; a hosted model was evaluated afterwards, see [LLM_AGENT.md](./LLM_AGENT.md). No human data.
- No cross-machine reproduction.

## Reproduce
```
python -m pip install -r python/requirements-dev.txt
PYTHONPATH=build/python:python python -m darkfleet.ml.anomaly    --config configs/anomaly.json    --out models/anomaly
PYTHONPATH=build/python:python python -m darkfleet.ml.calibrator --config configs/calibrator.json --out models/calibrator
PYTHONPATH=build/python:python python -m darkfleet.ml.runner_es  --config configs/runner.json     --out models/runner
PYTHONPATH=build/python:python python -m darkfleet.bench --seeds heldout --out docs/bench
python -m pytest python/tests -q
```
