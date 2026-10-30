# Dark Fleet

An asymmetric hidden-information cat-and-mouse game on a photoreal ocean: a fictional covert tanker versus analysts, aircraft, cutters and satellites. Real geography; entirely fictional vessels, companies and incidents.

**Status:** D0 and D1 complete (headless): engine-independent C++ core with traffic generator, AIS and SAR sensors, claims and scoring, verified replays, role-filter leak harness, plus Python trackers/bots and an evaluation harness. See [docs/D0_EVIDENCE.md](docs/D0_EVIDENCE.md) and [docs/D1_EVIDENCE.md](docs/D1_EVIDENCE.md). It is not yet a playable game (no UI) and is not balanced.

| Document | Purpose |
|---|---|
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
