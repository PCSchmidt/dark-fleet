# Dark Fleet - Requirements, User Stories and Session Walkthrough

**Status:** Draft v1 - design only
**Companion to:** [SPEC.md](./SPEC.md). Requirement IDs here are referenced by tests, spikes and the agent benchmark.
Priorities: **P0** = needed for the first playable headless slice; **P1** = first complete game; **P2** = later.

## 1. User stories and acceptance scenarios

### US-1 (P0) - Play the Hunter against a bot Runner
As a Hunter, I fuse partial sensor information to identify and interdict a Runner.
1. **Given** a seeded scenario, **when** I open it, **then** I see only what my role may know (map, AIS picture, available assets, time, weather) and the objective.
2. **Given** an available satellite pass, **when** I task it over an area, **then** the result arrives at the pass time as a noisy contact list or "nothing detected / cloud-obscured".
3. **Given** a candidate vessel, **when** I file an identification claim with cited evidence, **then** the claim is scored (correct, wrong, insufficient evidence) and the debrief explains why.
4. **Given** I accuse an innocent vessel, **then** a false-accusation penalty applies and the debrief shows what the evidence did and did not support.

### US-2 (P0) - Play the Runner against a bot Hunter
1. **Given** a scenario, **when** I plan a route with a transfer site and delivery port, **then** the plan is checked for physical feasibility (speed, fuel, draft, sea state).
2. **Given** I switch AIS off or spoof identity, **then** each action shows its risk class and leaves traces the Hunter may discover.
3. **Given** the Hunter interdicts, **then** the outcome follows the rules and is explained in the debrief with the full-information replay.

### US-3 (P0) - Resolve a deterministic replay
1. **Given** a replay, **when** it is re-simulated on the same build, **then** every recorded state hash matches.
2. **Given** a tampered input log, **when** verified, **then** it is rejected with a typed error.

