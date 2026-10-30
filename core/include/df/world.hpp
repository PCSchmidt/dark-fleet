// Dark Fleet world: truth state, role-visible views derived from it, and
// role-filtered observations built ONLY from those views.
//
// Information-flow rule (REQUIREMENTS FR-020/FR-023): observation code never
// receives the truth state. It receives PublicPicture (what sensors reported),
// plus HunterPrivate / RunnerPrivate (each side's own knowledge and resources).
#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "hr/clock.hpp"
#include "hr/error.hpp"
#include "hr/hash_chain.hpp"
#include "hr/rng.hpp"

namespace df {

using json = nlohmann::json;

inline constexpr const char* kBuildId = "df-core-0.2.0";
inline constexpr const char* kRulesVersion = "df-rules-1";

enum class Role { Hunter, Runner };
enum class AisMode { On, Dark, Spoof };
enum class EpisodeState { Running, RunnerDelivered, ClaimResolved, Timeout };

const char* to_string(Role r) noexcept;
const char* to_string(AisMode m) noexcept;
const char* to_string(EpisodeState s) noexcept;
bool role_from_string(const std::string& s, Role& out) noexcept;

// RNG stream ids (stable; never reuse or renumber).
inline constexpr std::uint64_t kStreamTraffic = 1;
inline constexpr std::uint64_t kStreamSensors = 3;
inline constexpr std::uint64_t kStreamSar = 4;

// Size class seen by coarse sensors (derived from vessel class).
const char* size_class_of(const std::string& vessel_class) noexcept;

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
  std::vector<std::pair<double, double>> waypoints;  // followed while non-empty
  std::size_t wp_idx = 0;
  std::vector<std::pair<std::uint64_t, std::uint64_t>> outages;  // [start,end) innocent AIS faults
};

// ---- Derived, role-visible views ----------------------------------------------
struct AisReport {
  std::uint64_t tick = 0;
  std::string mmsi;
  std::string name;
  double x_m = 0, y_m = 0;  // as broadcast (noisy, possibly spoofed)
  double sigma_m = 0;
};

struct SarDetection {
  std::uint64_t serial = 0;
  std::uint64_t tick = 0;
  double x_m = 0, y_m = 0, sigma_m = 0;
  std::string size_class;
};

struct PublicPicture {
  std::vector<AisReport> latest;     // newest AIS report per broadcasting vessel
  std::vector<SarDetection> sar;     // all SAR detections delivered so far
  std::uint64_t ais_max_age_ticks = 0;
  std::uint64_t sar_max_age_ticks = 0;
};

struct PendingTask {
  std::uint64_t exec_tick = 0;
  double x_m = 0, y_m = 0;
};

struct HunterPrivate {
  std::string target_mmsi, target_class;
  std::uint32_t sar_remaining = 0;
  double sar_footprint_radius_m = 0;
  std::vector<PendingTask> pending;
};

struct RunnerPrivate {
  double x_m = 0, y_m = 0, speed_mps = 0, course_deg = 0;
  AisMode ais_mode = AisMode::On;
  double delivery_x_m = 0, delivery_y_m = 0, delivery_radius_m = 0;
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

struct Outcome {
  std::string name = "none";  // none, runner_delivered, correct_supported, insufficient_support, wrong_identity, timeout
  double hunter_score = 0;
  double runner_score = 0;
};

struct ObservationInputs {
  Role role = Role::Hunter;
  std::uint64_t tick = 0;
  std::int64_t utc_ms = 0;
  std::uint64_t seed = 0;
  const PublicPicture* pub = nullptr;
  const RunnerPrivate* runner = nullptr;   // consulted only for Role::Runner
  const HunterPrivate* hunter = nullptr;   // consulted only for Role::Hunter
  const std::vector<Event>* events = nullptr;
  EpisodeState state = EpisodeState::Running;
  std::string outcome = "none";
};

std::string ais_contact_id(std::uint64_t seed, Role role, const std::string& claimed_mmsi);
std::string sar_contact_id(std::uint64_t seed, Role role, std::uint64_t serial);

// Builds an ObservationV1 JSON document from role-visible views only.
json build_observation(const ObservationInputs& in);

class World {
 public:
  World() : clock_(0, 1000) {}
  // Validates the scenario (E_SCHEMA / E_VERSION on failure) and initializes `out`.
  static Status from_scenario(const json& scenario, World& out);

