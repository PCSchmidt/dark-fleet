#include "df/world.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <set>

#include "hr/sha256.hpp"

namespace df {

namespace {

constexpr double kPi = 3.14159265358979323846;
constexpr double kMaxSpoofOffsetM = 50'000.0;
constexpr double kWaypointReachM = 500.0;

// Standard normal from two counter positions (Box-Muller). Uses libm log/cos/sqrt:
// determinism is guaranteed only for identical builds (platform spec section 4.2).
double normal_at(const hr::Rng& rng, std::uint64_t counter) {
  const double u1 = static_cast<double>((rng.at(counter) >> 11) + 1) * 0x1.0p-53;  // (0, 1]
  const double u2 = static_cast<double>(rng.at(counter + 1) >> 11) * 0x1.0p-53;    // [0, 1)
  return std::sqrt(-2.0 * std::log(u1)) * std::cos(2.0 * kPi * u2);
}

double unit_at(const hr::Rng& rng, std::uint64_t counter) {
  return static_cast<double>(rng.at(counter) >> 11) * 0x1.0p-53;
}

std::uint64_t bits_of(double d) {
  std::uint64_t u;
  std::memcpy(&u, &d, sizeof u);
  return u;
}

bool parse_ais_mode(const std::string& s, AisMode& out) {
  if (s == "on") { out = AisMode::On; return true; }
  if (s == "dark") { out = AisMode::Dark; return true; }
  if (s == "spoof") { out = AisMode::Spoof; return true; }
  return false;
}

Status err(hr::ErrorCode c, std::string msg) { return Status{c, std::move(msg)}; }

template <class T>
bool get(const json& j, const char* key, T& out) {
  if (!j.is_object() || !j.contains(key)) return false;
  try {
    out = j.at(key).get<T>();
  } catch (const json::exception&) {
    return false;
  }
  return true;
}

bool finite_pair(double a, double b) { return std::isfinite(a) && std::isfinite(b); }

}  // namespace

Status World::from_scenario(const json& s, World& out) {
  try {
    if (!s.is_object()) return err(hr::ErrorCode::Schema, "scenario must be an object");
    std::string version, title, seed_str;
    if (!get(s, "schema_version", version) || version.rfind("1.", 0) != 0)
      return err(hr::ErrorCode::Version, "unsupported schema_version");
    if (!get(s, "title_id", title) || title != "dark-fleet") return err(hr::ErrorCode::Schema, "title_id");
    if (!get(s, "seed", seed_str) || seed_str.empty() ||
        seed_str.find_first_not_of("0123456789") != std::string::npos || seed_str.size() > 20)
      return err(hr::ErrorCode::Schema, "seed");

    World w;
    w.scenario_ = s;
    w.seed_ = std::stoull(seed_str);

    const json& t = s.at("time");
    std::int64_t start = 0, tick_s = 0;
    std::uint64_t dur = 0;
    if (!get(t, "start_utc_ms", start) || !get(t, "tick_s", tick_s) || !get(t, "duration_ticks", dur) ||
        start < 0 || tick_s < 1 || tick_s > 3600 || dur < 1)
      return err(hr::ErrorCode::Schema, "time");
    w.clock_ = hr::FixedClock(start, tick_s * 1000);
    w.duration_ticks_ = dur;

    const json& a = s.at("sensors").at("ais");
    if (!get(a, "sigma_m", w.ais_sigma_m_) || w.ais_sigma_m_ <= 0.0) return err(hr::ErrorCode::Schema, "sigma_m must be > 0");
    if (!get(a, "report_prob", w.ais_report_prob_) || w.ais_report_prob_ < 0.0 || w.ais_report_prob_ > 1.0)
      return err(hr::ErrorCode::Schema, "report_prob");
    if (!get(a, "report_interval_ticks", w.ais_interval_ticks_) || w.ais_interval_ticks_ < 1)
      return err(hr::ErrorCode::Schema, "report_interval_ticks");
    if (!get(a, "max_age_ticks", w.ais_max_age_ticks_) || w.ais_max_age_ticks_ < 1)
      return err(hr::ErrorCode::Schema, "max_age_ticks");

    // Optional SAR sensor parameters (defaults documented in schemas/scenario.v1.json).
    if (s.at("sensors").contains("sar")) {
      const json& r = s.at("sensors").at("sar");
      if (!get(r, "sigma_m", w.sar_sigma_m_) || w.sar_sigma_m_ <= 0.0) return err(hr::ErrorCode::Schema, "sar.sigma_m");
      if (!get(r, "footprint_radius_m", w.sar_radius_m_) || w.sar_radius_m_ <= 0.0) return err(hr::ErrorCode::Schema, "sar.footprint_radius_m");
      if (!get(r, "delay_ticks", w.sar_delay_ticks_)) return err(hr::ErrorCode::Schema, "sar.delay_ticks");
      if (!get(r, "max_age_ticks", w.sar_max_age_ticks_) || w.sar_max_age_ticks_ < 1) return err(hr::ErrorCode::Schema, "sar.max_age_ticks");
      const json& p = r.at("p_detect");
      if (!get(p, "large", w.p_detect_large_) || !get(p, "medium", w.p_detect_medium_) || !get(p, "small", w.p_detect_small_))
        return err(hr::ErrorCode::Schema, "sar.p_detect");
    }
    if (s.contains("resources")) {
      std::uint32_t n = 0;
      if (!get(s.at("resources"), "sar_tasks", n)) return err(hr::ErrorCode::Schema, "resources.sar_tasks");
      w.sar_remaining_ = n;
    }
    if (s.contains("mission")) {
      const json& m = s.at("mission");
      const json& d = m.at("delivery");
      if (!get(d, "x_m", w.delivery_x_) || !get(d, "y_m", w.delivery_y_) || !get(d, "radius_m", w.delivery_radius_) ||
          w.delivery_radius_ <= 0.0)
        return err(hr::ErrorCode::Schema, "mission.delivery");
      if (!get(m, "target_mmsi", w.target_mmsi_) || !get(m, "target_class", w.target_class_))
        return err(hr::ErrorCode::Schema, "mission.target");
      if (m.contains("brief")) {
        const json& b = m.at("brief");
        std::string mode;
        if (!b.is_object() || !get(b, "mode", mode) || (mode != "mmsi" && mode != "profile"))
          return err(hr::ErrorCode::Schema, "mission.brief.mode");
        if (mode == "profile") {
          if (!b.contains("last_known")) return err(hr::ErrorCode::Schema, "mission.brief.last_known");
          const json& lk = b.at("last_known");
          if (!get(lk, "x_m", w.last_known_x_) || !get(lk, "y_m", w.last_known_y_) ||
              !get(lk, "sigma_m", w.last_known_sigma_) || w.last_known_sigma_ <= 0.0)
            return err(hr::ErrorCode::Schema, "mission.brief.last_known");
          w.brief_profile_ = true;
        }
      }
    }

    int runners = 0;
    for (const json& v : s.at("entities").at("vessels")) {
      Vessel x;
      std::string mode;
      if (!get(v, "id", x.id) || !get(v, "name", x.name) || !get(v, "class", x.cls) || !get(v, "mmsi", x.mmsi) ||
          !get(v, "x_m", x.x_m) || !get(v, "y_m", x.y_m) || !get(v, "speed_mps", x.speed_mps) ||
          !get(v, "course_deg", x.course_deg) || !get(v, "max_speed_mps", x.max_speed_mps) ||
          !get(v, "is_runner", x.is_runner) || !get(v, "ais_mode", mode) || !parse_ais_mode(mode, x.ais_mode))
        return err(hr::ErrorCode::Schema, "vessel fields");
      if (x.mmsi.size() != 9 || x.mmsi.find_first_not_of("0123456789") != std::string::npos)
        return err(hr::ErrorCode::Schema, "mmsi");
      if (x.speed_mps < 0 || x.speed_mps > x.max_speed_mps || x.course_deg < 0 || x.course_deg >= 360)
        return err(hr::ErrorCode::Schema, "vessel kinematics");
      get(v, "secret_note", x.secret_note);
      if (v.contains("waypoints")) {
        for (const json& p : v.at("waypoints")) {
          if (!p.is_array() || p.size() != 2) return err(hr::ErrorCode::Schema, "waypoint");
          x.waypoints.emplace_back(p[0].get<double>(), p[1].get<double>());
        }
      }
      if (v.contains("outages")) {
        for (const json& o : v.at("outages")) {
          if (!o.is_array() || o.size() != 2) return err(hr::ErrorCode::Schema, "outage");
          x.outages.emplace_back(o[0].get<std::uint64_t>(), o[1].get<std::uint64_t>());
        }
      }
      if (x.is_runner) ++runners;
      if (w.mmsi_to_idx_.count(x.mmsi)) return err(hr::ErrorCode::Schema, "duplicate mmsi");
      w.mmsi_to_idx_[x.mmsi] = w.vessels_.size();
      w.vessels_.push_back(std::move(x));
    }
    if (w.vessels_.empty() || runners != 1) return err(hr::ErrorCode::Schema, "exactly one runner required");

    w.last_report_.assign(w.vessels_.size(), std::nullopt);
    w.events_.push_back({0, "scenario_start", ""});
    w.emit_ais_reports();  // tick 0 picture
    w.record_hash();
    out = std::move(w);
    return {};
  } catch (const json::exception& e) {
    return err(hr::ErrorCode::Schema, e.what());
  }
}

void World::emit_ais_reports() {
  const hr::Rng rng(seed_, kStreamSensors);
  const std::uint64_t t = clock_.tick();
  if (t % ais_interval_ticks_ != 0) return;
  for (std::size_t i = 0; i < vessels_.size(); ++i) {
    const Vessel& v = vessels_[i];
    if (v.ais_mode == AisMode::Dark) continue;
    bool in_outage = false;
    for (const auto& o : v.outages) in_outage |= (t >= o.first && t < o.second);
    if (in_outage) continue;
    // Counter layout is a pure function of (tick, vessel index): independent of iteration history.
    const std::uint64_t base = t * 65536ULL + static_cast<std::uint64_t>(i) * 8ULL;
    if (unit_at(rng, base) >= ais_report_prob_) continue;
    AisReport r;
    r.tick = t;
    r.mmsi = v.mmsi;
    r.name = v.name;
    r.sigma_m = ais_sigma_m_;
    r.x_m = v.x_m + (v.ais_mode == AisMode::Spoof ? v.spoof_dx_m : 0.0) + ais_sigma_m_ * normal_at(rng, base + 1);
    r.y_m = v.y_m + (v.ais_mode == AisMode::Spoof ? v.spoof_dy_m : 0.0) + ais_sigma_m_ * normal_at(rng, base + 3);
    last_report_[i] = std::move(r);
  }
}

void World::run_due_sar_passes() {
  const hr::Rng rng(seed_, kStreamSar);
  const std::uint64_t t = clock_.tick();
  std::vector<PendingTask> remaining;
  std::uint64_t task_no = sar_task_count_ - pending_.size();  // tasks are executed in submission order
  for (const PendingTask& task : pending_) {
    if (task.exec_tick != t) {
      remaining.push_back(task);
      ++task_no;
      continue;
    }
    for (std::size_t i = 0; i < vessels_.size(); ++i) {
      const Vessel& v = vessels_[i];
      const double dx = v.x_m - task.x_m, dy = v.y_m - task.y_m;
      if (std::sqrt(dx * dx + dy * dy) > sar_radius_m_) continue;
      const std::string sc = size_class_of(v.cls);
      const double p = sc == "large" ? p_detect_large_ : (sc == "medium" ? p_detect_medium_ : p_detect_small_);
      const std::uint64_t base = (t * 64ULL + (task_no % 64ULL)) * 65536ULL + static_cast<std::uint64_t>(i) * 8ULL;
      if (unit_at(rng, base) >= p) continue;
      SarDetection d;
      d.serial = sar_detections_.size();
      d.tick = t;
      d.x_m = v.x_m + sar_sigma_m_ * normal_at(rng, base + 1);
      d.y_m = v.y_m + sar_sigma_m_ * normal_at(rng, base + 3);
      d.sigma_m = sar_sigma_m_;
      d.size_class = sc;
      sar_detections_.push_back(d);
      sar_truth_.push_back(i);
    }
    ++task_no;
  }
  pending_ = std::move(remaining);
}

void World::finish(EpisodeState s, const Outcome& o) {
  state_ = s;
  outcome_ = o;
  events_.push_back({clock_.tick(), "episode_end", o.name});
}

void World::check_end_conditions() {
  if (state_ != EpisodeState::Running) return;
  for (const Vessel& v : vessels_) {
    if (!v.is_runner || delivery_radius_ <= 0.0) continue;
    const double dx = v.x_m - delivery_x_, dy = v.y_m - delivery_y_;
    if (std::sqrt(dx * dx + dy * dy) <= delivery_radius_) {
      finish(EpisodeState::RunnerDelivered, {"runner_delivered", 0.0, 100.0});
      return;
    }
  }
  if (clock_.tick() >= duration_ticks_) finish(EpisodeState::Timeout, {"timeout", 0.0, 0.0});
}

bool World::step() {
  if (state_ != EpisodeState::Running) return false;
  const double dt = clock_.dt_seconds();
  for (Vessel& v : vessels_) {
    if (v.wp_idx < v.waypoints.size()) {
      const double dx = v.waypoints[v.wp_idx].first - v.x_m;
      const double dy = v.waypoints[v.wp_idx].second - v.y_m;
      if (std::sqrt(dx * dx + dy * dy) <= std::max(kWaypointReachM, v.speed_mps * dt)) {
        ++v.wp_idx;
      } else {
        double c = std::atan2(dx, dy) * 180.0 / kPi;
        if (c < 0) c += 360.0;
        if (c >= 360.0) c -= 360.0;
        v.course_deg = c;
      }
    }
    const double rad = v.course_deg * kPi / 180.0;
    v.x_m += v.speed_mps * std::sin(rad) * dt;
    v.y_m += v.speed_mps * std::cos(rad) * dt;
  }
  clock_.advance();
  emit_ais_reports();
  run_due_sar_passes();
  check_end_conditions();
  record_hash();
  return true;
}

std::optional<std::size_t> World::truth_of_contact(const std::string& id) const {
  // Reverse-map a Hunter-visible contact id to a truth vessel. Used only for adjudication.
  for (const auto& r : last_report_) {
    if (r && ais_contact_id(seed_, Role::Hunter, r->mmsi) == id) {
      const auto it = mmsi_to_idx_.find(r->mmsi);
      if (it != mmsi_to_idx_.end()) return it->second;
    }
  }
  for (const SarDetection& d : sar_detections_)
    if (sar_contact_id(seed_, Role::Hunter, d.serial) == id) return sar_truth_[static_cast<std::size_t>(d.serial)];
  return std::nullopt;
}

Status World::apply_action(const Action& a) {
  auto reject = [&](hr::ErrorCode c, const std::string& msg) {
    events_.push_back({clock_.tick(), std::string("action_rejected:") + to_string(a.role),
                       std::string(hr::to_string(c))});
    return err(c, msg);
  };

  if (state_ != EpisodeState::Running) return reject(hr::ErrorCode::RuleViolation, "episode has ended");
  if (a.tick != clock_.tick()) return reject(hr::ErrorCode::RuleViolation, "action tick does not match world tick");

  const bool runner_only = a.type == "set_ais_mode" || a.type == "set_course_speed";
  const bool hunter_only = a.type == "task_sensor" || a.type == "file_claim";
  if (a.type != "noop" && !runner_only && !hunter_only) return reject(hr::ErrorCode::Schema, "unknown action_type");
  if (runner_only && a.role != Role::Runner) return reject(hr::ErrorCode::RoleForbidden, "runner-only action");
  if (hunter_only && a.role != Role::Hunter) return reject(hr::ErrorCode::RoleForbidden, "hunter-only action");

  Vessel* runner = nullptr;
  for (Vessel& v : vessels_)
    if (v.is_runner) runner = &v;

  if (a.type == "set_ais_mode") {
    std::string mode;
    AisMode m;
    if (!a.params.is_object() || !get(a.params, "mode", mode) || !parse_ais_mode(mode, m))
      return reject(hr::ErrorCode::Schema, "set_ais_mode params");
    double dx = 0, dy = 0;
    if (m == AisMode::Spoof) {
      if (!get(a.params, "offset_x_m", dx) || !get(a.params, "offset_y_m", dy))
        return reject(hr::ErrorCode::Schema, "spoof requires offsets");
      if (!finite_pair(dx, dy) || std::fabs(dx) > kMaxSpoofOffsetM || std::fabs(dy) > kMaxSpoofOffsetM)
        return reject(hr::ErrorCode::RuleViolation, "spoof offset out of range");
    }
    runner->ais_mode = m;
    runner->spoof_dx_m = dx;
    runner->spoof_dy_m = dy;
  } else if (a.type == "set_course_speed") {
    double course = 0, speed = 0;
    if (!a.params.is_object() || !get(a.params, "course_deg", course) || !get(a.params, "speed_mps", speed) ||
        !finite_pair(course, speed) || course < 0 || course >= 360 || speed < 0)
      return reject(hr::ErrorCode::Schema, "set_course_speed params");
    if (speed > runner->max_speed_mps) return reject(hr::ErrorCode::RuleViolation, "speed exceeds vessel maximum");
    runner->course_deg = course;
    runner->speed_mps = speed;
    runner->wp_idx = runner->waypoints.size();  // manual steering overrides route following
  } else if (a.type == "task_sensor") {
    std::string sensor;
    double x = 0, y = 0;
    if (!a.params.is_object() || !get(a.params, "sensor", sensor) || (sensor != "eo" && sensor != "sar") ||
        !get(a.params, "x_m", x) || !get(a.params, "y_m", y) || !finite_pair(x, y))
      return reject(hr::ErrorCode::Schema, "task_sensor params");
    if (sensor == "eo") return reject(hr::ErrorCode::RuleViolation, "eo sensor not available in this ruleset");
    if (sar_remaining_ == 0) return reject(hr::ErrorCode::Limit, "no SAR taskings remaining");
    --sar_remaining_;
    ++sar_task_count_;
    pending_.push_back({clock_.tick() + sar_delay_ticks_, x, y});
    if (sar_delay_ticks_ == 0) {
      // Immediate pass: execute in this tick without advancing time.
      run_due_sar_passes();
    }
  } else if (a.type == "file_claim") {
    std::string claimed;
    if (!a.params.is_object() || !get(a.params, "contact_id", claimed) || !a.params.contains("evidence") ||
        !a.params.at("evidence").is_array())
      return reject(hr::ErrorCode::Schema, "file_claim params");
    std::vector<std::string> evidence;
    for (const json& e : a.params.at("evidence")) {
      if (!e.is_string()) return reject(hr::ErrorCode::Schema, "evidence entries must be strings");
      evidence.push_back(e.get<std::string>());
    }
    if (evidence.size() > 32) return reject(hr::ErrorCode::Limit, "too many evidence entries");

    // Contact visibility is judged against the Hunter's current view.
    const PublicPicture pub = public_picture();
    const json view = build_observation({Role::Hunter, clock_.tick(), clock_.utc_ms(), seed_, &pub, nullptr, nullptr, &events_,
                                         state_, "none"});
    std::set<std::string> visible;
    for (const json& c : view.at("contacts")) visible.insert(c.at("contact_id").get<std::string>());
    if (!visible.count(claimed)) return reject(hr::ErrorCode::RuleViolation, "claimed contact is not currently visible");

    const auto claimed_idx = truth_of_contact(claimed);
    std::set<std::string> distinct;
    distinct.insert(claimed);
    for (const std::string& e : evidence)
      if (visible.count(e)) distinct.insert(e);
    std::size_t supporting = 0;
    for (const std::string& e : distinct) {
      const auto idx = truth_of_contact(e);
      if (idx && claimed_idx && *idx == *claimed_idx) ++supporting;
    }
    const bool is_runner = claimed_idx && vessels_[*claimed_idx].is_runner;
    Outcome o;
    const double remaining = duration_ticks_ > clock_.tick() ? static_cast<double>(duration_ticks_ - clock_.tick()) : 0.0;
    if (!is_runner) {
      o = {"wrong_identity", -50.0, 50.0};
    } else if (supporting >= 2) {
      o = {"correct_supported", 100.0 + 0.2 * remaining, 0.0};
    } else {
      o = {"insufficient_support", 25.0, 25.0};
    }
    events_.push_back({clock_.tick(), "claim_filed", o.name});
    finish(EpisodeState::ClaimResolved, o);
  }
  return {};
}

Status World::submit_action(const Action& a) {
  json doc = {{"schema_version", "1.1.0"}, {"role", to_string(a.role)}, {"tick", a.tick},
              {"action_type", a.type},     {"params", a.params}};
  inputs_.push_back({clock_.tick(), doc});
  return apply_action(a);
}

Status World::submit_action_json(const json& doc) {
  inputs_.push_back({clock_.tick(), doc});
  Action a;
  std::string role, version;
  if (!doc.is_object() || !get(doc, "schema_version", version) || version.rfind("1.", 0) != 0)
    return err(hr::ErrorCode::Version, "action schema_version");
  if (!get(doc, "role", role) || !role_from_string(role, a.role) || !get(doc, "tick", a.tick) ||
      !get(doc, "action_type", a.type) || !doc.contains("params"))
    return err(hr::ErrorCode::Schema, "action fields");
  a.params = doc.at("params");
  return apply_action(a);
}

PublicPicture World::public_picture() const {
  PublicPicture p;
  p.ais_max_age_ticks = ais_max_age_ticks_;
  p.sar_max_age_ticks = sar_max_age_ticks_;
  for (const auto& r : last_report_)
    if (r) p.latest.push_back(*r);
  p.sar = sar_detections_;
  return p;
}

std::optional<RunnerPrivate> World::runner_private() const {
  for (const Vessel& v : vessels_) {
    if (v.is_runner)
      return RunnerPrivate{v.x_m, v.y_m, v.speed_mps, v.course_deg, v.ais_mode, delivery_x_, delivery_y_, delivery_radius_};
  }
  return std::nullopt;
}

HunterPrivate World::hunter_private() const {
  HunterPrivate h;
  h.target_mmsi = target_mmsi_;
  h.target_class = target_class_;
  h.brief_profile = brief_profile_;
  h.last_known_x_m = last_known_x_;
  h.last_known_y_m = last_known_y_;
  h.last_known_sigma_m = last_known_sigma_;
  h.sar_remaining = sar_remaining_;
  h.sar_footprint_radius_m = sar_radius_m_;
  h.pending = pending_;
  return h;
}

json World::observe(Role role) const {
  const PublicPicture pub = public_picture();
  const auto rp = runner_private();
  const HunterPrivate hp = hunter_private();
  ObservationInputs in;
  in.role = role;
  in.tick = clock_.tick();
  in.utc_ms = clock_.utc_ms();
  in.seed = seed_;
  in.pub = &pub;
  in.runner = rp ? &*rp : nullptr;
  in.hunter = &hp;
  in.events = &events_;
  in.state = state_;
  in.outcome = outcome_.name;
  return build_observation(in);
}

std::string World::canonical_state() const {
  std::string s = "df1;t=" + std::to_string(clock_.tick()) + ";st=" + to_string(state_) + ";sr=" +
                  std::to_string(sar_remaining_) + ";pend=" + std::to_string(pending_.size()) + ";det=" +
                  std::to_string(sar_detections_.size()) + ";out=" + outcome_.name;
  for (const Vessel& v : vessels_) {
    s += ";" + v.id + "," + std::to_string(bits_of(v.x_m)) + "," + std::to_string(bits_of(v.y_m)) + "," +
         std::to_string(bits_of(v.speed_mps)) + "," + std::to_string(bits_of(v.course_deg)) + "," +
         to_string(v.ais_mode) + "," + std::to_string(bits_of(v.spoof_dx_m)) + "," +
         std::to_string(bits_of(v.spoof_dy_m)) + "," + std::to_string(v.wp_idx);
  }
  return s;
}

void World::record_hash() { chain_.append(clock_.tick(), canonical_state()); }

std::string World::final_state_hash() const {
  json d = json::array();
  for (const auto& x : sar_detections_)
    d.push_back({x.serial, x.tick, bits_of(x.x_m), bits_of(x.y_m), x.size_class});
  json e = json::array();
  for (const auto& x : events_) e.push_back({x.tick, x.kind, x.code});
  const std::string material = canonical_state() + "|" + d.dump() + "|" + e.dump() + "|" + outcome_.name + "|" +
                               std::to_string(bits_of(outcome_.hunter_score)) + "|" +
                               std::to_string(bits_of(outcome_.runner_score));
  return hr::Sha256::hash_hex(material);
}

json World::replay() const {
  json r;
  r["schema_version"] = "1.0.0";
  r["title_id"] = "dark-fleet";
  r["build_id"] = kBuildId;
  r["rules_version"] = kRulesVersion;
  r["scenario_hash"] = hr::Sha256::hash_hex(scenario_.dump());
  r["n_ticks"] = clock_.tick();
  json inputs = json::array();
  for (const auto& in : inputs_) inputs.push_back({{"at_tick", in.at_tick}, {"doc", in.doc}});
  r["inputs"] = std::move(inputs);
  json chain = json::array();
  for (const auto& l : chain_.links()) chain.push_back({{"tick", l.tick}, {"hex", l.hex}});
  r["chain"] = std::move(chain);
  r["final_state_hash"] = final_state_hash();
  r["result"] = {{"state", to_string(state_)},
                 {"outcome", outcome_.name},
                 {"hunter_score", outcome_.hunter_score},
                 {"runner_score", outcome_.runner_score}};
  r["content_hash"] = hr::Sha256::hash_hex(r.dump());  // computed before the field exists
  return r;
}

}  // namespace df
