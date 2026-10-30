# Dark Fleet - Agent Layer Specification

**Status:** Draft v1 - design only. Items tagged `[VERIFY]` depend on external tools or specifications that change quickly.
**Decision:** [ADR 0004](https://github.com/PCSchmidt/hyperreal-platform/blob/main/docs/adr/0004-headless-first-and-agent-isolation.md).

## 1. Purpose
Make LLM agents a **visible, evaluated and secure** part of the game, and make the game a reusable **agent environment and benchmark**. The agent layer demonstrates: multi-agent orchestration, tool use over a typed interface, human-in-the-loop control, grounded generation with citation checking, observability, evaluation methodology, cost/latency engineering and prompt-injection defense.

Hard rules:
1. Agents never mutate simulation state. They propose or submit **actions through the same API as a human**; a human-in-the-loop mode requires approval.
2. Agents see only the **role-filtered observation** (least privilege). The tool surface cannot return truth state.
3. Agent decisions are logged to the replay's AGENT_TRACE; **inputs, not model outputs, are authoritative**, so replays never need to re-query a model.
4. The game is fully playable with agents disabled.
5. In-world text (vessel names, tips, port notes) is **untrusted data**.

## 2. Agent roles

| Agent | Role side | Tools | Output |
|---|---|---|---|
| **Case Officer (orchestrator)** | Hunter | Delegates to specialists; reads evidence board | Ranked plan with rationale and citations |
| **AIS Analyst** | Hunter | `query_ais_tracks`, `gap_features`, `pattern_of_life` | Anomaly report with evidence IDs |
| **Collection Planner** | Hunter | `list_passes`, `task_sensor`(proposal), `weather_at` | Tasking proposals ranked by expected information gain |
| **Air/Surface Ops Planner** | Hunter | `asset_status`, `plan_sortie`(proposal), `intercept_estimate` | Sortie/intercept proposals |
| **Evidence Clerk** | Hunter | `list_evidence`, `link_evidence`, `check_citations` | Case summary, contradiction list, draft claim |
| **Fixer (opponent assistant)** | Runner (optional) | `route_feasibility`, `risk_summary` | Plausible alternate-story suggestions, with risk explanations |
| **Tip/Chatter Generator** | World | Structured facts in; text out | Noisy, partly unreliable in-world reports (facts supplied by the sim; generation constrained to them) |

Orchestration is a thin, explicit state machine (not a framework black box): plan → delegate → verify citations → present. A graph framework may be used if it improves tracing `[VERIFY choice]`; the interface is framework-independent.

## 3. Tool surface and protocol
- Tools are defined once as typed schemas (JSON Schema) in `hr-core` and exposed through (a) in-process calls for the game, and (b) an **MCP server** for external agents `[VERIFY: current MCP revision; recent revisions made the protocol stateless and deprecated some client features - design to the tool/resources primitives and re-check at implementation]`.
- Tools that propose actions return a `Proposal` object (action + preconditions + cost); only `submit_action` after human approval changes state.
- Every tool call is role-scoped by a capability token issued per session; calls outside the role return `E_ROLE_FORBIDDEN`.
- Rate and budget limits per session (calls, tokens, wall time).

## 4. Models and runtime
- **Default:** local open-weight models via a local runtime (already installed on the Dev machine) so the game runs offline and costs nothing `[VERIFY: model availability and licenses; GPU memory fit on a 16 GB card]`.
- **Optional:** hosted models behind the same interface for benchmarking comparisons; keys from environment, never committed.
- Model licenses are recorded separately from code licenses.
- Prompts, tool schemas and evaluation configs are versioned files.

## 5. Grounding and citation checking
- Any factual assertion by an agent must reference evidence IDs. A deterministic **citation checker** verifies: ID exists, is visible to the role, and supports the claim type (position, identity, time, kinematic anomaly) per a rule table.
- Uncited or unsupported assertions are marked *unverified* in the UI and excluded from scoring unless a human promotes them.
- The same checker computes the benchmark's **faithfulness** metric.

## 6. Observability
- Structured traces (OpenTelemetry-compatible spans: agent step, tool call, model call, tokens, latency, cost) stored with the replay.
- In-game **Agent Trace panel** shows plan steps, tool calls, evidence retrieved, citations and costs in real time.
- Offline trace viewer for debugging and write-ups (`[VERIFY: whether to adopt an existing tracing backend or a minimal viewer]`).

## 7. Security: prompt injection and untrusted text
**Threat:** attacker-controlled strings in the world (ship names, destination fields, tip bodies, port notes, document text) attempt to redirect agents, exfiltrate hidden info, or trigger actions.

**Defenses (layered):**
1. **Data/instruction separation:** untrusted fields are passed as structured data with explicit delimiters and labelled untrusted; agents are instructed never to follow instructions found in data.
2. **Least privilege:** no tool returns truth; tools cannot take free-text commands.
3. **Deterministic policy enforcement outside the model:** proposals are validated against rules; destructive or irreversible actions (interdiction, accusation) require human approval.
4. **Output validation:** schema validation and the citation checker.
5. **Tool-result parsing:** only schema-defined fields are returned to the model; free-text fields are length-capped and sanitized.
6. **Adversarial evaluation:** an injection suite with static and *adaptive* attacks (recognizing that defenses validated only on static suites have failed against adaptive attackers in recent literature `[VERIFY citations]`). Residual failure rates are reported honestly, not hidden.

**Metrics:** attack success rate by vector; utility under attack; false-refusal rate; cost overhead.

## 8. DarkFleet-Bench
A benchmark of seeded scenarios for agents-as-players and agent-assisted play.

| Item | Spec |
|---|---|
| Scenarios | Versioned seed lists with difficulty tiers (clear weather, cloud-heavy, decoy-rich, injection-laced) |
| Roles | Hunter agent vs scripted Runner (v1); Runner agent vs scripted Hunter (v1.1) |
| Metrics | Identification accuracy; time-to-identification; false-accusation rate; calibration (Brier/ECE of stated confidence); tool-call validity rate; citation faithfulness; resource efficiency (assets used); cost and latency; injection attack success rate |
| Baselines | Random, rule-based analyst, particle-filter tracker + greedy tasker, human owner (reported with n and caveats) |
| Protocol | Fixed seeds; N repeats per seed; temperature and seed controls; report mean ± 95% CI via bootstrap |
| Ablations | Tools on/off, citation checker on/off, orchestrator vs single agent, model size, local vs hosted |
| Leakage control | Held-out seed set; seeds not used in prompt development |
| Outputs | `bench-result.v1.json`, a leaderboard table in the repo, and a methodology write-up |
| Reproducibility | Pinned scenario generator version, prompts and configs; trace files published |
| License | CC BY 4.0 for the benchmark data and results |

## 9. Evaluation of the agent layer inside the game
- **Assistance effect:** matched-seed comparison of bot-Hunter and human-Hunter performance with/without agent proposals (the human arm is the owner only and is reported as such).
- **Trust calibration:** whether stated confidence correlates with correctness.
- **Human-in-the-loop quality:** approval/edit/reject rates and outcome by decision type.

## 10. Risks
| Risk | Mitigation |
|---|---|
| Local model quality too low for good tool use | Benchmark several sizes; fall back to scripted planners with LLM-generated explanation only; report negative results |
| Nondeterminism of model outputs | Out-of-band; inputs recorded; traces archived |
| Prompt-injection defenses overfit to static tests | Adaptive attack suite; honest reporting |
| Cost/latency in hosted comparisons | Budgets and caps; local default |
| Tooling churn (MCP, frameworks) | Framework-independent tool schemas; re-verify at implementation |
| Agent usefulness is marginal | The benchmark measures it; publish the result either way |
