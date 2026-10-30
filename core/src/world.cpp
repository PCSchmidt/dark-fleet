#include "df/world.hpp"

#include <cmath>
#include <cstring>

#include "hr/sha256.hpp"

namespace df {

namespace {

constexpr double kPi = 3.14159265358979323846;
constexpr double kMaxSpoofOffsetM = 50'000.0;

// Standard normal from two counter positions (Box-Muller). Uses libm log/cos/sqrt:
// determinism is guaranteed only for identical builds (platform spec section 4.2).
double normal_at(const hr::Rng& rng, std::uint64_t counter) {
  const double u1 = static_cast<double>((rng.at(counter) >> 11) + 1) * 0x1.0p-53;  // (0, 1]
  const double u2 = static_cast<double>(rng.at(counter + 1) >> 11) * 0x1.0p-53;    // [0, 1)
  return std::sqrt(-2.0 * std::log(u1)) * std::cos(2.0 * kPi * u2);
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
  if (!j.contains(key)) return false;
  try {
    out = j.at(key).get<T>();
  } catch (const json::exception&) {
    return false;
  }
  return true;
}

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
      if (x.is_runner) ++runners;
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
    // Counter layout is a pure function of (tick, vessel index): independent of iteration history.
    const std::uint64_t base = t * 65536ULL + static_cast<std::uint64_t>(i) * 8ULL;
    const double keep = static_cast<double>(rng.at(base) >> 11) * 0x1.0p-53;
    if (keep >= ais_report_prob_) continue;
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

bool World::step() {
  if (clock_.tick() >= duration_ticks_) return false;
  const double dt = clock_.dt_seconds();
  for (Vessel& v : vessels_) {
    const double rad = v.course_deg * kPi / 180.0;
    v.x_m += v.speed_mps * std::sin(rad) * dt;
    v.y_m += v.speed_mps * std::cos(rad) * dt;
  }
  clock_.advance();
  emit_ais_reports();
  record_hash();
  return true;
}

Status World::submit_action(const Action& a) {
  auto reject = [&](hr::ErrorCode c, const std::string& msg) {
    events_.push_back({clock_.tick(), std::string("action_rejected:") + to_string(a.role),
                       std::string(hr::to_string(c))});
    return err(c, msg);
  };

  if (a.tick != clock_.tick()) return reject(hr::ErrorCode::RuleViolation, "action tick does not match world tick");

  const bool runner_only = a.type == "set_ais_mode" || a.type == "set_course_speed";
  const bool hunter_only = a.type == "task_sensor";
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
      if (std::fabs(dx) > kMaxSpoofOffsetM || std::fabs(dy) > kMaxSpoofOffsetM || !std::isfinite(dx) || !std::isfinite(dy))
        return reject(hr::ErrorCode::RuleViolation, "spoof offset out of range");
    }
    runner->ais_mode = m;
    runner->spoof_dx_m = dx;
    runner->spoof_dy_m = dy;
  } else if (a.type == "set_course_speed") {
    double course = 0, speed = 0;
    if (!a.params.is_object() || !get(a.params, "course_deg", course) || !get(a.params, "speed_mps", speed) ||
        !std::isfinite(course) || !std::isfinite(speed) || course < 0 || course >= 360 || speed < 0)
      return reject(hr::ErrorCode::Schema, "set_course_speed params");
    if (speed > runner->max_speed_mps) return reject(hr::ErrorCode::RuleViolation, "speed exceeds vessel maximum");
    runner->course_deg = course;
    runner->speed_mps = speed;
  } else if (a.type == "task_sensor") {
    std::string sensor;
    double x = 0, y = 0;
    if (!a.params.is_object() || !get(a.params, "sensor", sensor) || (sensor != "eo" && sensor != "sar") ||
        !get(a.params, "x_m", x) || !get(a.params, "y_m", y))
      return reject(hr::ErrorCode::Schema, "task_sensor params");
    // D0: accepted and ignored; sensor tasking arrives in D1.
  }
  return {};
}

Status World::submit_action_json(const json& doc) {
  Action a;
  std::string role, version;
  if (!doc.is_object() || !get(doc, "schema_version", version) || version.rfind("1.", 0) != 0)
    return err(hr::ErrorCode::Version, "action schema_version");
  if (!get(doc, "role", role) || !role_from_string(role, a.role) || !get(doc, "tick", a.tick) ||
      !get(doc, "action_type", a.type) || !doc.contains("params"))
    return err(hr::ErrorCode::Schema, "action fields");
  a.params = doc.at("params");
  return submit_action(a);
}

PublicPicture World::public_picture() const {
  PublicPicture p;
  p.max_age_ticks = ais_max_age_ticks_;
  for (const auto& r : last_report_)
    if (r) p.latest.push_back(*r);
  return p;
}

std::optional<RunnerPrivate> World::runner_private() const {
  for (const Vessel& v : vessels_) {
    if (v.is_runner) return RunnerPrivate{v.x_m, v.y_m, v.speed_mps, v.course_deg, v.ais_mode};
  }
  return std::nullopt;
}

json World::observe(Role role) const {
  const PublicPicture pub = public_picture();
  const auto priv = runner_private();
  return build_observation(role, clock_.tick(), clock_.utc_ms(), pub, priv ? &*priv : nullptr, events_, seed_);
}

std::string World::canonical_state() const {
  std::string s = "df1;t=" + std::to_string(clock_.tick());
  for (const Vessel& v : vessels_) {
    s += ";" + v.id + "," + std::to_string(bits_of(v.x_m)) + "," + std::to_string(bits_of(v.y_m)) + "," +
         std::to_string(bits_of(v.speed_mps)) + "," + std::to_string(bits_of(v.course_deg)) + "," +
         to_string(v.ais_mode) + "," + std::to_string(bits_of(v.spoof_dx_m)) + "," +
         std::to_string(bits_of(v.spoof_dy_m));
  }
  return s;
}

void World::record_hash() { chain_.append(clock_.tick(), canonical_state()); }

}  // namespace df
