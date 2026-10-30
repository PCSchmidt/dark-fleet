# Dark Fleet - Machine Learning Specification

**Status:** Draft v1 - design only
**Standard:** Every learned or statistical component below has: problem formulation, data, baseline, metrics, ablations, reproducible training, export, model card, and CI evaluation. Thresholds stated are *targets to be justified*, not established results.

## 0. Common infrastructure
| Item | Spec |
|---|---|
| Environment | Gymnasium-style wrappers around `hr-core` via Python bindings; vectorized headless environments; seeded and deterministic |
| Data generation | Scenario generator produces labelled synthetic tracks; train/val/test split **by seed** with a locked held-out test set |
| Training stack | PyTorch; config-driven (YAML), seeded, single-command reproduction (`make train-<model>`) |
| Tracking | Experiment tracker (self-hosted or file-based; `[VERIFY choice]`) logging config hash, git commit, data hash, metrics, artifacts |
| Export | ONNX for in-engine/in-sim inference; hash recorded in the model card; parity test between PyTorch and ONNX outputs within tolerance |
| Evaluation | Scripted evaluation harness; bootstrap confidence intervals; CI runs a small deterministic eval on every change and a full eval on release tags |
| Compute | Local RTX A4500 (16 GB); budget recorded per experiment |
| Documentation | `model-card.md` per model; negative results retained |

## 1. Hunter state estimation: multi-hypothesis vessel tracker (classical, baseline-first)
- **Problem:** estimate the posterior over the Runner's position/identity given sparse, noisy, delayed, partly deceptive observations (AIS, SAR/EO detections, RF fixes, port records).
- **Approach:** (a) Bayesian filter with particle representation and a vessel motion model constrained by land, bathymetry, fuel and speed; (b) multi-target data association (e.g. global-nearest-neighbour then multi-hypothesis tracking) across background traffic; (c) observation models from HRSensors including false alarms and missed detections.
- **Baselines:** last-known position + dead reckoning; random-walk prior.
- **Metrics:** posterior mass within R nm of truth over time; negative log-likelihood of truth; identity-association accuracy; calibration (reliability diagram, ECE); computation time.
- **Ablations:** number of particles; with/without motion constraints; with/without deception-aware observation model.
- **Targets (to justify):** beats dead reckoning on log-likelihood at every horizon; calibration slope within 0.8-1.2.

## 2. AIS anomaly detection
- **Problem:** flag vessels whose reporting or kinematics are inconsistent with normal behavior (gap anomalies, impossible kinematics, identity inconsistency, pattern-of-life breaks) while keeping false positives low, since innocent gaps are common.
- **Data:** synthetic tracks from the traffic generator with injected, labelled events (spoof, swap, dark, STS), plus a statistically fitted innocent-gap process; optional validation on public AIS samples (license permitting) for the *innocent* distribution only.
- **Models:** (a) rule/feature baseline; (b) gradient-boosted trees on engineered features (gap duration, location context, speed before/after, route deviation, historical gap rate); (c) sequence model (e.g. 1-D CNN or small Transformer) on track windows; (d) unsupervised autoencoder / isolation forest as a comparison.
- **Metrics:** precision-recall AUC, recall at fixed false-positive rate (operationally meaningful), per-event-type detection rate, calibration, detection latency, robustness to distribution shift (new regions, new weather).
- **Ablations:** feature groups; context features (location/class) on/off; synthetic-to-real gap analysis where permissible.
- **Honesty requirement:** report the synthetic-data limitation explicitly; do not claim real-world performance.

## 3. Runner policy (learned deception and planning)
- **Problem:** choose route, speed, AIS actions and STS location/time to complete delivery while minimizing identification, against the Hunter population.
- **Formulation:** partially observable Markov game; observation = Runner's role-filtered view; action = discrete high-level decisions per decision epoch (route segment, speed profile, AIS mode, rendezvous choice); reward = delivery value − detection/identification penalties − fuel/time costs.
- **Training:** PPO (or a comparable method) with self-play against a pool of Hunter policies (tracker-based, scripted, and past checkpoints); population-based evaluation to avoid cycling; curriculum from easy to hard weather/surveillance.
- **Baselines:** shortest-path runner; random-AIS runner; scripted heuristic runner.
- **Metrics:** delivery rate; identification rate; value per run; exploitability estimate (best-response Hunter trained against the fixed Runner); strategy diversity.
- **Ablations:** reward terms; population vs single opponent; action-space abstraction; observation features.
- **Risk:** exploiting simulator artifacts. Mitigation: exploit review, randomization of parameters, and held-out parameter sets.

## 4. Hunter policy (tasking optimization)
- **Problem:** allocate scarce sensor and asset actions to maximize information gain about the Runner.
- **Approach:** (a) greedy expected-information-gain over the tracker posterior; (b) Monte Carlo tree search / rollout planning; (c) RL fine-tuned policy.
- **Metrics:** time-to-identification; assets used; false-accusation rate; compared against the human owner (reported with caveats) and agent planners.
- **This is the principal comparison point for the LLM agents** (see AGENTS.md): do agents match the information-gain planner, and at what cost?

## 5. Evidence-claim calibration
- **Problem:** convert evidence sets into identification probabilities that are well calibrated, to make claims scoring fair and to give agents a calibrated reference.
- **Approach:** logistic/GBM classifier over evidence features trained on simulated cases; temperature-scaled; compare with a Bayesian model-based posterior.
- **Metrics:** log loss, Brier score, ECE, selective-prediction curves.

## 6. Sensor-image models (stretch)
- **Problem:** classify vessel type or detect STS pairs from synthetic EO/SAR chips; the generator provides unlimited labelled data.
- **Models:** small CNN/ViT with augmentation; evaluation on held-out ship models and sea states; domain-gap analysis (synthetic-only).
- **Metrics:** top-1 accuracy by GSD and sea state; confusion matrix; robustness to cloud/haze.
- **Status:** stretch; only if the visual pipeline exists.

## 7. LLM-related ML work
Covered in AGENTS.md: evaluation harness, calibration of verbalized confidence, citation faithfulness, injection robustness, cost/latency, and optional small-model fine-tuning for tool-call reliability (LoRA) with before/after benchmark results `[VERIFY feasibility on 16 GB]`.

## 8. Reproducibility and release checklist (per model)
1. Config, seeds, code commit and data hash recorded.
2. One-command training reproduces metrics within stated tolerance.
3. Held-out evaluation on locked seeds with CI.
4. ONNX export with parity test.
5. Model card: intended use, data lineage, metrics, baselines, limitations, license.
6. Negative and failed results documented.

## 9. Evaluation matrix (summary)
| Component | Primary metric | Baseline to beat | Dataset split |
|---|---|---|---|
| Tracker | Log-likelihood / posterior mass | Dead reckoning | Held-out seeds |
| Anomaly detector | Recall @ fixed FPR | Rule baseline | Held-out seeds + region shift |
| Runner policy | Value, exploitability | Shortest-path / heuristic | Held-out parameter sets |
| Hunter planner | Time-to-ID, false accusations | Greedy tasker | Held-out seeds |
| Claim calibrator | ECE, Brier | Bayesian posterior | Held-out cases |
| Agents | Bench metrics (AGENTS.md §8) | Rule-based analyst, planner | Held-out seeds |
