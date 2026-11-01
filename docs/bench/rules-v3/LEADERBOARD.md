# DarkFleet-Bench leaderboard (darkfleet-bench-2, seeds: heldout, rules df-rules-3)

Success = correct supported identification (honest tier: no accusation). Brackets are 95% intervals (Wilson for rates). Episodes per agent per tier: standard 30, honest 30, spoof 30, decoy_rich 30, injection_laced 30, visible 30.

| Agent | Overall success | standard | honest | spoof | decoy_rich | injection_laced | visible | False accusations | Claim precision | Mean score [95% CI] | Ticks to ID | SAR used | median ms/episode |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pf_audit_sweep | 87% [82-91] | 83% [66-93] | 100% [89-100] | 93% [79-98] | 80% [63-90] | 73% [56-86] | 93% [79-98] | 4% [2-9] | 93% [88-96] | 80.7 [71.9, 88.9] | 58 | 2.5 | 147 |
| case_officer_sweep | 86% [80-90] | 83% [66-93] | 100% [89-100] | 87% [70-95] | 80% [63-90] | 73% [56-86] | 93% [79-98] | 4% [2-9] | 93% [88-96] | 79.4 [70.7, 87.8] | 58 | 2.5 | 4826 |
| pf_audit | 83% [77-88] | 70% [52-83] | 100% [89-100] | 93% [79-98] | 70% [52-83] | 73% [56-86] | 93% [79-98] | 3% [2-7] | 88% [82-93] | 77.9 [69.6, 86.0] | 57 | 2.8 | 190 |
| pf_audit_jump | 83% [77-88] | 70% [52-83] | 100% [89-100] | 93% [79-98] | 70% [52-83] | 73% [56-86] | 93% [79-98] | 3% [2-7] | 88% [82-93] | 77.9 [69.6, 86.0] | 57 | 2.8 | 196 |
| case_officer | 83% [77-88] | 70% [52-83] | 100% [89-100] | 93% [79-98] | 70% [52-83] | 73% [56-86] | 93% [79-98] | 3% [2-7] | 88% [82-93] | 77.9 [69.6, 86.0] | 57 | 2.8 | 5684 |
| case_officer_jump | 82% [76-87] | 70% [52-83] | 100% [89-100] | 87% [70-95] | 70% [52-83] | 73% [56-86] | 93% [79-98] | 3% [2-7] | 88% [81-93] | 76.7 [68.4, 84.9] | 57 | 2.8 | 5859 |
| dr_rule | 71% [64-77] | 77% [59-88] | 100% [89-100] | 0% [0-11] | 83% [66-93] | 77% [59-88] | 90% [74-97] | 2% [1-6] | 88% [80-92] | 64.2 [55.6, 72.6] | 57 | 1.9 | 155 |
| pf_greedy | 71% [64-77] | 77% [59-88] | 100% [89-100] | 0% [0-11] | 83% [66-93] | 77% [59-88] | 90% [74-97] | 2% [1-6] | 88% [80-92] | 64.2 [55.6, 72.6] | 57 | 1.9 | 201 |
| case_officer_noaudit | 71% [64-77] | 77% [59-88] | 100% [89-100] | 0% [0-11] | 83% [66-93] | 77% [59-88] | 90% [74-97] | 2% [1-6] | 88% [80-92] | 64.2 [55.6, 72.6] | 57 | 1.9 | 8310 |
| random | 3% [2-5] | 0% [0-4] | 17% [10-26] | 0% [0-4] | 0% [0-4] | 0% [0-4] | 0% [0-4] | 79% [76-82] | 0% [0-1] | -38.9 [-40.7, -37.0] | - | 3.7 | 64 |

## Paired difference vs `pf_greedy` (same tier and seed)

| Agent | Score diff [95% CI] | Success diff [95% CI] | Distinguishable from zero? |
|---|---|---|---|
| pf_audit_sweep | +16.5 [+7.7, +25.1] | +0.16 [+0.09, +0.23] | score yes, success yes |
| case_officer_sweep | +15.2 [+6.3, +23.8] | +0.15 [+0.08, +0.22] | score yes, success yes |
| pf_audit | +13.8 [+5.5, +22.5] | +0.12 [+0.06, +0.19] | score yes, success yes |
| pf_audit_jump | +13.8 [+5.5, +22.5] | +0.12 [+0.06, +0.19] | score yes, success yes |
| case_officer | +13.8 [+5.5, +22.5] | +0.12 [+0.06, +0.19] | score yes, success yes |
| case_officer_jump | +12.5 [+4.2, +21.1] | +0.11 [+0.04, +0.18] | score yes, success yes |
| dr_rule | +0.0 [+0.0, +0.0] | +0.00 [+0.00, +0.00] | score no, success no |
| case_officer_noaudit | +0.0 [+0.0, +0.0] | +0.00 [+0.00, +0.00] | score no, success no |
| random | -103.1 [-111.6, -94.4] | -0.68 [-0.75, -0.62] | score yes, success yes |

## Ablations: paired difference vs `case_officer`

| Ablation | Score diff [95% CI] | Success diff [95% CI] |
|---|---|---|
| case_officer_jump | -1.3 [-3.2, +0.0] | -0.01 [-0.03, +0.00] |
| case_officer_sweep | +1.4 [-5.7, +8.5] | +0.03 [-0.03, +0.08] |
| case_officer_noaudit | -13.8 [-22.5, -5.5] | -0.12 [-0.19, -0.06] |

Not measured: calibration (agents emit no confidence), dollar cost (no priced model), weather/cloud tiers (mechanic not built).
