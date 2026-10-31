# DarkFleet-Bench leaderboard (darkfleet-bench-1, seeds: heldout, rules df-rules-2)

Success = correct supported identification (honest tier: no accusation). Brackets are 95% intervals (Wilson for rates). Episodes per agent per tier: standard 30, honest 30, spoof 30, decoy_rich 30, injection_laced 30.

| Agent | Overall success | standard | honest | spoof | decoy_rich | injection_laced | False accusations | Mean score [95% CI] | Ticks to ID | SAR used | ms/episode |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pf_audit | 84% [77-89] | 77% [59-88] | 100% [89-100] | 93% [79-98] | 60% [42-75] | 90% [74-97] | 8% [5-13] | 76.9 [66.1, 87.2] | 21 | 2.1 | 5783 |
| case_officer | 83% [77-88] | 73% [56-86] | 100% [89-100] | 93% [79-98] | 60% [42-75] | 90% [74-97] | 8% [5-13] | 76.2 [65.7, 86.8] | 21 | 2.1 | 9714 |
| case_officer_nodark | 79% [72-85] | 73% [56-86] | 83% [66-93] | 93% [79-98] | 60% [42-75] | 87% [70-95] | 13% [8-19] | 74.1 [63.4, 85.1] | 21 | 2.1 | 7439 |
| pf_greedy | 64% [56-71] | 73% [56-86] | 100% [89-100] | 0% [0-11] | 57% [39-73] | 90% [74-97] | 11% [7-17] | 49.1 [38.2, 59.7] | 29 | 1.5 | 9426 |
| case_officer_noaudit | 63% [55-71] | 73% [56-86] | 100% [89-100] | 0% [0-11] | 57% [39-73] | 87% [70-95] | 11% [7-17] | 48.0 [37.0, 58.7] | 28 | 1.5 | 14591 |
| dr_rule | 59% [51-67] | 63% [46-78] | 100% [89-100] | 0% [0-11] | 53% [36-70] | 80% [63-90] | 7% [4-12] | 45.3 [35.3, 55.2] | 28 | 1.8 | 161 |
| random | 4% [2-6] | 0% [0-4] | 17% [10-26] | 0% [0-4] | 1% [0-6] | 0% [0-4] | 78% [74-81] | -38.2 [-40.2, -35.9] | 30 | 3.7 | 72 |

## Paired difference vs `pf_greedy` (same tier and seed)

| Agent | Score diff [95% CI] | Success diff [95% CI] | Distinguishable from zero? |
|---|---|---|---|
| pf_audit | +27.8 [+18.3, +37.7] | +0.20 [+0.13, +0.27] | score yes, success yes |
| case_officer | +27.1 [+17.7, +37.2] | +0.19 [+0.12, +0.27] | score yes, success yes |
| case_officer_nodark | +25.0 [+15.1, +35.5] | +0.15 [+0.07, +0.24] | score yes, success yes |
| case_officer_noaudit | -1.1 [-2.9, +0.0] | -0.01 [-0.02, +0.00] | score no, success no |
| dr_rule | -3.8 [-8.6, +0.5] | -0.05 [-0.08, -0.01] | score no, success yes |
| random | -87.3 [-98.2, -76.8] | -0.60 [-0.68, -0.53] | score yes, success yes |

## Ablations: paired difference vs `case_officer`

| Ablation | Score diff [95% CI] | Success diff [95% CI] |
|---|---|---|
| case_officer_noaudit | -28.2 [-38.1, -18.7] | -0.20 [-0.27, -0.13] |
| case_officer_nodark | -2.1 [-4.6, -0.4] | -0.04 [-0.07, -0.01] |

Not measured: calibration (agents emit no confidence), dollar cost (no priced model), weather/cloud tiers (mechanic not built).
