#include "df/traffic.hpp"

#include <algorithm>
#include <cmath>
#include <utility>
#include <vector>

namespace df {

namespace {

using Pt = std::pair<double, double>;

// Two crossing lanes, listed in the "forward" direction.
const std::vector<Pt> kLaneA = {{-250000, -70000}, {-100000, -35000}, {0, -5000}, {100000, 15000}, {250000, 40000}};
const std::vector<Pt> kLaneB = {{-60000, -250000}, {-20000, -100000}, {10000, 0}, {40000, 120000}, {70000, 250000}};

struct ClassSpec {
  const char* name;
  double weight;
  double v_lo, v_hi;  // service speed range, m/s
  double v_max;
};
const ClassSpec kClasses[] = {
    {"bulk", 25, 5.5, 7.0, 8.0},        {"container", 20, 8.0, 11.0, 12.5}, {"product_tanker", 20, 6.0, 7.5, 8.5},
    {"aframax", 10, 6.0, 7.5, 8.5},     {"suezmax", 8, 6.0, 7.5, 8.5},       {"vlcc", 7, 6.0, 7.5, 8.5},
    {"fishing", 8, 1.5, 3.0, 5.0},      {"tug", 2, 3.0, 4.5, 6.0},
};

double u(hr::Rng& r) { return r.uniform01(); }

Pt lerp(const Pt& a, const Pt& b, double t) { return {a.first + (b.first - a.first) * t, a.second + (b.second - a.second) * t}; }

double lane_length(const std::vector<Pt>& lane) {
  double l = 0;
  for (std::size_t i = 1; i < lane.size(); ++i) l += std::hypot(lane[i].first - lane[i - 1].first, lane[i].second - lane[i - 1].second);
  return l;
}

// Position at fraction f of the lane, plus the index of the next waypoint.
std::pair<Pt, std::size_t> locate(const std::vector<Pt>& lane, double f) {
  double target = f * lane_length(lane), acc = 0;
  for (std::size_t i = 1; i < lane.size(); ++i) {
    const double seg = std::hypot(lane[i].first - lane[i - 1].first, lane[i].second - lane[i - 1].second);
    if (acc + seg >= target) return {lerp(lane[i - 1], lane[i], (target - acc) / seg), i};
    acc += seg;
  }
  return {lane.back(), lane.size() - 1};
}

std::string pad(int n, int width) {
  std::string s = std::to_string(n);
  return std::string(static_cast<std::size_t>(width) > s.size() ? width - s.size() : 0, '0') + s;
}

}  // namespace

json generate_scenario(const GenParams& p) {
  hr::Rng rng(p.seed, kStreamTraffic);
  json s;
  s["schema_version"] = "1.1.0";
  s["scenario_id"] = "df-gen-" + std::to_string(p.seed);
  s["title_id"] = "dark-fleet";
  s["seed"] = std::to_string(p.seed);
  s["time"] = {{"start_utc_ms", 1793318400000LL}, {"tick_s", p.tick_s}, {"duration_ticks", p.duration_ticks}};
  s["sensors"] = {{"ais", {{"sigma_m", p.ais_sigma_m},
                           {"report_prob", p.ais_report_prob},
                           {"report_interval_ticks", 1},
                           {"max_age_ticks", 12}}},
                  {"sar", {{"sigma_m", 150.0},
                           {"footprint_radius_m", 40000.0},
                           {"delay_ticks", 3},
                           {"max_age_ticks", 240},
                           {"p_detect", {{"large", 0.95}, {"medium", 0.8}, {"small", 0.5}}}}}};
  s["resources"] = {{"sar_tasks", p.sar_tasks}};

  // Runner: a fictional VLCC heading for a delivery point; jittered per seed.
  const double rx = -120000 + (u(rng) - 0.5) * 20000, ry = -40000 + (u(rng) - 0.5) * 20000;
  const double dx = 100000 + (u(rng) - 0.5) * 20000, dy = 20000 + (u(rng) - 0.5) * 20000;
  s["mission"] = {{"delivery", {{"x_m", dx}, {"y_m", dy}, {"radius_m", 2000.0}}},
                  {"target_mmsi", "999000001"},
                  {"target_class", "vlcc"}};

  json vessels = json::array();
  {
    const double course = std::atan2(dx - rx, dy - ry) * 180.0 / 3.14159265358979323846;
    json v;
    v["id"] = "truth-runner";
    v["name"] = "FICTIONAL MERIDIAN STAR";
    v["class"] = "vlcc";
    v["mmsi"] = "999000001";
    v["x_m"] = rx;
    v["y_m"] = ry;
    v["speed_mps"] = 7.0;
    v["course_deg"] = course < 0 ? course + 360.0 : course;
    v["max_speed_mps"] = 8.5;
    v["is_runner"] = true;
    v["ais_mode"] = "on";
    v["secret_note"] = "RUNNER-CANARY-" + std::to_string(rng.next_u64() % 1000000);
    vessels.push_back(std::move(v));
  }

  double total_w = 0;
  for (const auto& c : kClasses) total_w += c.weight;

  for (int i = 0; i < p.n_background; ++i) {
    // Class by weight.
    double pick = u(rng) * total_w;
    const ClassSpec* cls = &kClasses[0];
    for (const auto& c : kClasses) {
      if (pick < c.weight) { cls = &c; break; }
      pick -= c.weight;
    }
    json v;
    v["id"] = "truth-bg-" + std::to_string(i);
    v["name"] = "FICTIONAL TRADER " + pad(i + 1, 2);
    v["class"] = cls->name;
    v["mmsi"] = "9990" + pad(i + 100, 5);
    v["max_speed_mps"] = cls->v_max;
    const double speed = cls->v_lo + (cls->v_hi - cls->v_lo) * u(rng);
    v["speed_mps"] = speed;
    v["is_runner"] = false;
    v["ais_mode"] = "on";
    v["secret_note"] = "BG-CANARY-" + std::to_string(rng.next_u64() % 1000000);

    json wps = json::array();
    double x, y, course;
    if (std::string(cls->name) == "fishing") {
      // Wander between random points.
      x = (u(rng) - 0.5) * 300000;
      y = (u(rng) - 0.5) * 300000;
      for (int k = 0; k < 4; ++k) wps.push_back({x + (u(rng) - 0.5) * 80000, y + (u(rng) - 0.5) * 80000});
      course = std::atan2(wps[0][0].get<double>() - x, wps[0][1].get<double>() - y) * 180.0 / 3.14159265358979323846;
    } else {
      std::vector<Pt> lane = (u(rng) < 0.6) ? kLaneA : kLaneB;
      const bool reverse = u(rng) < 0.5;
      if (reverse) std::reverse(lane.begin(), lane.end());
      const double f = u(rng) * 0.9;
      const auto [pos, next] = locate(lane, f);
      const double lateral = (u(rng) - 0.5) * 6000;  // lane width spread
      x = pos.first + lateral;
      y = pos.second + lateral * 0.5;
      for (std::size_t k = next; k < lane.size(); ++k) wps.push_back({lane[k].first + lateral, lane[k].second + lateral * 0.5});
      course = std::atan2(lane[next].first - x, lane[next].second - y) * 180.0 / 3.14159265358979323846;
    }
    v["x_m"] = x;
    v["y_m"] = y;
    v["course_deg"] = course < 0 ? course + 360.0 : (course >= 360.0 ? course - 360.0 : course);
    v["waypoints"] = wps;

    if (u(rng) < p.outage_probability) {
      const std::uint64_t len = 6 + rng.below(24);  // 30 min - 2.5 h at 5-minute ticks
      const std::uint64_t start = rng.below(p.duration_ticks > len ? p.duration_ticks - len : 1);
      v["outages"] = json::array({json::array({start, start + len})});
    }
    vessels.push_back(std::move(v));
  }
  s["entities"] = {{"vessels", std::move(vessels)}};
  return s;
}

}  // namespace df
