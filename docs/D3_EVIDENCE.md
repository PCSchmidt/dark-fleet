# D3 Evidence - Agent Layer

**Gate:** D3 (see [SPEC.md](./SPEC.md) section 14). **Date:** 2026-10-30.
**Status:** Built and machine-verified for the scope below. **No real language model has driven any of it.** There is no API key or local model in the build environment, so the Case Officer's specialists are deterministic planners, the model path is optional and tested only against a fake transport, and the model-robustness questions (S15, S17 adaptive) are open. The owner playtest (D2 gate) is still pending, and so is the first look at the Case Officer panel in a browser.

## What D3 added
| Capability | Where |
|---|---|
| Tool surface for Hunter-side agents: 11 JSON-Schema tools, capability token, argument validation, per-turn call budget, untrusted-text wrapping, proposals instead of actions | `python/darkfleet/agent/tools.py` |
| Evidence ledger: everything the Hunter was ever shown, keyed by contact id | `agent/ledger.py` |
| Deterministic citation checker, 7 assertion types, 6 statuses, claim gate | `agent/citations.py` |
| Case Officer: plan, delegate (AIS Analyst, Collection Planner, Evidence Clerk), verify, present; optional model-written summary | `agent/case_officer.py` |
| Structured trace (spans, tool/model calls, tokens, cost, latency) as JSONL | `agent/trace.py` |
| Model access behind one interface; null default; standard-library Anthropic Messages client | `agent/llm.py` |
| MCP server over stdio (tools primitive) with environment-control tools | `agent/mcp_server.py` |
| Headless agent environment and auto-approving autopilot | `agent/env.py`, `agent/run.py` |
| Static injection suite (125 payloads) | `agent/injection.py` |
| S14 labelled citation set and evaluator | `agent/s14.py` |
| In-game Case Officer panel: ask, see findings with verified/UNVERIFIED badges, proposals with approve / edit / reject, collapsible trace and cost; `--no-agents` switch | `play/session.py`, `play/server.py`, `play/static/index.html` |

### Rules and bot changes made alongside (open issues from D2)
- **Rules v2 (`df-rules-2`):** a correct, supported claim on a Runner that never went dark or spoofed is now an `unfounded_claim` (Hunter -25, Runner +25) instead of a win. This closes the D2 gap where accusing an innocent vessel paid off. Replays recorded under rules v1 are rejected as `E_VERSION`, by design.
- **Bot:** `HunterConfig.require_dark_ticks` makes the bot accuse only after the target was silent that long. Default 0, so earlier results stay reproducible.
- **Not changed:** the target-lock heuristic (still about 90% correct) and the narrow Runner policy set.

## Verification (2026-10-30, Windows, MSVC 19.44, Python 3.14)
| Check | Result |
|---|---|
| C++ tests: 36 cases, 194,844 assertions (adds the `unfounded_claim` outcome; existing claim test now makes the Runner violate first) | Passed |
| Python tests: 68 (20 new in `test_agent.py`) | Passed |
| GitHub Actions CI on the D3 commit | see "CI" at the end |

## Spike results
Raw files are in [spike-results/](./spike-results/).

