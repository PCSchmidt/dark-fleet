#include <doctest/doctest.h>

#include <cmath>
#include <set>

#include "df/traffic.hpp"
#include "test_support.hpp"

using df::Role;

namespace {

df::World gen_world(std::uint64_t seed, int n_bg = 20) {
  df::GenParams p;
  p.seed = seed;
  p.n_background = n_bg;
  return dft::make_world(df::generate_scenario(p));
}

df::json contacts_of(const df::World& w, Role r = Role::Hunter) { return w.observe(r)["contacts"]; }

}  // namespace

TEST_CASE("Traffic generator: deterministic, valid, and varied across seeds") {
  df::GenParams p;
  p.seed = 77;
  const auto a = df::generate_scenario(p);
  const auto b = df::generate_scenario(p);
  CHECK(a.dump() == b.dump());
  p.seed = 78;
  CHECK(df::generate_scenario(p).dump() != a.dump());

  df::World w;
  CHECK(df::World::from_scenario(a, w).ok());
  CHECK(w.truth_vessels().size() == 21);
  CHECK(w.truth_vessels()[0].is_runner);
}

TEST_CASE("Traffic generator: class mix and innocent AIS outages follow the configuration") {
  df::GenParams p;
  p.seed = 5;
  p.n_background = 400;
  const auto s = df::generate_scenario(p);
  std::size_t outages = 0, fishing = 0, large = 0;
  for (const auto& v : s["entities"]["vessels"]) {
    if (v.value("is_runner", false)) continue;
    if (v.contains("outages")) ++outages;
    const std::string c = v["class"];
    fishing += (c == "fishing");
    large += (c == "vlcc" || c == "suezmax");
  }
  CHECK(outages > 400 * 0.2);
  CHECK(outages < 400 * 0.4);
  CHECK(fishing > 400 * 0.04);
  CHECK(fishing < 400 * 0.13);
  CHECK(large > 400 * 0.08);
}

TEST_CASE("Background vessels follow their lane waypoints") {
  auto w = gen_world(11, 10);
  const auto before = w.truth_vessels();
  for (int i = 0; i < 30; ++i) w.step();
  std::size_t moved = 0;
  for (std::size_t i = 1; i < before.size(); ++i) {
    const double d = std::hypot(w.truth_vessels()[i].x_m - before[i].x_m, w.truth_vessels()[i].y_m - before[i].y_m);
    moved += d > 1000.0;
  }
  CHECK(moved >= 8);
}

TEST_CASE("SAR tasking: limit, delay, footprint and size-dependent detection") {
  df::GenParams p;
  p.seed = 21;
  p.sar_tasks = 2;
  auto w = dft::make_world(df::generate_scenario(p));
  const auto cx = w.truth_vessels()[0].x_m, cy = w.truth_vessels()[0].y_m;  // runner position
  const auto task = [&](std::uint64_t t) {
    return dft::action(Role::Hunter, t, "task_sensor", {{"sensor", "sar"}, {"x_m", cx}, {"y_m", cy}});
  };
  REQUIRE(w.submit_action(task(0)).ok());
  CHECK(w.observe(Role::Hunter)["resources"]["sar_tasks_remaining"] == 1);
  CHECK(w.observe(Role::Hunter)["resources"]["pending_taskings"].size() == 1);

  // Nothing delivered before the pass; delivered at the delay (3 ticks).
  for (int i = 0; i < 2; ++i) w.step();
  for (const auto& c : contacts_of(w)) CHECK(c["source"] != "sar");
  w.step();
  std::size_t sar = 0;
  for (const auto& c : contacts_of(w)) {
    if (c["source"] == "sar") {
      ++sar;
      CHECK_FALSE(c.contains("claimed_mmsi"));
      CHECK(c["age_ticks"] == 0);
    }
  }
  CHECK(sar >= 1);

  REQUIRE(w.submit_action(task(w.tick())).ok());
  CHECK(w.submit_action(task(w.tick())).code == hr::ErrorCode::Limit);
  CHECK(w.submit_action(dft::action(Role::Hunter, w.tick(), "task_sensor", {{"sensor", "eo"}, {"x_m", 0.0}, {"y_m", 0.0}})).code ==
        hr::ErrorCode::RuleViolation);
}

