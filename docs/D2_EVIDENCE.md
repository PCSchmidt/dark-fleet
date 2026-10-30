# D2 Evidence - Playable 2D Client

**Gate:** D2 (see [SPEC.md](./SPEC.md) section 14). **Date:** 2026-10-30.
**Status:** Built and machine-verified for the scope below. **The gate's "owner playtest recorded honestly" is not met yet:** nobody has played it, and I (the author, an AI assistant) did not run the client in a browser. The client script passes a Node syntax check and the server API is exercised by tests, but the on-screen behaviour (drawing, clicking, layout) is unverified.

## What D2 added
| Capability | Where |
|---|---|
| Profile briefing: the Hunter gets the target class and a last-known area (6 km sigma, offset up to 5 km from the true start), **not** the MMSI. Optional per scenario; legacy MMSI briefs unchanged | `mission.brief` in scenario schema, `core/src/observe.cpp`, `generate_scenario(..., brief="profile")` |
| Bot target lock: the AIS vessel nearest the last-known area (within 20 km) | `HunterBot._resolve_brief` |
| Bot AIS audit: one early SAR pass at the target's AIS position; a large vessel under it means consistent, none means look for an unexplained large vessel and estimate the spoof offset, then follow and claim on a two-pass SAR chain | `HunterBot._audit_step`, `_spoof_step` |
| Play session (either role vs a bot), engine-validated actions, truth released only after the end | `python/darkfleet/play/session.py` |
| Local web server (standard library, localhost) and canvas client: pan/zoom map, AIS trails, SAR detections, pending footprints, SAR tasking by click, evidence picking and claim, Runner course/speed/AIS controls, optional analyst overlay (particle filter on the player's own observations), event log, debrief with true track and replay verification | `play/server.py`, `play/static/index.html` |
| Evaluation of the bots under the profile briefing | `python/darkfleet/evaluate_d2.py` |

## Verification (2026-10-30, Windows, MSVC 19.44, Python 3.14)
| Check | Result |
|---|---|
| C++ tests: 36 cases, 194,829 assertions (adds: profile-briefing leak fuzz over 300 seeds, legacy-brief compatibility, schema rejection of a profile brief without `last_known`) | Passed |
| Python tests: 48 (adds 15 in `test_play.py`: schemas, byte-identical legacy scenarios, bot lock and audit, session role filtering for both roles, engine validation of human actions, verified debrief, HTTP flow and guards) | Passed |
| Hunter session state over 60 ticks never contains the canary, `true_track`, `debrief`, `own` or `mission`; replay endpoint refuses before the end | Passed |
| Runner session state never contains `brief`, `resources` or analyst data | Passed |
| Client JavaScript syntax (`node --check`) | Passed |
| Live server smoke test (page 200, new game returns a profile brief and contacts) | Passed |
| Every evaluated episode replay-verified by re-simulation | 480 of 480 (120 dev + 360 held-out; `all_replays_verified: true` in both JSON files) |

## Bot results under the profile briefing
Dev seeds 1-20 shaped the audit thresholds; **held-out seeds 1000-1059 were run once after the code was frozen.** Raw: [results/d2_heldout.json](./results/d2_heldout.json), dev: [results/d2_dev.json](./results/d2_dev.json). n = 60 per row; no confidence intervals are computed here, so differences of a few episodes should be read as noise.

| Runner policy | Hunter bot | correct_supported | runner_delivered | wrong_identity | insufficient_support | mean Hunter score |
|---|---|---|---|---|---|---|
| dark_run | no audit | 50 | 7 | 2 | 1 | 101.3 |
| dark_run | audit | 49 | 5 | 4 | 2 | 99.1 |
| honest | no audit | 2 | 58 | 0 | 0 | 4.0 |
| honest | audit | 8 | 49 | 1 | 2 | 16.0 |
| spoof | no audit | 2 | 58 | 0 | 0 | 4.0 |
| spoof | audit | **48** | 8 | 4 | 0 | **97.3** |

Reading:
- **The audit closes the spoofing gap from D1.** Without it, the bot trusts the broadcast position and almost never finds a spoofing Runner (2/60); with it, 48/60. The audit flagged spoofing in 50 of 60 spoof episodes and in no dark-run or honest episode (`audit_results` in the JSON).
- **Target lock is the main new failure mode.** The nearest-AIS-vessel heuristic locked the true target in 54/60 (dark_run, honest) and 53/60 (spoof) episodes. A wrong lock leads to wrong claims (`wrong_identity`, -50 for the Hunter) or a missed Runner. The 4-5 `wrong_identity` outcomes in the audit rows are consistent with this, but I did not break outcomes down by lock correctness.
- **The audit costs one of six SAR taskings**, and the dark_run score is 99.1 vs 101.3. With n = 60 and no interval, I do not claim a real difference.
- **The honest-Runner "wins" (2 and 8 of 60) are not detections of wrongdoing.** The Runner delivers cargo with AIS on; a random AIS drop-out makes the bot task SAR and claim the real vessel, and scoring (D1) rewards any correct, supported identification. Scoring has no notion of an innocent vessel yet.

## Balance
**Not achieved.** The Hunter bot finds a dark-running Runner about 83% of the time (50/60) while the Runner bots use fixed scripts. No human has played either side, so there is no evidence about difficulty for people. The Runner has no way to exploit what the audit does.

## Limits and known issues
- No owner playtest; the client UI is unverified in a browser; no accessibility audit beyond labels and a Space shortcut; colours were not checked for colour-blind legibility (shapes differ between AIS and SAR).
- Single session per server process; localhost only; no authentication (not needed for the intended use, but do not expose it).
- The analyst overlay is a strong aid (a particle filter); it can be switched off in the client, but there is no scoring difference for using it.
- The 2D map has a grid only: no coastline or basemap (open decision "basemap approach" remains).
- Bot Runner policies are the three D1 scripts; the human Hunter cannot tell which one it faces until the debrief, but the set is small.
- The audit and lock heuristics are tuned on 20 dev seeds and brittle in two ways seen above: a spoof offset larger than the lock radius (20 km) breaks the lock, and an audit pass that sees only clutter is "inconclusive".
- Everything from the D0/D1 limits still applies (same-build determinism, pattern-based leak harness, tracker caveats).

## Reproduce
```
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
python -m pytest python/tests -q
PYTHONPATH=build/python:python python -m darkfleet.evaluate_d2 --seeds heldout --out docs/results/d2_heldout.json
PYTHONPATH=build/python:python python -m darkfleet.play.server        # then open http://127.0.0.1:8765/
```
