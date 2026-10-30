# D1 Evidence - Headless Vertical Slice

**Gate:** D1 (see [SPEC.md](./SPEC.md) section 14). **Date:** 2026-10-30.
**Status:** Implemented and verified for the scope below. It is a baseline simulation with bots, **not a balanced or playable game**: there is no UI yet (D2).

## What D1 added
| Capability | Where |
|---|---|
| Deterministic scenario + background-traffic generator (two lanes, class mix, fishing wanderers, innocent AIS outage windows) | `core/src/traffic.cpp` |
| Waypoint-following vessel motion; delivery objective; timeout | `core/src/world.cpp` |
| Second sensor: tasked SAR passes (budget, delay, footprint, size-dependent detection probability, unlabeled contacts; detects the Runner even when AIS is dark) | `core/src/world.cpp` |
| Evidence and scoring: `file_claim` with cited contacts; outcomes `correct_supported`, `insufficient_support`, `wrong_identity`, plus `runner_delivered` and `timeout`; scores derived purely from the run | `core/src/world.cpp` |
| Replay container + verifier (`E_HASH`, `E_DESYNC` with first bad tick, `E_VERSION`, `E_SCHEMA`) | `core/src/replay.cpp`, `World::replay()` |
| Scripted Runner policies (`honest`, `dark_run`, `spoof`) and baseline Hunter bot | `python/darkfleet/policies.py` |
| Hunter trackers: dead-reckoning baseline and particle filter | `python/darkfleet/tracker.py` |
| Evaluation harness with held-out seeds and bootstrap CIs | `python/darkfleet/evaluate.py` |
| Schemas v1.1 (SAR contacts, mission, brief, resources, status, `file_claim`) | `schemas/` |

## Verification (2026-10-30, Windows, MSVC 19.44, Python 3.14)
| Check | Result |
|---|---|
| C++ tests: 34 cases, 158,524 assertions | Passed |
| Leak fuzz, legacy scenarios: 2,000 seeds x 25 ticks x 2 roles | 0 leaks |
| Leak fuzz, generated scenarios with SAR tasking, claims and dark runners: 1,000 seeds x up to 40 ticks x 2 roles | 0 leaks; Hunter never receives `own` or `mission` |
| Mutation test: 600 deliberately leaky observations | 600/600 detected |
| Replay verification (C++): 4 scripted runs verify; round-trip through JSON text verifies; identical scripts give byte-identical replays | Passed |
| Tamper tests: changed input (naive edit and rehashed edit), deleted input, forged result, forged chain link, wrong scenario, wrong ruleset, garbage, dropped trailing input | All rejected with the expected typed code |
| Python tests: 33 | Passed |
| Bot episodes (all recorded and then verified by re-simulation): 40+40+20+20 dev episodes and 60+60+20 held-out episodes | 100% verified (`all_replays_verified: true`) |

A false positive found by the harness while building D1 is worth recording: the leak fuzz flagged an exact truth number in the Hunter's own `pending_taskings` because the *test* had tasked SAR at the Runner's exact truth coordinates. That was a test-design error (a real Hunter cannot know truth), not an engine leak; the test was changed to perturb its orders.

## Tracker study (primary technical result)
Protocol, from `python/darkfleet/evaluate.py`: the Runner goes dark for 40-70 ticks and makes one heading dogleg; both trackers see the identical observation stream; SAR taskings are scheduled using only the dead-reckoning estimate so neither tracker influences its own data; metrics are on ticks where the target's AIS has been silent for >= 3 ticks; negative log-likelihood (NLL) uses a 1% uniform-over-region mixture for both trackers so one catastrophic miss cannot dominate. Dev seeds 1-40 were used to tune the filter; **held-out seeds 1000-1059 were run once, after the code was frozen**. Raw output: [results/d1_heldout.json](./results/d1_heldout.json).