### S14 - citation checker
500 labelled findings from 20 agent-played episodes (seeds 2000-2019): 216 supported, 284 corrupted across 8 kinds (wrong position, wrong MMSI, overstated silence, infeasible chain, fabricated id, id from the Runner's view, no citation, assertion about an uncited id).
| Metric | Result | Target |
|---|---|---|
| Recall on bad claims | 284/284 = 1.00 | >= 0.95 on fabricated / out-of-role ids |
| Precision of flagged | 284/284 = 1.00 | >= 0.95 |
| False-block rate on good claims | 0/216 = 0.00 | reported |
| Fabricated ids / Runner-view ids | 35/35 and 35/35 flagged | |

**Read this carefully:** the labels come from how the claims were built, by the same author who wrote the rules. A perfect score shows the implementation matches the rule table. It says nothing about the mistakes a real model makes, which are subtler than my corruptions. The `apart` assertion is covered by unit tests, not by this set. Verdict: the spike's pass criteria are met on this construction, and I do not count it as evidence that agent output is faithful.

### S16 - replay with an agent in the loop
150 episodes (50 per Runner policy, seeds 3000-3149) driven by the auto-approving Case Officer. All 150 replays verified by re-simulation from recorded inputs alone; zero mismatches. The target was 1,000 runs, so this is a scaled-down check: no failures in 150 does not establish zero in 1,000. Mean 340 tool calls per episode.

### S17 - injection (static suite only)
125 payloads (25 templates x 5 forms: plain, upper case, control characters, zero-width, long) placed in every background vessel's name. For each, a simulated compromised agent made 16 attacks: Runner/truth tools (6), a bad capability token, approving its own proposal through a tool, a free-text command argument, an oversized argument, claims with fabricated and Runner-view evidence, an uncited finding, and budget abuse.
| Metric | Result |
|---|---|
| Attack attempts | 2,000 |
| Attacks that succeeded | 0 |
| Structural checks failed (payload text outside `untrusted` fields, or uncapped/unprintable) | 0 of 375 |
| Episodes in which a Hunter action reached the world without a human decision | 0 |

**What this does and does not show.** It shows the enforcement layer (capabilities, schemas, budgets, claim gate, proposals) behaves as designed under these 125 payloads. It does **not** show that a language model would resist the payloads: no model was in the loop, so "attack success" here means only that the tool layer let a forbidden call through. The adaptive attacker half of S17 and any model-susceptibility measurement are not done. A 0% rate must not be read as immunity.

## Agent behaviour (not a benchmark)
These are single-configuration runs for orientation, with no confidence intervals and no baselines beyond the earlier bot.

- Autopilot (Case Officer approving every proposal), 150 episodes, outcomes: dark-running Runner 39 correct / 6 delivered / 4 wrong identity / 1 thin of 50; spoofing Runner 39 correct / 11 delivered of 50; honest Runner 49 delivered / 1 wrong identity of 50. It never made an `unfounded_claim`.
- Wrong-identity results are consistent with the known lock error (the nearest AIS vessel to the last-known area is not always the target) but I did not break them down by lock correctness.
- D2 bots re-run under rules v2 on the same held-out seeds (1000-1059, n = 60 per row). **This is not an untouched held-out run:** those seeds were already used once under rules v1. Raw: [results/d2_heldout_rules2.json](./results/d2_heldout_rules2.json).

| Runner | Bot | correct_supported | unfounded_claim | wrong_identity | runner_delivered | mean Hunter score |
|---|---|---|---|---|---|---|
| dark_run | no audit | 49 | 1 | 2 | 7 | 98.8 |
| dark_run | audit | 47 | 2 | 4 | 5 | 94.1 |
| dark_run | audit + dark evidence | 48 | 1 | 3 | 6 | 97.2 |
| honest | no audit | 0 | 2 | 0 | 58 | -0.8 |
| honest | audit | 0 | 8 | 1 | 49 | -3.3 |
| honest | audit + dark evidence | 0 | 1 | 0 | 59 | -0.4 |
| spoof | no audit | 2 | 0 | 0 | 58 | 4.0 |
| spoof | audit | 48 | 0 | 4 | 8 | 97.3 |
| spoof | audit + dark evidence | 48 | 0 | 3 | 9 | 98.1 |

The dark-evidence rule cuts accusations of the honest Runner from 8 to 1 without hurting the other cases. One accusation of the honest Runner remains, which I attribute to a random run of AIS drop-outs lasting 6+ ticks; I did not verify that.

## Honest limits
- No real model has been run: no S15 (local model tool use), no live test of the Anthropic client, no model-written summary has ever been produced outside the fake-transport test.
- Specialists implemented: AIS Analyst, Collection Planner, Evidence Clerk. Not implemented: Air/Surface Ops Planner, Fixer, Tip/Chatter Generator, weather and orbital-pass tools (those mechanics do not exist yet).
- The Case Officer reuses the bot's tracker, audit and chain logic; it is the same intelligence with a checked, explainable interface, not a better analyst. Its usefulness to a human is untested.
- The MCP server was exercised only in-process and over a StringIO round trip, never against a third-party MCP client; the protocol revision is a `[VERIFY]` item.
- The AGENT_TRACE is returned and downloadable as JSONL but is not embedded in the replay container; replays do not need it (S16), which is the intended design.
- The citation checker's rule table covers positions, identities, silences, size classes, SAR chains, AIS/SAR co-location and separation. It cannot judge intent, plausibility or anything outside the table; such text is `unverifiable`.
- DarkFleet-Bench (D4), calibration of agent confidence, human-assistance evaluation and cost studies are not started.
- Everything from the D0-D2 limits still applies.

## Reproduce
```
cmake --build build --config Release && ctest --test-dir build -C Release --output-on-failure
python -m pytest python/tests -q
PYTHONPATH=build/python:python python -m darkfleet.agent.s14 --out docs/spike-results/S14.json
PYTHONPATH=build/python:python python -m darkfleet.agent.injection --out docs/spike-results/S17-static.json
PYTHONPATH=build/python:python python -m darkfleet.agent.run --n 150 --out docs/spike-results/S16.json
PYTHONPATH=build/python:python python -m darkfleet.evaluate_d2 --seeds heldout --rules2 --out docs/results/d2_heldout_rules2.json
PYTHONPATH=build/python:python python -m darkfleet.play.server        # Case Officer panel; add --no-agents to disable
```
