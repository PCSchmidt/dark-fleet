#include <algorithm>

#include "df/world.hpp"
#include "hr/sha256.hpp"

namespace df {

const char* to_string(Role r) noexcept { return r == Role::Hunter ? "hunter" : "runner"; }

const char* to_string(AisMode m) noexcept {
  switch (m) {
    case AisMode::On: return "on";
    case AisMode::Dark: return "dark";
    case AisMode::Spoof: return "spoof";
  }
  return "on";
}

bool role_from_string(const std::string& s, Role& out) noexcept {
  if (s == "hunter") { out = Role::Hunter; return true; }
  if (s == "runner") { out = Role::Runner; return true; }
  return false;
}

namespace {
// Opaque, per-observer contact id: a salted hash of the broadcast MMSI. It does not
// expose any truth identifier (truth ids are never an input to this function).
std::string contact_id(std::uint64_t seed, Role role, const std::string& claimed_mmsi) {
  const std::string material = std::to_string(seed) + "|" + to_string(role) + "|" + claimed_mmsi;
  return "c-" + hr::Sha256::hash_hex(material).substr(0, 8);
}
}  // namespace

json build_observation(Role role, std::uint64_t tick, std::int64_t utc_ms, const PublicPicture& pub,
                       const RunnerPrivate* runner_private, const std::vector<Event>& events,
                       std::uint64_t scenario_seed) {
  json obs;
  obs["schema_version"] = "1.0.0";
  obs["role"] = to_string(role);
  obs["tick"] = tick;
  obs["utc_ms"] = utc_ms;

  json contacts = json::array();
  for (const AisReport& r : pub.latest) {
    if (tick < r.tick || tick - r.tick > pub.max_age_ticks) continue;
    contacts.push_back({{"contact_id", contact_id(scenario_seed, role, r.mmsi)},
                        {"source", "ais"},
                        {"claimed_mmsi", r.mmsi},
                        {"claimed_name", r.name},
                        {"x_m", r.x_m},
                        {"y_m", r.y_m},
                        {"sigma_m", r.sigma_m},
                        {"age_ticks", tick - r.tick}});
  }
  std::sort(contacts.begin(), contacts.end(),
            [](const json& a, const json& b) { return a["contact_id"] < b["contact_id"]; });
  obs["contacts"] = std::move(contacts);

  json ev = json::array();
  for (const Event& e : events) {
    // Rejected-action events are only visible to the role that caused them (encoded in kind prefix).
    if (e.kind == "scenario_start") {
      ev.push_back({{"tick", e.tick}, {"kind", e.kind}});
    } else if (e.kind == std::string("action_rejected:") + to_string(role)) {
      ev.push_back({{"tick", e.tick}, {"kind", "action_rejected"}, {"code", e.code}});
    }
  }
  obs["events"] = std::move(ev);

  if (role == Role::Runner && runner_private) {
    obs["own"] = {{"x_m", runner_private->x_m},
                  {"y_m", runner_private->y_m},
                  {"speed_mps", runner_private->speed_mps},
                  {"course_deg", runner_private->course_deg},
                  {"ais_mode", to_string(runner_private->ais_mode)}};
  }
  return obs;
}

}  // namespace df
