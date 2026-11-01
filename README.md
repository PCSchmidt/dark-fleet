# Dark Fleet

An asymmetric hidden-information cat-and-mouse game on a photoreal ocean: a fictional covert tanker versus analysts, aircraft, cutters and satellites. Real geography; entirely fictional vessels, companies and incidents.

**Status:** D0 and D1 complete; D2 to D5 built, a language-model Hunter evaluated, and **rules v3** (the mission requires a loitering transfer) built and evaluated; owner playtest pending. Engine-independent C++ core with traffic generator, AIS and SAR sensors, claims and scoring, verified replays and a role-filter leak harness; Python trackers and bots; a local 2D map client where you can play either role against a bot; an agent layer (Hunter tool surface, citation checker, Case Officer, MCP server, injection suite). See [docs/D0_EVIDENCE.md](docs/D0_EVIDENCE.md), [docs/D1_EVIDENCE.md](docs/D1_EVIDENCE.md) and [docs/D2_EVIDENCE.md](docs/D2_EVIDENCE.md). It is playable but **not balanced**, and nobody has playtested it yet.

| Document | Purpose |
|---|---|
| [docs/RULES_V3.md](docs/RULES_V3.md) | Rules v3: the mission needs a loitering transfer; benchmark v2, three generations of learned Runners and what each exploited, limits |
| [docs/LLM_AGENT.md](docs/LLM_AGENT.md) | A hosted language model as the Hunter: tool use (S15), prompt injection (S17), cost, limits |
| [docs/D5_EVIDENCE.md](docs/D5_EVIDENCE.md) | D5 machine learning: three models, held-out results, the learned-Runner finding, limits |
| [docs/model-cards/](docs/model-cards/) | Model cards: anomaly detector, claim calibrator, learned Runner |
| [docs/BENCH.md](docs/BENCH.md) | DarkFleet-Bench v1: protocol, held-out results with CIs, ablations, negative results |
| [docs/D3_EVIDENCE.md](docs/D3_EVIDENCE.md) | D3 agent layer evidence, spike results S14/S16/S17 (static), limits |
| [docs/D2_EVIDENCE.md](docs/D2_EVIDENCE.md) | D2 verification evidence, bot results, playtest status and limits |
| [docs/D1_EVIDENCE.md](docs/D1_EVIDENCE.md) | D1 verification evidence, tracker study and limits |
| [docs/D0_EVIDENCE.md](docs/D0_EVIDENCE.md) | D0 verification evidence and limits |
| [docs/TRACEABILITY.md](docs/TRACEABILITY.md) | Requirement to evidence map |
| [docs/SPEC.md](docs/SPEC.md) | Architecture and scoping specification |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | User stories, requirement IDs, session walkthrough |
| [docs/AGENTS.md](docs/AGENTS.md) | LLM agent layer, DarkFleet-Bench, prompt-injection security |
| [docs/ML.md](docs/ML.md) | Learned components with baselines, metrics and model cards |
| [docs/PRIOR_ART.md](docs/PRIOR_ART.md) | Prior art and differentiation (initial survey) |
| [docs/SPIKES.md](docs/SPIKES.md) | Verification spike ladder with pass/fail criteria |
| [docs/SOURCES_AND_LICENSES.md](docs/SOURCES_AND_LICENSES.md) | Data, asset and license register |

