# Dark Fleet - Architecture and Scoping Specification

**Created:** 2026-10-29
**Status:** Draft v1 - design only; nothing implemented or verified
**Platform dependency:** [hyperreal-platform](https://github.com/PCSchmidt/hyperreal-platform) (HRCore, HRScenario, HRReplay, HRGeo, HRAtmosphere, HROcean, HRSensors, HRNet, HRRating, HRAgents)
**Companion documents:** [REQUIREMENTS.md](./REQUIREMENTS.md) (user stories, FR IDs, session walkthrough) | [AGENTS.md](./AGENTS.md) (agent layer, DarkFleet-Bench, injection security) | [ML.md](./ML.md) (learned components) | [SPIKES.md](./SPIKES.md) | [PRIOR_ART.md](./PRIOR_ART.md)
**Build order:** first title of the collection; delivered **headless-first** (simulation, bots, agents and benchmark before any Unreal work).

> `[VERIFY]` marks assumptions not yet checked. All vessels, companies, people and incidents are **fictional**; geography is real.

---

## 1. Vision

**Pitch.** An asymmetric cat-and-mouse game on a photoreal ocean. One player commands a sanctions-evading tanker (the **Runner**) trying to complete a covert cargo transfer and delivery. One to three players (the **Hunters**: maritime analysts, patrol aircraft, coast guard assets and satellite tasking) try to find, identify and interdict the vessel using incomplete, delayed, and sometimes deceptive information.

**Player fantasies**
- **Runner:** blend into legitimate traffic, manage a plausible lie, choose when to go dark, and execute a ship-to-ship transfer without being pinned down.
- **Hunter:** fuse weak signals - AIS gaps, kinematic anomalies, satellite passes, patrol sorties - into a defensible track and decide where to commit scarce assets.

**Pillars**
1. **Hidden information is physical.** Detection comes from modeled sensors with real geometry, weather and orbital timing, not arbitrary reveal rolls.
2. **Absence is not proof.** A missing AIS signal is ambiguous (equipment fault, coverage gap, spoof, or evasion) - the same honest-evidence stance as the Regional Analysis Desk.
3. **Deception is the skill.** The Runner wins by creating plausible alternate stories; Hunters win by detecting inconsistency.
4. **Scarcity forces decisions.** Aircraft sorties, satellite passes and cutters are limited and move in real time.
5. **Photoreal where it matters.** The map is the main UI, but each sensor contact resolves into a convincing 3D view (EO satellite chip, SAR image, aircraft camera, bridge view).

**Non-goals:** graphic combat, weapons systems modeling, naming real companies as wrongdoers, real-time AIS feeds, modeling of classified capabilities, a full naval warfare simulator.

---

## 2. Competitive design

### 2.1 Roles and asymmetry

| Role | Information | Actions | Win condition |
|---|---|---|---|
| **Runner** (1) | Knows own state; sees public AIS picture and weather; sees threats only through limited warning (AIS of patrol vessels, radar contacts, rumor tokens) | Navigate, change speed, AIS control (on / off / spoof / identity swap), rendezvous for transfer, false flag, port calls, request services, decoys | Deliver cargo within window with fewer than N identifications; survive interdiction |
| **Hunters** (1-3) | Public AIS, port records (with delay), satellite imagery (after tasking), patrol sensors, tips (noisy) | Task satellites, sortie aircraft, vector cutters, request boarding, share intel, file identification claims | Correctly identify and interdict the Runner before delivery; wrongful interdiction penalized |

Roles are balanced by tuning detection probabilities and resource budgets, verified by bot-vs-bot matches (Section 8).

### 2.2 Modes
| Mode | Description | Netcode |
|---|---|---|
| **Scenario Duel (1v1)** | One Runner vs one Hunter on a seeded region and period | Async turns or live |
| **Task Force (1v2-3)** | Hunters split roles (satellite desk, air ops, surface) | Live/async |
| **Single-player vs AI** | Human plays either side against a learned bot | None |
| **Campaign** | Chained scenarios with persistent reputation, upgrades limited to flavor (no pay-to-win) | None |
| **Daily Trace** | Seeded puzzle: given a day's data, identify the Runner among traffic; scored for accuracy | None |

### 2.3 Scoring and ratings
- Runner: delivery value, stealth bonus, penalties for identification, bonus for deception that causes a Hunter false-positive interdiction.
- Hunter: correct identification with evidence, time-to-identification, interdiction success, penalty for false accusation (calibrated to avoid reckless guessing).
- **Per-role ratings** (TrueSkill-family); match difficulty set by scenario parameters (resource budgets, weather).

### 2.4 Time model
Real-time with **variable time compression** (1×, 10×, 60×, 600×) and pause in single-player. Live multiplayer uses fixed compression with decision windows; async mode uses simultaneous plans per turn (e.g. 3-hour game turns) resolved by the deterministic sim.

---

## 3. Simulation architecture

### 3.1 World model
- **Space:** global ocean on WGS84, regional extents (Gulf of Oman / Strait of Hormuz, Singapore/Malacca, Bab-el-Mandeb/Red Sea, Baltic/Danish straits, South China Sea). Great-circle and rhumb-line routing; land and shallow-water masks from coastline and bathymetry data.
- **Time:** UTC; scenario start date; sun/moon positions for visibility and EO feasibility.
- **State:** vessels (Runner, decoys, background traffic), ports, aircraft, cutters, satellites (fictional constellation with realistic orbital mechanics), weather fields, communication delays.

### 3.2 Vessel dynamics
- **Kinematics:** 3-DOF (surge, sway, yaw) with speed-dependent turn rate, acceleration limits, draft and squat in shallow water, speed reductions in heavy weather (added resistance from waves), and fuel consumption by speed (cubic law).
- **Classes:** VLCC, Suezmax, Aframax, product tanker, LPG/LNG, bulk, container, fishing, tug, offshore supply, naval/coast guard cutter, small boats. Each has dimensions, service speed, radar cross-section, AIS class, and cargo/fuel capacity.
- **Behavior of background traffic:** a statistical traffic generator (Section 5) produces plausible flows: lanes, port calls, anchoring queues, fishing patterns, with timetable consistency.

### 3.3 AIS and identity model
- **AIS transmission:** Class A reporting interval depends on speed/status (seconds underway, minutes at anchor); Class B slower `[VERIFY intervals against ITU-R M.1371]`. Reception depends on terrestrial range (VHF line-of-sight, ducting) and satellite AIS coverage with revisit/latency and message-collision losses in dense areas.
- **Runner AIS controls:** transmit normally; go dark; spoof position (offset or fabricated track, subject to plausibility checks); swap identity (MMSI/IMO/name/flag) with consequences (registry mismatch, inconsistent dimensions, history break); manipulate destination and ETA fields; broadcast from a decoy. Each has a *detectability model* (Section 4.4).
- **Honest ambiguity:** AIS gaps arise naturally (coverage holes, equipment failures, storms) for innocent vessels at a modeled base rate, so a gap alone is weak evidence. A "gap anomaly" score is context-dependent (location, vessel class, history, behavior before/after).

### 3.4 Ship-to-ship (STS) transfer
- Requires two vessels within a few hundred metres, low speed, compatible freeboards, fenders/hoses, calm sea state (Hs below a threshold), duration of hours (cargo-dependent), and time on station.
- Produces signatures: kinematic (co-location at near-zero speed), thermal/visual (EO/IR), AIS patterns (both ships slow in the same box), and draft/waterline change visible in imagery after the transfer.
- Locations: offshore anchorages, quiet sea areas, or at night; detection risk varies with surveillance density.

### 3.5 Weather and environment
HRAtmosphere + HROcean provide wind, sea state, cloud cover, visibility and sea surface roughness. Weather modulates sensor performance (clouds block EO; sea clutter affects radar; high sea states block STS), vessel speeds and aircraft sorties. Scenario-seeded synoptic patterns with plausible evolution (fronts, monsoon, dust).

---

## 4. Sensor and detection architecture (HRSensors)

### 4.1 Framework
Each sensor has: platform pose, sensor geometry, resolution/field of view, revisit/dwell constraints, environment dependencies, and a **detection probability function** `P(detect | target signature, geometry, environment)` plus a **classification confidence function**. Outputs are noisy contacts with position uncertainty ellipses and identification confidence, not ground truth.

### 4.2 Sensor types (fictional systems, public physics)

| Sensor | Platform | Physics handled | Notes |
|---|---|---|---|
| **AIS (terrestrial)** | Shore stations | VHF line-of-sight ~ `3.57(√h1+√h2)` km (more with ducting) | Free, deceivable |
| **AIS (satellite)** | LEO constellation | Pass geometry, footprint, message collision, latency | Coverage gaps |
| **EO imagery (optical)** | Satellite / aircraft | Daylight, cloud cover, resolution (GSD), look angle, haze, sun glint | Weather-dependent; tasking required |
| **SAR** | Satellite / aircraft | All-weather; ship RCS vs sea clutter vs sea state; incidence angle; resolution/swath; synthetic speckle | Detects ships, harder to identify class; wake signatures |
| **RF geolocation** | Satellite | Detects radar/communications emitters; positional error ellipse; unreliable if emitters are off | Reveals EMCON discipline |
| **Maritime patrol aircraft** | Aircraft | Radar horizon `4.12(√h1+√h2)` km (standard refraction), radar range vs RCS, EO/IR turret, endurance and on-station time | Scarce sorties |
| **Surface radar** | Cutters / ships | Horizon-limited detection; sea clutter | Short range |
| **Visual** | Aircraft / ships | Meteorological visibility, target size, contrast, light | Final identification |
| **Human intel (tips)** | — | Noisy reports with reliability and delay | Narrative texture, can be false |
| **Port records** | Agencies | Delay and incompleteness; spoofable documentation | Cross-check tool |

### 4.3 Orbital model
Fictional constellation(s) with realistic orbital parameters (altitude, inclination, RAAN), propagated with SGP4-class mechanics `[VERIFY library choice]`. Passes over any area are computed deterministically from time. This produces authentic **revisit constraints** - "the next EO opportunity over the strait is in 4 h 12 min" - without any real satellite tasking data. Tasking lead times and priority conflicts add decisions.

### 4.4 Detectability of deception
Rather than a single "caught" flag, each deception action leaves **traces** that sensors and analysts can discover:
- Spoofed position vs. radar/SAR contact position mismatch.
- Identity swap vs. dimension/class mismatch in imagery (e.g. a VLCC broadcasting as a product tanker), or draft change inconsistent with reported cargo.
- Course/speed physically impossible for the claimed vessel.
- Pattern-of-life breaks (port call history, flag changes, repeated gaps near the same coordinates).
- STS signatures.
Traces accumulate evidence with source and time; Hunters assemble them into a case with graded confidence, which parallels the Regional Analysis Desk's evidence mechanics and may reuse its content/evidence data model conceptually (not code).

---

## 5. Data and traffic generation

| Item | Approach |
|---|---|
| Ports, anchorages, lanes | OSM, Natural Earth, public traffic-separation-scheme data `[VERIFY terms]`; hand-curated for the five initial regions |
| Statistical traffic | Fit route graphs and speed/course distributions from public AIS datasets (Marine Cadastre, Danish Maritime Authority) `[VERIFY terms]`; generate synthetic voyages; **do not redistribute raw AIS** |
| Vessel particulars | Public class statistics; fictional names, IMO/MMSI numbers in reserved/invalid ranges `[VERIFY ranges]` to avoid colliding with real ships |
| Bathymetry/coast | GEBCO, NOAA; coastline polygons |
| Weather | Seeded synthetic fields, optionally initialized from reanalysis (ERA5/GFS) snapshots |
| Satellites | Orbital elements for fictional spacecraft |
| Imagery assets | Procedural ship kit, Nanite hulls, kit-based decks and cargo; ocean from HROcean; SAR via shader |

The traffic engine is **deterministic from the seed** so the same Daily Trace is identical for everyone.

---

## 6. Rendering and presentation

### 6.1 Operational layer (primary UI)
- Globe/regional map using Cesium for Unreal (free non-commercial tier) or a custom projection with offline basemaps `[VERIFY terms]`; vessel symbols with uncertainty ellipses; layers for AIS, sensors' footprints, weather, orbits and time sliders.
- Information panels: vessel dossier, evidence board, tasking planner, comms/log feed.
- Time controls: pause/compress.

### 6.2 Sensor views (the hyper-real payoff)
| View | Generation |
|---|---|
| **EO satellite chip** | Top-down photoreal render of the 3D scene with sensor model: GSD blur, atmospheric haze, noise, compression artifacts, sun angle, sun glint |
| **SAR image** | Shader pipeline: backscatter by material/orientation, speckle noise, layover/shadow, sea clutter texture by Hs and wind; look-direction artifacts; wake signature |
| **Aircraft EO/IR turret** | Cockpit-style gimbal camera with zoom, thermal palette (white-hot / black-hot), stabilization jitter, haze and atmospheric refraction |
| **Bridge view (Runner)** | First-person bridge/horizon view with night lights, radar scope, ECDIS-like chart |
| **Cutter bridge/RHIB** | Close approach visuals for boarding/identification |
The scene is built on demand from sim state (ships, weather, time) so each contact view is physically consistent with the map.

### 6.3 Performance
Map layer is light; 3D views are on-demand renders at moderate resolution. Target ≥ 60 FPS on the Reference tier in 3D views, with the map layer always responsive `[VERIFY S5]`.

---

## 7. Audio and UX
- Map sounds (UI), radio chatter generated from templates, bridge ambience, aircraft cockpit audio, sonar-like pings as stylized feedback only.
- UX goals: dense but readable information, fast cycles between map and sensor views, evidence board with drag-linking, persistent logs, accessibility (colorblind-safe symbology, text scaling, keyboard operability).

---

## 8. AI and ML (a major showcase)

> Detailed specifications: classical and learned models in [ML.md](./ML.md); LLM agents, DarkFleet-Bench and injection security in [AGENTS.md](./AGENTS.md). The summary below is superseded where they differ.

| Component | Approach |
|---|---|
| **Runner bot** | POMDP-informed planner plus learned policy (RL via self-play against Hunter bot): route choice, AIS manipulation timing, STS site selection |
| **Hunter bot** | Probabilistic tracking (particle filter / multi-hypothesis tracking) over the Runner's possible positions given observations; tasking optimization (greedy information gain) |
| **Evidence fusion assistant** | Bayesian case builder that estimates identity posterior; shows reasoning (explainable) |
| **Anomaly detection** | Learned models (autoencoders / isolation forests) on synthetic tracks to flag AIS gap anomalies and pattern-of-life breaks; evaluation on held-out scenarios |
| **Balance tooling** | Large bot-vs-bot tournaments to tune detection rates and budgets; report win rates by scenario |
| **Daily Trace generator** | Procedural scenarios with verified solvability |
| **Evaluation** | Metrics: detection latency, false positive rate, calibration curves for Hunter identification confidence |

The Hunter-bot tracking problem and Runner-bot deception problem are genuine research-flavored ML tasks and strong portfolio material.

---

## 9. Networking

- **Async-first:** simultaneous planning turns (e.g. 1-6 h game time); players submit plans; the deterministic server resolves; each side receives only its own information. Works over days (play-by-email style), zero live infrastructure.
- **Live:** compressed real-time with 1-4 Hz tick; commands are discrete (orders, tasking) so latency is irrelevant.
- **Information security:** the server never sends hidden state to clients - each client receives a per-role filtered view. This is a core architectural rule (and a tested one).
- **Replays:** full-information replays available post-match; the debrief shows what each side knew vs. truth.

---

## 10. Technical architecture

### 10.1 Layering (headless-first; see platform ADR 0001 and 0004)
```
 Presentation (later)   2D tactical map (first)  ->  Unreal sensor views (EO/SAR/turret)
 -------------------------------------------------------------------------------
 Agent layer            HRAgentKit: tool schemas, MCP server, harness, trace, bench
 -------------------------------------------------------------------------------
 Role-filtered API      ObservationV1 / ActionV1 (the only way anything reads or acts)
 -------------------------------------------------------------------------------
 dark-fleet-core (C++20, no engine dependency, Python bindings)
   DFWorld  DFTraffic  DFVessel  DFAIS  DFSensors  DFEvidence  DFRoles  DFRules
 -------------------------------------------------------------------------------
 hr-core (platform): HRCore, HRScenario, HRReplay, HRGeo, HRSensors, HRRating
```

| Module | Responsibility |
|---|---|
| DFWorld | Truth state, entities, time, scenario loading |
| DFTraffic | Statistical traffic generator, schedules, port calls |
| DFVessel | Kinematics, fuel, AIS controls, STS state machine |
| DFAIS | Transmission and reception models, coverage, anomaly features |
| DFSensors | Sensor definitions on HRSensors; orbit propagation; tasking |
| DFEvidence | Traces, case building, identification claims, scoring |
| DFRoles | Fog-of-war filters, per-role views, action validation |
| DFRules | Win conditions, penalties, debrief generation |
| DFPy | Python bindings, Gymnasium environments, headless runner |
| DFAgents | Classical bots/trackers, learned policy loading (ONNX), agent adapters |
| DFBench | DarkFleet-Bench runner and reporting |
| DFMap (UI) | 2D tactical map and evidence board (first front end) |
| DFViews (UE) | EO/SAR/turret/bridge scene builders (later) |

**Strict separation:** `DFWorld` (truth) -> `DFRoles` (filtered knowledge) -> UI/agents. UI and agent code have no access to truth objects, enforced by module boundaries and the automated information-leak suite (FR-023).

**Front-end choice (first):** a lightweight 2D map client (decision pending among a native C++ immediate-mode UI, a Python desktop UI, or a local web UI served by the Python layer) `[DECIDE at D1; criteria: iteration speed, map rendering, accessibility, ease of recording demos]`. Unreal is introduced at D6 for sensor views only.

**Language split:** C++ core; Python for tooling, ML training, agents, benchmark; headless sim for tournaments and CI.

## 11. Content, data and policy
- **Fictionalization:** invented ship names, owners, ports of registry are fictional; avoid real company names; avoid implying a real flag state is complicit. Real chokepoints and ports appear as geography.
- **Sensitive-topic handling:** present as a game of detection and deception, include a brief in-game note that scenarios are fictional; no operational guidance for real evasion (abstract mechanics only; no real vessel identifiers; no actual spoofing techniques beyond generic concepts).
- **Public-source boundary:** use public references only. Sensor performance values are invented design parameters, labelled as tunable, and are not derived from non-public sources.
- **Licensing:** manifest as per platform; attribution for OSM/Natural Earth/GEBCO/NOAA.

---

## 12. Verification strategy
See [SPIKES.md](./SPIKES.md). Summary:

| Layer | Evidence |
|---|---|
| Information security | Zero hidden-state leaks to the Hunter client across a fuzzed action/state suite |
| Determinism | Re-sim of seeded campaigns reproduces identical truth and sensor outputs |
| Traffic realism | Synthetic traffic statistics match fitted distributions (KS tests) within tolerance |
| Orbital passes | Computed passes match an independent SGP4 implementation |
| Sensor models | Detection curves match design tables; weather correctly modulates EO/SAR |
| Balance | Bot tournaments produce 40-60% Runner win rates on default scenarios |
| Explainability | Every identification outcome can be traced to logged traces |

---

## 13. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Fog-of-war leaks hidden state | Medium | High | Role-filter architecture + leak tests |
| Balance of asymmetric roles | High | High | Bot tournaments; scenario parameters; asymmetric ratings |
| "Feels like a spreadsheet" | High | High | Strong sensor-view payoffs; time pressure; clear evidence UX; prototype early |
| Real-world sensitivity (sanctions evasion theme) | Low-Medium | Medium | Fictionalize; abstract mechanics; clear framing |
| Orbit/scheduling complexity | Medium | Medium | Fictional constellation, simple tasking first |
| SAR and EO realism effort | Medium | Medium | Stylized-but-physically-motivated shaders; spikes |
| Data licensing for traffic | Medium | Medium | Statistical models only; no redistribution |
| Async play depends on opponents | High | Low | Bots as default opponents |
| Scope (many systems) | High | High | Vertical slice: one region, one Runner vs one Hunter, 4 sensor types |

---

## 14. Milestones (gated, effort-agnostic)

| Gate | Deliverable | Exit evidence |
|---|---|---|
| D0 | Platform G0-G2 done; `dark-fleet-core` skeleton; scenario/action/observation schemas; leak-test harness (Spike S1) | CI green; schemas validated; leak suite runs |
| D1 | **Headless vertical slice:** one region, traffic, AIS model, 2 sensor types, evidence and scoring, scripted Runner and baseline Hunter tracker, replay verification | Deterministic replays; tracker beats dead reckoning; US-3 passes |
| D2 | **2D tactical map client:** US-1 and US-2 playable vs bots | Owner playtest recorded honestly |
| D3 | **Agent layer:** HRAgentKit, MCP server, Case Officer + specialists, citation checker, trace panel | US-4 passes; citation checker spike (S14) |
| D4 | **DarkFleet-Bench v1 + injection suite:** baselines, agent results with CIs, write-up | Published results, including negative ones; reproducible with one command |
| D5 | **ML expansion:** anomaly detector, learned Runner via self-play, claim calibrator, model cards | ML.md release checklist satisfied per model |
| D6 | **Unreal sensor views:** EO chip, SAR image, turret view (platform gate G2U) | FR-073 and Spike S5 |
| D7 | Daily Trace and verified async leaderboard | Tampered replay rejected |
| D8 | Task Force multiplayer and campaign (optional) | Role tests, desync tests |

## 15. Open decisions

| Decision | Default | Resolve by |
|---|---|---|
| First region | Gulf of Oman / Strait of Hormuz approaches (high traffic, compact) | D0 |
| Basemap approach | Offline custom vector map from OSM/Natural Earth for the 2D client; Cesium only if needed for the Unreal views | D2 |
| Real-time vs turn-based default | Async turns + optional compression | D1 |
| Agent model defaults | Local open-weight model; hosted optional for comparison | D3 |
| 2D client technology | See 10.1 | D1 |
| Reuse of Regional Analysis Desk evidence model | Concept reuse only | D1 |
| Hunter team roles | Satellite desk / air ops / surface | D5 |
| Thematic framing (sanctions vs smuggling vs piracy) | Neutral "dark fleet" with fictional actors | D0 |