TEST_CASE("SAR detects the runner even when its AIS is dark") {
  df::GenParams p;
  p.seed = 33;
  p.sar_tasks = 20;
  auto w = dft::make_world(df::generate_scenario(p));
  REQUIRE(w.submit_action(dft::action(Role::Runner, 0, "set_ais_mode", {{"mode", "dark"}})).ok());
  int hits = 0, tries = 0;
  for (int k = 0; k < 15; ++k) {
    const auto& r = w.truth_vessels()[0];
    REQUIRE(w.submit_action(dft::action(Role::Hunter, w.tick(), "task_sensor", {{"sensor", "sar"}, {"x_m", r.x_m + 2000.0}, {"y_m", r.y_m}})).ok());
    for (int i = 0; i < 3; ++i) w.step();
    ++tries;
    for (const auto& c : contacts_of(w)) {
      if (c["source"] != "sar" || c["age_ticks"] != 0) continue;
      const auto& rr = w.truth_vessels()[0];
      if (std::hypot(c["x_m"].get<double>() - rr.x_m, c["y_m"].get<double>() - rr.y_m) < 1500.0) {
        ++hits;
        break;
      }
    }
  }
  CHECK(hits >= 10);   // p_detect(large)=0.95, with ties to other vessels in the footprint
  CHECK(hits <= tries);
  for (const auto& c : contacts_of(w)) CHECK(c.value("claimed_mmsi", "") != "999000001");
}

TEST_CASE("Claims: wrong identity, insufficient support, and correct supported outcomes") {
  const auto run_claim = [](int which, bool violate = true) {
    df::GenParams p;
    p.seed = 90;
    p.sar_tasks = 10;
    auto j = df::generate_scenario(p);
    j["sensors"]["sar"]["p_detect"] = {{"large", 1.0}, {"medium", 1.0}, {"small", 1.0}};
    auto w = dft::make_world(j);
    if (violate) {  // the Runner goes dark for a moment, then back on: it has broken the rules (v2)
      REQUIRE(w.submit_action(dft::action(Role::Runner, w.tick(), "set_ais_mode", {{"mode", "dark"}})).ok());
      w.step();
      REQUIRE(w.submit_action(dft::action(Role::Runner, w.tick(), "set_ais_mode", {{"mode", "on"}})).ok());
    }
    // Two SAR passes over the runner give two SAR contacts of the same vessel.
    for (int k = 0; k < 2; ++k) {
      const auto& r = w.truth_vessels()[0];
      REQUIRE(w.submit_action(dft::action(Role::Hunter, w.tick(), "task_sensor", {{"sensor", "sar"}, {"x_m", r.x_m}, {"y_m", r.y_m}})).ok());
      for (int i = 0; i < 3; ++i) w.step();
    }
    std::string runner_ais, other_ais;
    std::vector<std::string> runner_sar;
    const auto& r = w.truth_vessels()[0];
    for (const auto& c : contacts_of(w)) {
      const double d = std::hypot(c["x_m"].get<double>() - r.x_m, c["y_m"].get<double>() - r.y_m);
      if (c["source"] == "ais") {
        if (c["claimed_mmsi"] == "999000001") runner_ais = c["contact_id"]; else other_ais = c["contact_id"];
      } else if (d < 1500.0) {
        runner_sar.push_back(c["contact_id"]);
      }
    }
    REQUIRE_FALSE(runner_ais.empty());
    REQUIRE_FALSE(other_ais.empty());
    REQUIRE(runner_sar.size() >= 1);
    df::json params;
    if (which == 0) params = {{"contact_id", other_ais}, {"evidence", df::json::array({other_ais})}};             // wrong identity
    if (which == 1) params = {{"contact_id", runner_ais}, {"evidence", df::json::array({runner_ais})}};           // right, thin
    if (which == 2) params = {{"contact_id", runner_ais}, {"evidence", df::json::array({runner_ais, runner_sar[0]})}};  // right, supported
    REQUIRE(w.submit_action(dft::action(Role::Hunter, w.tick(), "file_claim", params)).ok());
    return std::make_pair(w.outcome(), w.state());
  };
  const auto wrong = run_claim(0);
  CHECK(wrong.first.name == "wrong_identity");
  CHECK(wrong.first.hunter_score < 0);
  CHECK(wrong.second == df::EpisodeState::ClaimResolved);
  const auto thin = run_claim(1);
  CHECK(thin.first.name == "insufficient_support");
  const auto innocent = run_claim(2, false);  // same evidence, but the Runner never went dark or spoofed
  CHECK(innocent.first.name == "unfounded_claim");
  CHECK(innocent.first.hunter_score < 0);
  CHECK(innocent.first.runner_score > 0);
  const auto good = run_claim(2);
  CHECK(good.first.name == "correct_supported");
  CHECK(good.first.hunter_score > 100);
  CHECK(good.first.runner_score == 0);
}