### US-4 (P0) - An agent assists the Hunter, visibly
1. **Given** the agent desk is enabled, **when** I request a tasking recommendation, **then** I see the agent's tool calls, retrieved evidence IDs, proposed actions with rationale, and cost/latency.
2. **Given** a proposal, **when** I approve, edit or reject it, **then** my decision (not the agent's) is the recorded input.
3. **Given** an agent claim without a valid evidence citation, **then** the claim is flagged and not presented as established.
4. **Given** agents are disabled, **then** the game is fully playable.

### US-5 (P1) - Daily Trace
Given a seeded day of traffic, I identify the Runner among N vessels; score reflects accuracy, evidence quality and calibration.

### US-6 (P1) - Benchmark an agent
Given an agent configuration, I run DarkFleet-Bench on a fixed seed list and obtain a reproducible result file with confidence intervals, cost and latency.

### US-7 (P1) - Review and learn
After any match I see a full-information replay with what each side knew at each time, the evidence timeline, and the agent trace if used.

### US-8 (P1) - Resume and manage data safely
Saves and settings are versioned; corrupt or incompatible files are reported and never silently overwritten.

### US-9 (P1) - Sensor views
Selecting a contact shows an EO, SAR or turret view consistent with the map and sensor model.

### US-10 (P2) - Task Force and live play
Two or three Hunters cooperate with distinct roles; live or async turns.

## 2. Functional requirements

### Simulation and rules
- **FR-001 (P0)** The simulation is deterministic: same build + scenario + inputs ⇒ identical state hashes.
- **FR-002 (P0)** Time advances in fixed ticks; compression changes the number of ticks per wall second only.
- **FR-003 (P0)** Vessel motion respects speed, turn-rate, fuel and shallow-water limits.
- **FR-004 (P0)** Background traffic is generated deterministically from the seed.
- **FR-005 (P0)** AIS transmission follows class-dependent intervals; reception follows coverage and loss models.
- **FR-006 (P0)** The Runner can go dark, spoof position, and swap identity; each has a documented trace model.
- **FR-007 (P1)** STS transfers require proximity, low speed, sea-state limits and duration, and generate signatures.
- **FR-008 (P0)** Weather fields modulate sensors, speeds and operations.
- **FR-009 (P1)** Satellite passes derive from orbital propagation of a fictional constellation.

### Information and roles
- **FR-020 (P0)** Each role receives only an `ObservationV1`; no truth state is exposed.
- **FR-021 (P0)** Actions are typed, validated and role-scoped; invalid actions return typed errors.
- **FR-022 (P0)** Sensor outputs are noisy contacts with uncertainty and confidence, not ground truth.
- **FR-023 (P0)** An automated leak suite asserts FR-020 on every build.

### Evidence and scoring
- **FR-030 (P0)** Evidence items have IDs, sources, times and confidence.
- **FR-031 (P0)** An identification claim cites evidence IDs; scoring distinguishes wrong identity from insufficient support.
- **FR-032 (P0)** Scores are a pure function of the replay.
- **FR-033 (P1)** Debrief shows truth vs knowledge timelines.

### Agents (see [AGENTS.md](./AGENTS.md))
- **FR-040 (P0)** Agents use only the role-filtered tool surface.
- **FR-041 (P0)** Agent outputs that assert facts carry evidence IDs, validated by a checker.
- **FR-042 (P0)** Agent decisions are logged in the replay's AGENT_TRACE; human-approved actions are the authoritative inputs.
- **FR-043 (P1)** Agent runs report token, cost and latency metrics.
- **FR-044 (P1)** An MCP server exposes the role-filtered tool surface over the headless sim.
- **FR-045 (P1)** An injection test suite exercises untrusted in-world text and records outcomes.

### ML (see [ML.md](./ML.md))
- **FR-050 (P0)** Baseline Hunter tracker and scripted Runner bot exist for balance and evaluation.
- **FR-051 (P1)** Learned Runner policy and anomaly detector are trained reproducibly and exported with model cards.

### Data and platform
- **FR-060 (P0)** Scenarios, actions, observations and replays conform to [DATA_CONTRACTS](https://github.com/PCSchmidt/hyperreal-platform/blob/main/docs/DATA_CONTRACTS.md).
- **FR-061 (P0)** Every dataset has a manifest entry with license and hash.
- **FR-062 (P1)** Saves and settings are versioned with migrations.

### Interface
- **FR-070 (P0)** A 2D tactical map shows only role-visible information, uncertainty ellipses, sensor footprints, a time control and a log.
- **FR-071 (P1)** Evidence board supports linking items to claims.
- **FR-072 (P1)** Keyboard navigation, scalable text, and non-colour-only symbology.
- **FR-073 (P1)** EO/SAR/turret views are generated from sim state.

## 3. Non-functional requirements
- **NFR-001** Headless sim runs ≥ 1,000× real time for a standard scenario on the Dev machine.
- **NFR-002** Map UI stays responsive at ≥ 60 FPS on the Reference tier.
- **NFR-003** A standard game session is completable in 20-40 minutes.
- **NFR-004** No network is required for single-player.
- **NFR-005** All third-party data/assets are in the license register.
- **NFR-006** Agent features have no effect on determinism or on scoring integrity.
- **NFR-007** Every learned model has a model card and a reproducible training command.

## 4. Session walkthrough (Hunter, standard scenario, ~30 minutes)

| Minute | Player experience | Systems exercised |
|---|---|---|
| 0:00 | Briefing: "Tanker *Meridian Star* (fictional) is suspected to be loading cargo covertly in the Gulf of Oman approaches. You have 72 game-hours and: 2 EO passes, 1 SAR pass window, 1 patrol aircraft with 2 sorties, 1 cutter." Map shows AIS picture. | Scenario load, FR-020 |
| 2:00 | Player scans traffic; the AIS analyst panel highlights 3 vessels with unexplained AIS gaps. One is innocent (coverage hole). | FR-005, anomaly features |
| 5:00 | Player tasks an EO pass over the suspected anchorage. Time compresses to the pass. Result: cloud-obscured. A SAR pass is scheduled instead. | FR-009, FR-008 |
| 9:00 | The agent desk is asked for a recommendation; the trace panel shows it querying the AIS gaps, pass schedule and weather, then proposing "SAR pass over Box B, hold aircraft sortie until T+30 h" with cited evidence IDs. Player edits the plan. | FR-040-FR-043 |
| 14:00 | SAR pass returns two unreported contacts alongside a known tanker, close together and nearly stationary. | FR-007 signatures |
| 18:00 | Player sorties the aircraft. EO/IR turret view shows two hulls alongside; waterline suggests a draft change. | FR-073 |
| 23:00 | Player files an identification claim citing five evidence items. A tip arrives with embedded text trying to redirect the agent; the agent flags it as untrusted and ignores it. | FR-045 |
| 26:00 | Player requests interdiction; cutter intercepts. | Rules, FR-031 |
| 28:00 | Debrief: truth vs knowledge timeline, scoring, agent contribution and cost. | FR-033, FR-043 |

## 5. Traceability
Each FR maps to at least one spike in [SPIKES.md](./SPIKES.md) or an automated test; the mapping is maintained in `docs/TRACEABILITY.md` once implementation begins.