  std::uint64_t tick() const noexcept { return clock_.tick(); }
  std::int64_t utc_ms() const noexcept { return clock_.utc_ms(); }
  std::uint64_t duration_ticks() const noexcept { return duration_ticks_; }
  EpisodeState state() const noexcept { return state_; }
  const Outcome& outcome() const noexcept { return outcome_; }

  // Advances one tick: motion, AIS reporting, due SAR passes, delivery and timeout checks.
  // Returns false (without advancing) once the episode has ended.
  bool step();

  // Validates and applies an action at the current tick. A rejected action never
  // changes simulation state (it only appends an action_rejected event).
  Status submit_action(const Action& a);
  Status submit_action_json(const json& action_doc);

  json observe(Role role) const;

  // Canonical bytes of the full truth state (doubles as bit patterns) and its hash chain.
  std::string canonical_state() const;
  const hr::HashChain& chain() const noexcept { return chain_; }
  std::string state_hash() const { return chain_.head(); }

  // Replay document for this run so far (see replay.hpp).
  json replay() const;
  // Hash over end-of-run state, public detections, events and outcome (covers trailing actions).
  std::string final_state_hash() const;

  // Truth accessors are intentionally explicit: used by tests and verifiers, never by observers.
  const std::vector<Vessel>& truth_vessels() const noexcept { return vessels_; }
  const json& scenario() const noexcept { return scenario_; }
  PublicPicture public_picture() const;
  std::optional<RunnerPrivate> runner_private() const;
  HunterPrivate hunter_private() const;
  const std::vector<Event>& events() const noexcept { return events_; }

 private:
  struct LoggedInput {
    std::uint64_t at_tick;
    json doc;
  };
  Status apply_action(const Action& a);
  void emit_ais_reports();
  void run_due_sar_passes();
  void check_end_conditions();
  void record_hash();
  void finish(EpisodeState s, const Outcome& o);
  std::optional<std::size_t> truth_of_contact(const std::string& contact_id) const;

  hr::FixedClock clock_;
  json scenario_;
  std::uint64_t seed_ = 0;
  std::uint64_t duration_ticks_ = 0;
  double ais_sigma_m_ = 0, ais_report_prob_ = 0;
  std::uint64_t ais_interval_ticks_ = 1, ais_max_age_ticks_ = 1;

  // SAR sensor
  double sar_sigma_m_ = 100, sar_radius_m_ = 30000;
  std::uint64_t sar_delay_ticks_ = 3, sar_max_age_ticks_ = 240;
  double p_detect_large_ = 0.95, p_detect_medium_ = 0.8, p_detect_small_ = 0.5;
  std::uint32_t sar_remaining_ = 0;
  std::uint64_t sar_task_count_ = 0;
  std::vector<PendingTask> pending_;           // ordered by exec tick
  std::vector<SarDetection> sar_detections_;
  std::vector<std::size_t> sar_truth_;         // detection serial -> vessel index (truth only)

  // Mission
  std::string target_mmsi_, target_class_;
  double delivery_x_ = 0, delivery_y_ = 0, delivery_radius_ = 0;

  EpisodeState state_ = EpisodeState::Running;
  Outcome outcome_;

  std::vector<Vessel> vessels_;
  std::unordered_map<std::string, std::size_t> mmsi_to_idx_;
  std::vector<std::optional<AisReport>> last_report_;  // parallel to vessels_
  std::vector<Event> events_;
  std::vector<LoggedInput> inputs_;
  hr::HashChain chain_;
};

}  // namespace df
