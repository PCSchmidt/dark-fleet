// Dark Fleet D0 world: truth state, a public AIS picture derived from it, and
// role-filtered observations built ONLY from role-visible views.
//
// Information-flow rule (REQUIREMENTS FR-020/FR-023): observation code never
// receives the truth state. It receives PublicPicture (what AIS receivers
// heard) and, for the runner, RunnerPrivate (the runner's own ship).
#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "hr/clock.hpp"
#include "hr/error.hpp"
#include "hr/hash_chain.hpp"
#include "hr/rng.hpp"

namespace df {

using json = nlohmann::json;

enum class Role { Hunter, Runner };
enum class AisMode { On, Dark, Spoof };

const char* to_string(Role r) noexcept;
const char* to_string(AisMode m) noexcept;
bool role_from_string(const std::string& s, Role& out) noexcept;

// RNG stream ids (stable; never reuse or renumber).
inline constexpr std::uint64_t kStreamSensors = 3;

// ---- Truth (never leaves World except through derived views) ----------------
struct Vessel {
  std::string id;
  std::string name;
  std::string cls;
  std::string mmsi;
  double x_m = 0, y_m = 0;
  double speed_mps = 0, course_deg = 0, max_speed_mps = 0;
  bool is_runner = false;
  AisMode ais_mode = AisMode::On;
  double spoof_dx_m = 0, spoof_dy_m = 0;
  std::string secret_note;
};

// ---- Derived, role-visible views ----------------------------------------------
struct AisReport {
  std::uint64_t tick = 0;
  std::string mmsi;
  std::string name;
  double x_m = 0, y_m = 0;  // as broadcast (noisy, possibly spoofed)
  double sigma_m = 0;
};

struct PublicPicture {
  std::vector<AisReport> latest;  // newest report per broadcasting vessel
  std::uint64_t max_age_ticks = 0;
};

struct RunnerPrivate {
  double x_m = 0, y_m = 0, speed_mps = 0, course_deg = 0;
  AisMode ais_mode = AisMode::On;
};

struct Event {
  std::uint64_t tick = 0;
  std::string kind;
  std::string code;
};

struct Action {
  Role role = Role::Hunter;
  std::uint64_t tick = 0;
  std::string type;
  json params = json::object();
};

struct Status {
  hr::ErrorCode code = hr::ErrorCode::Ok;
  std::string message;
  bool ok() const noexcept { return code == hr::ErrorCode::Ok; }
};

// Builds an ObservationV1 JSON document from role-visible views only.
json build_observation(Role role, std::uint64_t tick, std::int64_t utc_ms, const PublicPicture& pub,
                       const RunnerPrivate* runner_private, const std::vector<Event>& events,
                       std::uint64_t scenario_seed);

class World {
 public:
  World() : clock_(0, 1000) {}
  // Validates the scenario (E_SCHEMA / E_VERSION on failure) and initializes `out`.
  static Status from_scenario(const json& scenario, World& out);

  std::uint64_t tick() const noexcept { return clock_.tick(); }
  std::int64_t utc_ms() const noexcept { return clock_.utc_ms(); }
  std::uint64_t duration_ticks() const noexcept { return duration_ticks_; }

  // Advances one tick: applies motion, then AIS reporting. Returns false past scenario end.
  bool step();

  // Validates and applies an action at the current tick. A rejected action never
  // changes simulation state (only appends an action_rejected event).
  Status submit_action(const Action& a);
  Status submit_action_json(const json& action_doc);

  json observe(Role role) const;

  // Canonical bytes of the full truth state (doubles as bit patterns) and its hash chain.
  std::string canonical_state() const;
  const hr::HashChain& chain() const noexcept { return chain_; }
  std::string state_hash() const { return chain_.head(); }

  // Truth accessors are intentionally explicit: used by tests and verifiers, never by observers.
  const std::vector<Vessel>& truth_vessels() const noexcept { return vessels_; }
  PublicPicture public_picture() const;
  std::optional<RunnerPrivate> runner_private() const;
  const std::vector<Event>& events() const noexcept { return events_; }

 private:
  void emit_ais_reports();
  void record_hash();

  hr::FixedClock clock_;
  std::uint64_t seed_ = 0;
  std::uint64_t duration_ticks_ = 0;
  double ais_sigma_m_ = 0, ais_report_prob_ = 0;
  std::uint64_t ais_interval_ticks_ = 1, ais_max_age_ticks_ = 1;
  std::vector<Vessel> vessels_;
  std::vector<std::optional<AisReport>> last_report_;  // parallel to vessels_
  std::vector<Event> events_;
  hr::HashChain chain_;
};

}  // namespace df