Positive numbers mean the particle filter (PF) beats dead reckoning (DR); 95% CIs from a paired bootstrap over episodes (n = 60).

| Condition | Metric | DR | PF | DR - PF (mean, 95% CI) | PF better in |
|---|---|---|---|---|---|
| **With SAR** (6 taskings) | NLL (nats) | 21.86 | 19.80 | **2.06 [1.80, 2.30]** | 93% of episodes |
| | Mean position error (m) | 19,467 | 11,323 | **8,143 [6,087, 10,148]** | 87% |
| | Within 20 km (PF mass vs DR indicator) | 0.47 | 0.73 | - | - |
| | Error at last silent tick (m) | - | - | **-5,626 [-12,298, 416]** (PF worse on average; CI includes 0) | 53% |
| **Passive** (no SAR) | NLL (nats) | 21.86 | 21.96 | -0.10 [-0.22, 0.02] (no difference) | 42% |
| | Mean position error (m) | 19,467 | 19,232 | 235 [55, 415] (negligible) | 65% |

Reading: when SAR data is available the PF makes substantially better use of it (including negative information) than a tracker that ignores it. Without extra data the two are equivalent, as expected. **The filter does not improve the estimate at the final silent tick**, because the dominant-mode estimate can lock onto a wrong mode once the SAR budget is spent. This is a real weakness, not hidden here.

### What this does and does not establish against Spike S18
- Shown: PF has better NLL than DR with SAR on held-out seeds with a CI excluding zero.
- **Not shown:** better NLL "at all horizons" (not stratified by silence duration); calibration (reliability diagram / slope 0.8-1.2 not computed); behaviour against the `spoof` runner (the PF has an outlier term but this was not evaluated); comparison against stronger baselines (for example a PF without the birth proposal).
- The DR baseline's NLL uses a hand-set Gaussian width (1 km + 25% of distance travelled at 7 m/s), which may be generous or harsh; the comparison is only as fair as that choice.
- Particle count 3,000, tuned on dev seeds only; held-out used the same configuration.

## Bot behaviour (baselines, not a balanced game)
Held-out, 60 episodes each, vs the `dark_run` Runner:
| Hunter tasking guided by | correct_supported | insufficient_support | wrong_identity | runner_delivered | mean Hunter score |
|---|---|---|---|---|---|
| PF | 54 | 2 | 2 | 2 | 109.9 |
| DR | 51 | 2 | 2 | 5 | 103.9 |

Against the `honest` Runner (20 episodes), the bot only acts when the target goes silent, so it claims in 1 of 20 episodes and the Runner delivers in 19. Against `spoof`, the bot also never acts. **Balance (Spike S10) is not achieved**: the scripted Runner is easy to find once it goes dark, and the Hunter has no behaviour for detecting spoofing. The Runner also reuses a lane-adjacent route with many background vessels, so SAR clutter is realistic but the policy set is narrow.

## Limits and known issues
- No UI; no human playtest; no balance claim.
- Single sensor pair (AIS, SAR); EO is rejected as unavailable. No weather, STS, identity swap or evidence beyond contact chaining.
- The Hunter always knows the target MMSI (the briefing), which makes honest-runner cases trivial; the briefing and scoring need redesign for D2.
- The Hunter bot's claim logic accepts only SAR chains; AIS contacts are not used as supporting evidence.
- Determinism holds for identical builds (libm-based noise). Cross-compiler replay is not claimed.
- The leak harness remains pattern-based (see D0 limits).
- The tracker study uses a Python filter with NumPy; its random number generator is seeded but is not part of the deterministic core.
- Replay container lives in `dark-fleet` rather than the platform repo (extract when a second title needs it, per platform principle P6).

## Reproduce
```
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
python -m pip install -r python/requirements-dev.txt
python -m pytest python/tests -q
PYTHONPATH=build/python:python python -m darkfleet.evaluate --seeds heldout --out docs/results/d1_heldout.json
```
