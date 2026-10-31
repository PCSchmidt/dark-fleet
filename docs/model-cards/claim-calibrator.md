# Model card: claim calibrator

**Status:** trained, evaluated on locked seeds, exported to ONNX with a parity test, **integrated into the Case Officer as an option** (`case_officer_cal` in DarkFleet-Bench). **Date:** 2026-10-31.
**Files:** `models/calibrator/calibrator.onnx` (68 KB), `models/calibrator/calibration.json`, `models/calibrator/metrics.json`, `configs/calibrator.json`.
**License:** model files, card and results CC BY 4.0 (recorded choice; code is Apache-2.0).

## Correction recorded here
The first training run used seed ranges that overlapped (train 30000-33199 contained the validation and test seeds), so test episodes were also in training and the first test numbers were optimistic (AUROC 0.92, log loss 0.26). I found it while writing this card, fixed the ranges, retrained, and added a check (`assert_disjoint_splits`, plus tests) that makes the training scripts refuse overlapping ranges. All numbers below are from the corrected run.

## Intended use
Before a Hunter files a claim, estimate P(the claim is a correct, supported identification) so that a threshold can be a decision and calibration can be measured. Every agent before this model implicitly said "100%".

## Model
Gradient-boosted trees (150 trees, depth 3) over 17 features computed from the Hunter's own observation and its tracker (`claim_features`): number and kind of cited contacts, chain span, silence evidence, audit result, target-lock distance and ambiguity, traffic density, tracker spread, distance from the claimed contact to the tracker's estimate, SAR taskings left. The raw probability is mapped through a calibration map chosen on validation log loss among {identity, Platt, isotonic}; **isotonic was chosen** (validation log loss 0.229 vs 0.247 and 0.263). The ONNX file holds the trees; the map is in `calibration.json`.

## Data lineage
Synthetic. One row per episode in which a bot Hunter filed a claim; label = the outcome is `correct_supported`. Hunters cycle through four configurations (PF or dead reckoning, with and without the audit and the dark-evidence rule) and four scenario types (dark-running, honest, spoofing, dark-running in dense traffic), so wrong and unfounded claims occur. 1,500 particles, matching the Case Officer's analyst.
| Split | Seeds | Claims (correct) | Note |
|---|---|---|---|
| train | 30000-33199 | 1,764 (1,483) | 84.1% correct |
| val | 70000-70799 | 454 (381) | selects the calibration map |
| test_iid | 71000-72199 | 674 (543) | locked; 80.6% correct |
| test_shift | 73000-73599 | 518 (399) | dense traffic only (45 background vessels) |

## Results (held-out, 95% intervals by resampling episodes)
| Test split | Model | Log loss | Brier | ECE | AUROC |
|---|---|---|---|---|---|
| iid | "overconfident bot" (always 0.99) | 0.903 [0.767, 1.033] | 0.191 | 0.184 [0.155, 0.213] | 0.5 |
| iid | constant at the training base rate | 0.497 [0.447, 0.544] | 0.158 | 0.035 [0.005, 0.063] | 0.5 |
| iid | logistic regression | 0.375 [0.331, 0.417] | 0.118 | 0.037 [0.026, 0.071] | 0.832 |
| iid | GBT, raw | 0.328 [0.282, 0.371] | 0.100 | 0.045 [0.030, 0.071] | 0.871 |
| iid | **GBT + isotonic (released)** | 0.340 [0.279, 0.403] | 0.100 | 0.043 [0.026, 0.067] | 0.867 |
| shift | overconfident bot | 1.066 [0.906, 1.208] | 0.225 | 0.220 [0.185, 0.251] | 0.5 |
| shift | constant base rate | 0.556 [0.498, 0.607] | 0.182 | 0.070 [0.036, 0.101] | 0.5 |
| shift | GBT, raw | 0.433 [0.380, 0.486] | 0.134 | 0.041 [0.035, 0.080] | 0.790 |
| shift | GBT + isotonic (released) | 0.517 [0.426, 0.622] | 0.138 | 0.067 [0.049, 0.105] | 0.790 |

Reading:
- The model clearly beats the overconfident baseline on every metric (log loss 0.34 vs 0.90, ECE 0.043 vs 0.184) and beats the constant base rate on log loss (0.34 vs 0.50) because it discriminates (AUROC 0.87).
- Its calibration error is **not** distinguishable from the constant's (0.043 vs 0.035; intervals overlap heavily). The constant is already well calibrated when the test base rate (80.6%) is close to the training rate (84.1%). The model's benefit is discrimination, not better calibration.
- Isotonic and raw are indistinguishable in distribution (log loss 0.340 vs 0.328).

**Selective prediction (iid):** filing only when confidence is at least 0.5 keeps 86% of claims at 88.5% precision (base 80.6%); at 0.7, 76% of claims at 92.2%; at 0.9, 62% at 94.1%.

**Ablations (iid, log loss / AUROC; full model 0.340 / 0.867):** dropping tracker features 0.407 / 0.766 (the biggest loss); audit features 0.373 / 0.852; silence 0.357 / 0.849; lock features 0.328 / 0.877 (no loss).

## Negative and surprising results
- **The released calibration does not survive a change in traffic density.** On dense traffic the isotonic map is worse than the raw GBT (ECE 0.067 vs 0.041; log loss 0.517 vs 0.433) and AUROC falls from 0.87 to 0.79. Calibration fitted on one distribution should not be trusted on another; a deployment would need re-fitting per regime.
- Isotonic was chosen by the pre-set rule (best validation log loss). On held-out data the raw model is as good or better; I did not switch after seeing test results.
- The lock-ambiguity features I added specifically for this model contribute nothing measurable (ablation loss 0 on log loss).

## Limitations
- The labels are outcomes of bot Hunters' claims in this simulator; the 80-84% base rate reflects those bots, not human or LLM play.
- Features such as `lock_margin_km` depend on the bot's target-lock heuristic and would need redefining for another Hunter.
- Wrong claims are about 16-19% of rows, so probabilities at the low end are noisy.
- Not evaluated on human claims or a language-model agent.

## Use in DarkFleet-Bench
`case_officer_cal` files a claim only if the calibrated confidence is at least 0.3 (the break-even for a correct claim paying about +110 against about -40 for a wrong one; chosen before looking at results) and reports its confidence, which gives the benchmark its first calibration metric. See `docs/BENCH.md`.

## Reproduce
`PYTHONPATH=build/python:python python -m darkfleet.ml.calibrator --config configs/calibrator.json --out models/calibrator` (about 12 minutes on 22 workers). ONNX parity: raw probabilities within 2.2e-7, calibrated within 6.4e-6, on 674 rows.
