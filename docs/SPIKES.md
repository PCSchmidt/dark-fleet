# Dark Fleet - Verification Spike Ladder

**Created:** 2026-10-29 | **Status:** Not started

| ID | Question | Build | Pass criteria | If it fails |
|---|---|---|---|---|
| **S1** | Can role-filtered views prevent hidden-state leaks? | Truth/knowledge separation; fuzz harness issuing random actions and snapshots to a Hunter client stub | Zero fields from truth state appear in Hunter-visible output across 1M fuzz steps; UI layer cannot import truth types | Tighten module boundaries; allow-list serialization |
| **S2** | Does the AIS reception model produce realistic coverage and gaps? | Terrestrial + satellite coverage with collision loss | Background gap rate within a stated band (e.g. 1-5% of 6-h windows for innocent vessels in coverage); coverage holes appear where modelled | Adjust coverage/collision parameters |
| **S3** | Are orbital passes correct? | SGP4-class propagation of fictional constellation; compare to an independent implementation | Pass times within ±5 s and ground tracks within 1 km over 7 days | Fix time scale/frame conversions |
| **S4** | Is the traffic generator plausible and deterministic? | Fit route/speed distributions from a public AIS sample; generate 30 days | KS-test p>0.05 on speed and course distributions for each vessel class; identical output across runs with same seed | Refit; add route constraints |
| **S5** | Can on-demand sensor views meet budget and read correctly? | EO chip + SAR shader for a ship scene | Generation ≤ 2 s on Reference tier at chosen resolution; owner confirms VLCC vs product tanker distinguishable in EO at the sensor's GSD and not in a coarse SAR mode | Reduce resolution; simplify shaders |
| **S6** | Does weather modulate sensors correctly? | Cloud/Hs/wind sweeps | EO detection → 0 under overcast; SAR clutter increases with Hs; patrol sorties canceled above limit | Fix dependencies |
| **S7** | Are deception actions detectable but not trivially so? | Runner spoof/swap/dark scenarios vs scripted analyst | Each action detected with probability within design bands (e.g. 20-70% over 24 h under baseline surveillance) | Retune trace strengths |
| **S8** | Does STS produce physically plausible signatures? | STS state machine; EO/AIS output | Required proximity/speed/duration enforced; signatures generated; sea-state gating works | Fix state machine |
| **S9** | Is the Hunter tracker useful? | Particle-filter tracker vs random-walk and Runner bot | Posterior mass within 20 nm of truth ≥ 70% after 12 h of observations in baseline scenarios; calibration curve slope within 0.8-1.2 | Improve motion model; resample |
| **S10** | Is the game balanced? | 10,000 bot-vs-bot matches across scenarios | Runner win rate 40-60% on default scenarios; no dominant fixed strategy (checked via strategy clustering) | Retune resources/detection; add counters |
| **S11** | Does async turn resolution work end-to-end? | Two clients + server, simultaneous plans | Deterministic resolution; replays equal; no information leaks (S1 suite on live path) | Fix serialization/ordering |
| **S12** | Is the evidence UX understandable? | Owner plays Hunter scenario with evidence board | Owner can reach a justified identification from a Daily Trace in a recorded session; confusion points logged | Simplify UI |

## Order
S1 → S2/S3/S4 (parallel) → S6 → S7/S8 → S5 → S9 → S10 → S11 → S12.

## Evidence format
`docs/spike-results/Sx.md` with commit, data hashes, plots/tables, verdict and date. Retain failed attempts.
