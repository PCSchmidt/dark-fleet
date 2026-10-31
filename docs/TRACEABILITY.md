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
| FR-007 STS | Not started | - |
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
| US-6 benchmark an agent | Built; scripted agents only | `python/darkfleet/bench.py`, `docs/BENCH.md`, `docs/bench/`, `python/tests/test_bench.py` |
| NFR reproducibility of results | Done for the bench | three-run digest identical (`docs/spike-results/S19.json`) |
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