Part of the hyperreal competitive games collection: [hyperreal-platform](https://github.com/PCSchmidt/hyperreal-platform), [horizon-line](https://github.com/PCSchmidt/horizon-line), [gale-force](https://github.com/PCSchmidt/gale-force), [dark-fleet](https://github.com/PCSchmidt/dark-fleet), [depth-charge](https://github.com/PCSchmidt/depth-charge).

**First title of the collection.** Headless-first: simulation, bots, tracker, agents and benchmark come before any Unreal work; Unreal is used later for EO/SAR/turret sensor views. **Shared code:** the platform repo as a submodule. **Large assets** are kept out of git (see platform ADR 0003).

Personal, non-commercial portfolio project. All licenses are recorded so any later decision stays painless.

## Licenses
Code: [Apache-2.0](LICENSE). Documentation, specifications and benchmark data: [CC BY 4.0](LICENSE-DOCS). Third-party assets and model weights are not covered; see the license register.

## Build and test
```
git submodule update --init
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
python -m pip install -r python/requirements-dev.txt
python -m pytest python/tests -q
```
Requires CMake 3.21+, a C++20 compiler and Python 3.10+.

## Play it (D2)
```
PYTHONPATH=build/python:python python -m darkfleet.play.server
# then open http://127.0.0.1:8765/   (on Windows use ';' in PYTHONPATH)
```
Choose Hunter or Runner, choose the rules (v3, the default, makes the Runner loiter in a transfer zone; v2 is the original game), and press Start. You act on the same role-filtered observations the bots get; the true Runner track is shown only in the debrief, after the replay has been verified by re-simulation. Standard library only; the server binds to localhost.

Hunter games include an optional **Case Officer** panel: it proposes SAR taskings and claims with cited evidence and a visible tool trace, and nothing happens until you approve. Start with `--no-agents` to turn it off; the game is fully playable without it. A model can write the closing summary if you set `DF_LLM=anthropic` and `ANTHROPIC_API_KEY` (untested against the live API; the default needs no model).

### Use a language model (optional)
Put `OPENROUTER_API_KEY=...` and `DF_LLM_MODEL=<model id>` in a `.env` file at the repo root (it is git-ignored). Then:
```
PYTHONPATH=build/python:python python -m darkfleet.bench_llm --variant full --n 10 --max-usd 5 --out docs/bench/llm
```
The run stops at the spend cap you set. Results are sampled and differ run to run; see `docs/LLM_AGENT.md`.

### Learned Runner under rules v3
```
PYTHONPATH=build/python:python python -m darkfleet.ml.runner_v3 --config configs/runner_v3.json --out models/runner_v3    # ~8 min on 12 workers
PYTHONPATH=build/python:python python -m darkfleet.ml.arms_race_v3 --out docs/results/arms_race_v3.json                 # the table in docs/RULES_V3.md
```

### Train the models (D5)
```
python -m pip install -r python/requirements-dev.txt        # includes scikit-learn, onnx, onnxruntime, skl2onnx
PYTHONPATH=build/python:python python -m darkfleet.ml.anomaly    --config configs/anomaly.json    --out models/anomaly      # ~3 min
PYTHONPATH=build/python:python python -m darkfleet.ml.calibrator --config configs/calibrator.json --out models/calibrator   # ~13 min
PYTHONPATH=build/python:python python -m darkfleet.ml.runner_es  --config configs/runner.json     --out models/runner       # ~75 min
```
Trained artifacts are committed under `models/` with their metrics and provenance.

### Run the benchmark
```
PYTHONPATH=build/python:python python -m darkfleet.bench --seeds heldout --out docs/bench                          # rules v2, bench-1
PYTHONPATH=build/python:python python -m darkfleet.bench --rules 3 --seeds heldout --out docs/bench/rules-v3     # rules v3, bench-2
```
Seven agents (random, rule-based, particle filter, audit, Case Officer and two ablations) over five tiers; about six minutes on 24 cores. Results in `docs/bench/`.

### Use the tools from another agent (MCP)
```
PYTHONPATH=build/python:python python -m darkfleet.agent.mcp_server           # human approves proposals
PYTHONPATH=build/python:python python -m darkfleet.agent.mcp_server --auto-approve   # benchmark mode: adds df_decide
```
A stdio MCP server exposing the role-filtered Hunter tools plus `df_new_episode`, `df_step` and `df_status`. Tested in-process, not against a third-party MCP client.
