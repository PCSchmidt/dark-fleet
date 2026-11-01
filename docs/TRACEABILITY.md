# Traceability (initial, D1; D2 rows added)

Maps requirement IDs from [REQUIREMENTS.md](./REQUIREMENTS.md) to evidence. "Partial" means some but not all of the requirement is covered. Unlisted requirements have no implementation yet.

| Requirement | Status | Evidence |
|---|---|---|
| FR-001 deterministic simulation | Partial (same build only) | `core/tests/test_world.cpp` (hash chain equality), `test_replay.cpp` (byte-identical replays) |
| FR-002 fixed ticks | Done | `hr::FixedClock`, `World::step` |
| FR-003 vessel motion limits | Partial | speed limit via `set_course_speed` (RuleViolation); no fuel or shallow-water limits |
| FR-004 deterministic traffic | Done | `core/tests/test_d1.cpp` (generator tests) |
| FR-005 AIS transmission/reception | Partial | report probability, innocent outages; no class-dependent intervals |
| FR-006 Runner can go dark/spoof | Done (identity swap not implemented) | `test_actions.cpp`, `test_d1.cpp` |
| FR-007 STS | Partial (rules v3): a required loitering transfer in a zone (proximity, low speed, duration); no sea-state limits, no second ship, no signature model | `core/tests/test_v3.cpp`, `python/tests/test_v3.py`, `docs/RULES_V3.md` |
| FR-008 weather | Not started | - |
| FR-009 orbital passes | Not started (SAR passes are tasked with a fixed delay) | - |
| FR-020 role views only | Done | `core/tests/test_leak.cpp` |
| FR-021 typed role-scoped actions | Done | `test_actions.cpp`, `python/tests/test_binding.py` |
| FR-022 noisy contacts with uncertainty | Done | observation schema; `sigma_m` required > 0 |
| FR-023 leak suite on every build | Done (pattern-based) | `test_leak.cpp`, CI |
| FR-030 evidence items with IDs/sources | Partial (contacts only) | observation schema |
| FR-031 claims cite evidence; distinguishes wrong vs insufficient | Done | `test_d1.cpp` claims test |
| FR-032 score is a pure function of the replay | Done | `test_replay.cpp`, `python/tests/test_d1.py` |
| FR-033 debrief truth vs knowledge | Not started | - |
| FR-040 agents use only the role-filtered tool surface | Done | `agent/tools.py`; `test_agent.py` (capability, forbidden tools, no truth in outputs) |
| FR-041 asserted facts carry evidence IDs, validated by a checker | Done for the rule table in `citations.py` | `agent/citations.py`, spike S14 (`docs/spike-results/S14.json`) |
| FR-042 decisions logged; human-approved actions are the authoritative inputs | Partial | `ToolHost.approve` is the only path to the world; trace is observability (JSONL), stored beside, not inside, the replay |
| FR-043 token, cost, latency metrics | Partial | `Trace.totals()`; cost is `null` when no priced model is used |
| FR-044 MCP server | Done (in-process tested) | `agent/mcp_server.py`, `test_agent.py` |
| FR-045 injection suite | Done (static, tool layer only) | `agent/injection.py`, `docs/spike-results/S17-static.json` |
| Rules v3 (D5 recommendation): completing the mission requires a rule-breaking act | Built; scripted Hunters, three generations of learned Runners and (see doc) a language-model Hunter evaluated | `docs/RULES_V3.md`, `docs/bench/rules-v3/`, `docs/results/arms_race_v3.json`, `models/runner_v3*/` |
| US-6 benchmark an agent | Built; scripted agents only | `python/darkfleet/bench.py`, `docs/BENCH.md`, `docs/bench/`, `python/tests/test_bench.py` |
| NFR reproducibility of results | Done for the bench | three-run digest identical (`docs/spike-results/S19.json`) |
| ML.md s2 AIS anomaly detector | Built; synthetic-only, not integrated | `ml/anomaly.py`, `docs/model-cards/anomaly-detector.md` |
| ML.md s3 Runner policy via self-play | Built as parametric search, not deep RL | `ml/runner_es.py`, `docs/model-cards/learned-runner.md` |
| ML.md s5 claim calibration | Built; used by `case_officer_cal` | `ml/calibrator.py`, `docs/model-cards/claim-calibrator.md` |
| Spike S15 model drives the tool surface | Partial: one hosted model, 150 episodes, 0.84% tool errors; not three local models | `docs/LLM_AGENT.md`, `bench/llm/` |
| Spike S17 injection, model half | Partial: 68 attack episodes, 0 successes; no adaptive attacker | `docs/LLM_AGENT.md` |
| US-4 agent assists the Hunter, visibly | Built; owner playtest pending | Case Officer panel, `test_agent.py::test_game_server_officer_flow_*` |
| FR-050 baseline tracker + scripted Runner | Done | `python/darkfleet/`, `docs/D1_EVIDENCE.md` |
| FR-051 learned Runner / anomaly detector | Not started | - |
| FR-060 contracts followed | Partial | schemas v1.1; replay format per DATA_CONTRACTS (JSON, not binary) |
| FR-061 dataset manifest | N/A (no external datasets used yet) | - |
| FR-070.. interface | Not started | - |
| US-1/US-2 playable | Built; owner playtest pending | `python/darkfleet/play/`, `python/tests/test_play.py`, `docs/D2_EVIDENCE.md` |
| FR-021 UI uses only the role API | Done for the D2 client | `python/tests/test_play.py` (state never carries truth before the end) |
| FR-030 evidence/identification without a given MMSI | Partial | profile briefing (`mission.brief.mode = "profile"`), bot target lock and AIS audit |
| US-3 replay resolves and tamper is rejected | Done | `test_replay.cpp`, `python/tests/test_d1.py` |
| NFR-001 headless speed | Not measured | - |