TEST_CASE("Claims about unknown or invisible contacts are rejected without ending the episode") {
  auto w = gen_world(8);
  CHECK(w.submit_action(dft::action(Role::Hunter, 0, "file_claim", {{"contact_id", "c-00000000"}, {"evidence", df::json::array()}})).code ==
        hr::ErrorCode::RuleViolation);
  CHECK(w.state() == df::EpisodeState::Running);
  CHECK(w.submit_action(dft::action(Role::Hunter, 0, "file_claim", {{"contact_id", "c-00000000"}})).code == hr::ErrorCode::Schema);
  CHECK(w.submit_action(dft::action(Role::Runner, 0, "file_claim", {{"contact_id", "c-00000000"}, {"evidence", df::json::array()}})).code ==
        hr::ErrorCode::RoleForbidden);
}

TEST_CASE("Episode ends when the runner is delivered; later actions are rejected") {
  df::GenParams p;
  p.seed = 4;
  auto j = df::generate_scenario(p);
  j["mission"]["delivery"]["radius_m"] = 400000.0;  // trivially reachable
  auto w = dft::make_world(j);
  CHECK(w.step());
  CHECK(w.state() == df::EpisodeState::RunnerDelivered);
  CHECK(w.outcome().runner_score == 100.0);
  CHECK_FALSE(w.step());
  CHECK(w.submit_action(dft::action(Role::Runner, w.tick(), "noop", df::json::object())).code == hr::ErrorCode::RuleViolation);
  CHECK(w.observe(Role::Hunter)["status"]["outcome"] == "runner_delivered");
}

TEST_CASE("Timeout ends the episode with no winner") {
  df::GenParams p;
  p.seed = 4;
  p.duration_ticks = 2;
  auto w = dft::make_world(df::generate_scenario(p));
  w.step();
  w.step();
  CHECK(w.state() == df::EpisodeState::Timeout);
  CHECK(w.outcome().hunter_score == 0.0);
  CHECK_FALSE(w.step());
}

TEST_CASE("Innocent outages silence AIS for non-runners; hunter cannot tell outage from dark") {
  df::GenParams p;
  p.seed = 2;
  p.n_background = 5;
  p.outage_probability = 1.0;
  p.ais_report_prob = 1.0;
  auto j = df::generate_scenario(p);
  j["entities"]["vessels"][1]["outages"] = df::json::array({df::json::array({2, 40})});
  auto w = dft::make_world(j);
  for (int i = 0; i < 20; ++i) w.step();
  const std::string silent = j["entities"]["vessels"][1]["mmsi"];
  for (const auto& c : contacts_of(w))
    if (c["source"] == "ais") CHECK(c["claimed_mmsi"] != silent);
}
