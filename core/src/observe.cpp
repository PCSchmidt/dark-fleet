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

const char* to_string(EpisodeState s) noexcept {
  switch (s) {
    case EpisodeState::Running: return "running";
    case EpisodeState::RunnerDelivered: return "runner_delivered";
    case EpisodeState::ClaimResolved: return "claim_resolved";
    case EpisodeState::Timeout: return "timeout";
  }
  return "running";
}

bool role_from_string(const std::string& s, Role& out) noexcept {
  if (s == "hunter") { out = Role::Hunter; return true; }
  if (s == "runner") { out = Role::Runner; return true; }
  return false;
}

const char* size_class_of(const std::string& c) noexcept {
  if (c == "vlcc" || c == "suezmax") return "large";
  if (c == "fishing" || c == "tug" || c == "cutter") return "small";
  return "medium";
}

// Opaque, per-observer contact ids: salted hashes. They never take a truth identifier as input.
std::string ais_contact_id(std::uint64_t seed, Role role, const std::string& claimed_mmsi) {
  const std::string material = std::to_string(seed) + "|" + to_string(role) + "|ais|" + claimed_mmsi;
  return "c-" + hr::Sha256::hash_hex(material).substr(0, 8);
}

std::string sar_contact_id(std::uint64_t seed, Role role, std::uint64_t serial) {
  const std::string material = std::to_string(seed) + "|" + to_string(role) + "|sar|" + std::to_string(serial);
  return "c-" + hr::Sha256::hash_hex(material).substr(0, 8);
}

json build_observation(const ObservationInputs& in) {
  const Role role = in.role;
  json obs;
  obs["schema_version"] = "1.1.0";
  obs["role"] = to_string(role);
  obs["tick"] = in.tick;
  obs["utc_ms"] = in.utc_ms;

  json contacts = json::array();
  if (in.pub) {
    for (const AisReport& r : in.pub->latest) {
      if (in.tick < r.tick || in.tick - r.tick > in.pub->ais_max_age_ticks) continue;
      contacts.push_back({{"contact_id", ais_contact_id(in.seed, role, r.mmsi)},
                          {"source", "ais"},
                          {"claimed_mmsi", r.mmsi},
                          {"claimed_name", r.name},
                          {"x_m", r.x_m},
                          {"y_m", r.y_m},
                          {"sigma_m", r.sigma_m},
                          {"age_ticks", in.tick - r.tick}});
    }
    for (const SarDetection& d : in.pub->sar) {
      if (in.tick < d.tick || in.tick - d.tick > in.pub->sar_max_age_ticks) continue;
      contacts.push_back({{"contact_id", sar_contact_id(in.seed, role, d.serial)},
                          {"source", "sar"},
                          {"x_m", d.x_m},
                          {"y_m", d.y_m},
                          {"sigma_m", d.sigma_m},
                          {"size_class", d.size_class},
                          {"age_ticks", in.tick - d.tick}});
    }
  }
  std::sort(contacts.begin(), contacts.end(),
            [](const json& a, const json& b) { return a["contact_id"] < b["contact_id"]; });
  obs["contacts"] = std::move(contacts);

  json ev = json::array();
  if (in.events) {
    for (const Event& e : *in.events) {
      if (e.kind == "scenario_start") {
        ev.push_back({{"tick", e.tick}, {"kind", e.kind}});
      } else if (e.kind == std::string("action_rejected:") + to_string(role)) {
        ev.push_back({{"tick", e.tick}, {"kind", "action_rejected"}, {"code", e.code}});
      } else if (e.kind == "claim_filed" || e.kind == "episode_end") {
        ev.push_back({{"tick", e.tick}, {"kind", e.kind}, {"code", e.code}});
      }
    }
  }
  obs["events"] = std::move(ev);
  obs["status"] = {{"state", to_string(in.state)}, {"outcome", in.outcome}};

  if (role == Role::Runner && in.runner) {
    obs["own"] = {{"x_m", in.runner->x_m},
                  {"y_m", in.runner->y_m},
                  {"speed_mps", in.runner->speed_mps},
                  {"course_deg", in.runner->course_deg},
                  {"ais_mode", to_string(in.runner->ais_mode)}};
    if (in.runner->delivery_radius_m > 0.0) {
      obs["mission"] = {{"delivery",
                         {{"x_m", in.runner->delivery_x_m},
                          {"y_m", in.runner->delivery_y_m},
                          {"radius_m", in.runner->delivery_radius_m}}}};
    }
  }
  if (role == Role::Hunter && in.hunter) {
    if (in.hunter->brief_profile) {
      // Profile briefing: the class and where it was last known, never the identifier.
      obs["brief"] = {{"target_class", in.hunter->target_class},
                      {"last_known", {{"x_m", in.hunter->last_known_x_m},
                                      {"y_m", in.hunter->last_known_y_m},
                                      {"sigma_m", in.hunter->last_known_sigma_m},
                                      {"tick", 0}}}};
    } else if (!in.hunter->target_mmsi.empty()) {
      obs["brief"] = {{"target_mmsi", in.hunter->target_mmsi}, {"target_class", in.hunter->target_class}};
    }
    json pend = json::array();
    for (const PendingTask& p : in.hunter->pending)
      pend.push_back({{"exec_tick", p.exec_tick}, {"x_m", p.x_m}, {"y_m", p.y_m}});
    obs["resources"] = {{"sar_tasks_remaining", in.hunter->sar_remaining},
                        {"sar_footprint_radius_m", in.hunter->sar_footprint_radius_m},
                        {"pending_taskings", pend}};
  }
  return obs;
}

}  // namespace df
