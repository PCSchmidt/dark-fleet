// Replay verification (US-3, FR-001, FR-032).
//
// A replay is produced by World::replay(). verify_replay() re-simulates the scenario
// from the recorded inputs and checks, in order:
//   1. structure, versions and the replay content hash          (E_VERSION / E_SCHEMA / E_HASH)
//   2. that the replay belongs to the supplied scenario          (E_HASH)
//   3. every per-tick state-hash link                            (E_DESYNC, with first bad tick)
//   4. the end-of-run state hash and the recorded result         (E_DESYNC / E_HASH)
// Scores are therefore a pure function of (scenario, inputs): the recorded result is
// checked against the re-simulated one rather than trusted.
#pragma once
#include <cstdint>

#include "df/world.hpp"

namespace df {

struct VerifyResult {
  hr::ErrorCode code = hr::ErrorCode::Ok;
  std::string message;
  std::uint64_t first_bad_tick = 0;
  Outcome outcome;  // re-simulated outcome (valid when code == Ok)
  bool ok() const noexcept { return code == hr::ErrorCode::Ok; }
};

VerifyResult verify_replay(const json& scenario, const json& replay);

}  // namespace df
