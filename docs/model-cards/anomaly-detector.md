# Model card: AIS gap anomaly detector

**Status:** trained, evaluated on locked seeds, exported to ONNX with a parity test. **Not integrated into any agent or bot yet.** **Date:** 2026-10-31.
**Files:** `models/anomaly/anomaly.onnx` (108 KB), `models/anomaly/metrics.json` (all numbers and provenance), `configs/anomaly.json`.
**License:** model file, card and results CC BY 4.0 (a choice recorded here; the code is Apache-2.0).

## Intended use
Given an AIS vessel that has just gone silent, estimate the probability that this silence is the target deliberately going dark rather than an innocent fault. Decisions are made at fixed silence checkpoints (6, 12, 18, 24 ticks of 5 minutes) from what was reported *before* the gap. Use: research on synthetic Dark Fleet scenarios. **Not for real vessels or real AIS data.**

## Model
Gradient-boosted trees (scikit-learn `GradientBoostingClassifier`, 200 trees, depth 3) on 13 features: speed, speed variability, mean heading (sin, cos), turn rate and straightness over the last 8 reports (kinematic); position, distance from the briefed last-known area, gap start tick, earlier gaps, track length (context); and the silence so far. MMSI and vessel name are not features. Exported to ONNX (opset 17).

## Data lineage
Synthetic. Passive-observer episodes from the Dark Fleet generator; the Hunter's AIS stream is turned into one row per (gap, checkpoint reached). Label = the gap is the scripted Runner's dark window; all other gaps (innocent outages of 6-29 ticks, random 3+ tick drop-outs, honest-Runner drop-outs) are negatives. Runner dark windows last 40-70 ticks.
| Split | Seeds | Episodes | Rows (positives) | Note |
|---|---|---|---|---|
| train | 10000-10899 | 900 | 12,467 (2,396) | |
| val | 11000-11299 | 300 | 4,174 (796) | thresholds and the rule baseline are set here |
| test_iid | 20000-20399 | 400 | 5,560 (1,068) | locked |
| test_shift | 21000-21399 | 400 | 21,392 (1,060) | 45 background vessels, innocent-outage probability 0.6 |
| test_mimic | 22000-22399 | 400 | 6,859 (1,064) | Runner slowed to 75% of its normal speed |
Episode mix: two-thirds dark-running Runner, one-third honest. The dataset digest is in `metrics.json`.

## Results (held-out, 95% intervals by resampling episodes)
Operating point: threshold chosen on validation negatives for a 5% false-positive rate, applied unchanged to the test split.
| Test split | Silence checkpoint | GBT recall at ~5% FPR | GBT test FPR | GBT PR-AUC | Rule baseline recall |
|---|---|---|---|---|---|
| iid | 6 / 12 / 18 / 24 | 1.00 [1.00, 1.00] at all four | 4.0% / 4.4% / 4.4% / 7.2% | 1.00 | 0 / 0 / 0 / 0 |
| shift | 6 / 12 / 18 / 24 | 1.00 at all four | 3.9% / 4.3% / 4.4% / 7.0% | 0.98-0.99 | 0 |
| mimic | 6 / 12 / 18 / 24 | 0.99 [0.98, 1.00] / 0.99 / 0.99 / 1.00 | 3.0-5.5% | 0.95 / 0.99 / 0.99 / 1.00 | 0 |

**Baselines.** The *gap-length rule* ("flag once the silence reaches 29 ticks", the length exceeded by 5% of innocent gaps on validation) catches nothing before tick 29, so the model decides 23 ticks earlier at the first checkpoint. A logistic regression reaches the same recall at the 5% FPR operating point but with lower PR-AUC (0.87-0.96 iid). An **Isolation Forest** trained on innocent gaps only is useless here: recall 0 and PR-AUC 0.11-0.35 (a negative result, kept).

**Ablations (the important ones).** On the iid split every feature group alone is enough: kinematic-only recall 1.00 [0.99, 1.00], context-only 1.00. On the *mimic* split, kinematic-only collapses to recall 0.58 [0.51, 0.63] at checkpoint 6 and 0.64 at 12 and 18, and 0.04 at checkpoint 24 (PR-AUC 0.50-0.69), while the full model keeps 0.99. So the model is mostly recognising *where the Runner is* (off the shipping lanes, near the briefed area), not *how its silence differs*.

## Limitations (read these)
- **A near-perfect score here reflects a gap in the simulator, not a solved problem.** The Runner takes a straight route away from the shipping lanes; background vessels follow lanes or wander. Location alone separates them. The detector learned that. It does not show that a real dark-fleet detector is easy.
- The mimic test removes only the speed difference. A Runner that also hugged a lane would likely defeat this model; that test is not run.
- Test FPR at checkpoint 24 is 7.2% against a 5% target (the validation-chosen threshold did not transfer exactly).
- Labels come from the Runner's script, so they are exact, which real AIS data never gives you.
- The false-positive rate is per gap, not per vessel-day; operational false-alarm load is not estimated.
- One Runner behaviour family (dark window plus dogleg). The learned Runner (see its card) was **not** used to generate training data; testing this detector against it is the obvious next step.
- No sequence model was trained (ML.md lists one); no spoof detection (the only spoof the simulator has is a constant offset, which AIS history alone cannot reveal).

## Reproduce
`PYTHONPATH=build/python:python python -m darkfleet.ml.anomaly --config configs/anomaly.json --out models/anomaly` (about 3 minutes on 22 workers). Provenance in `metrics.json`: config hash, dataset digest, git commit and dirty flag, library versions. ONNX parity: maximum probability difference 2.1e-7 on 5,560 rows.
