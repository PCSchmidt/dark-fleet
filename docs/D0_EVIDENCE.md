# D0 Evidence - Skeleton, Schemas and Information-Leak Harness

**Gate:** D0 (see [SPEC.md](./SPEC.md) section 14). **Date:** 2026-10-30.
**Status:** Implemented and verified locally and in CI for the scope below. This is a skeleton, not a game.

## What D0 contains
| Item | Location |
|---|---|
| Platform core library `hr-core` (RNG, SHA-256, hash chain, fixed clock, typed errors) | `extern/hyperreal-platform` (submodule) |
| `dark-fleet-core`: truth world, public AIS picture, role-filtered observation builder, typed action validation | `core/` |
| Python bindings (JSON-in/JSON-out) | `bindings/` |
| JSON Schemas: scenario, observation, action (v1) + example scenario | `schemas/` |
| C++ tests incl. the information-leak fuzz harness and a mutation test | `core/tests/` |
| Python tests: schema conformance, binding behaviour, Python-side leak checks | `python/tests/` |
| CI: Linux and Windows build, C++ and Python tests | `.github/workflows/ci.yml` |

## Design points demonstrated
1. **Structural role filtering.** `build_observation` receives only `PublicPicture` (what AIS receivers heard) and, for the runner, its own ship. It has no access to the truth world, so truth cannot leak by accident; the harness checks it cannot leak by mistake either.
2. **Typed errors end to end** (`E_ROLE_FORBIDDEN`, `E_RULE_VIOLATION`, `E_SCHEMA`, `E_VERSION`), with the guarantee that a rejected action never changes simulation state (fuzz-verified).
3. **Deterministic, history-independent noise.** Sensor noise is a pure function of (seed, tick, vessel index) via a counter-based RNG, so stepping order cannot change earlier results (tested).
4. **Hash chain over canonical state** (doubles as exact bit patterns) with desync location reporting.

## Verification run (2026-10-30, Windows, MSVC 19.44, CMake 3.31, Python 3.14)
| Check | Result |
|---|---|
| `hr-core` unit tests (15 cases, 31,246 assertions: SHA-256 FIPS vectors, SplitMix64 reference sequence, stream independence, hash-chain desync, clock, errors) | Passed |
| `dark-fleet-core` C++ tests (17 cases, 131,390 assertions) | Passed |
| Leak fuzz: 2,000 random scenarios x 25 ticks x 2 roles = 100,000 observations, with random valid/invalid actions from both roles (including a bogus role and unknown action types) | 0 leaks |
| Rejected-action state integrity: every rejected action left the canonical state unchanged | Held (thousands of rejected actions) |
| Mutation test: 600 deliberately leaky observations (exact truth number, secret string, forbidden key) | 600/600 detected |
| Python tests (15): schema validity, bad-document rejection, observation conformance to schema over 50 ticks x 2 roles, typed errors, determinism, Python-side leak fuzz | Passed |
| GitHub Actions CI on `hyperreal-platform` (Ubuntu + Windows) | Passed |
| GitHub Actions CI on `dark-fleet` (Ubuntu + Windows; C++ and Python tests, run 41064306146 at commit 6397b6a) | Passed |

Commands (from a clean clone with `git submodule update --init`):
```
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
python -m pip install -r python/requirements-dev.txt
python -m pytest python/tests -q
```

## Limits - what this does NOT show
- **Not the full Spike S1.** S1 requires 1M fuzz steps and a UI-layer import boundary; D0 ran 100k observations and has no UI yet. The pass criterion for S1 is not claimed.
- **Leak detection is pattern-based.** It catches truth strings, forbidden key names and exact truth numbers. It cannot detect a *derived* leak (for example, a contact whose noise is so small that position is effectively revealed, or ordering that correlates with truth). Statistical leak tests are future work.
- **Determinism is same-build only.** Box-Muller uses libm functions; cross-compiler bit equality is not claimed. A cross-build replay corpus comes with replay support (platform G1).
- **Only one sensor (AIS) and linear motion.** No traffic generator, weather, STS or evidence system yet.
- **Replay container is not implemented**; only the hash chain primitive exists. Actions are not yet logged for replay.
- **Schema/C++ validation duplication.** The C++ loader validates by hand; the JSON Schemas are checked in Python. A single source of truth is a later cleanup.
- **Pass thresholds** in the spike table remain unjustified targets.

## Next (D1 entry)
Traffic generator, second sensor type, evidence/claim scoring, scripted Runner and baseline Hunter tracker, replay container and verification. Items are tracked in the SPEC milestones table.
