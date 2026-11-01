// Rules v3: the mission requires a loitering transfer; completing it is the rule-breaking act.
#include <doctest/doctest.h>

#include <cmath>

#include "df/replay.hpp"
#include "df/traffic.hpp"
#include "test_support.hpp"

using df::Role;

namespace {

df::json v3_scenario(std::uint64_t seed) {
  df::GenParams p;
  p.seed = seed;
  p.transfer = true;
  p.brief_profile = true;
  p.sar_tasks = 12;
  df::json j = df::generate_scenario(p);
  j["sensors"]["sar"]["p_detect"] = {{"large", 1.0}, {"medium", 1.0}, {"small", 1.0}};
  return j;
}

// Put the Runner at rest in the middle of the transfer zone.
df::json parked_in_zone(std::uint64_t seed) {
  df::json j = v3_scenario(seed);
  auto& r = j["entities"]["vessels"][0];
  r["x_m"] = j["mission"]["transfer"]["x_m"];
  r["y_m"] = j["mission"]["transfer"]["y_m"];
  r["speed_mps"] = 0.0;
  return j;
}

df::json contacts_of(const df::World& w) { return w.observe(Role::Hunter)["contacts"]; }

void sar_on_runner(df::World& w) {
  const auto& r = w.truth_vessels()[0];
  REQUIRE(w.submit_action(dft::action(Role::Hunter, w.tick(), "task_sensor", {{"sensor", "sar"}, {"x_m", r.x_m}, {"y_m", r.y_m}})).ok());
  for (int i = 0; i < 3; ++i) w.step();
}

// Contact ids that belong to the Runner (AIS by MMSI, SAR by being within 1.5 km of it).
std::vector<std::string> runner_contacts(const df::World& w) {
  std::vector<std::string> out;
  const auto& r = w.truth_vessels()[0];
  for (const auto& c : contacts_of(w)) {
    if (c["source"] == "ais") {
      if (c["claimed_mmsi"] == r.mmsi) out.push_back(c["contact_id"]);
    } else if (std::hypot(c["x_m"].get<double>() - r.x_m, c["y_m"].get<double>() - r.y_m) < 1500.0) {
      out.push_back(c["contact_id"]);
    }
  }
  return out;
}

df::Outcome claim(df::World& w) {
  const auto ids = runner_contacts(w);
  REQUIRE(ids.size() >= 2);
  REQUIRE(w.submit_action(dft::action(Role::Hunter, w.tick(), "file_claim", {{"contact_id", ids.back()}, {"evidence", ids}})).ok());
  return w.outcome();
}

}  // namespace

TEST_CASE("v3 generator: deterministic, adds zone decoys, and leaves legacy scenarios untouched") {
  const auto a = v3_scenario(31), b = v3_scenario(31);
  CHECK(a.dump() == b.dump());
  CHECK(a["mission"].contains("transfer"));
  CHECK(a["mission"]["brief"].contains("transfer_area"));
  CHECK(a["entities"]["vessels"].size() == 24);  // runner + 20 background + 3 zone decoys

  df::GenParams legacy;
  legacy.seed = 31;
  legacy.brief_profile = true;
  const auto l = df::generate_scenario(legacy);
  CHECK_FALSE(l["mission"].contains("transfer"));
  CHECK(l["entities"]["vessels"].size() == 21);
  // The legacy part of a v3 scenario (the runner and background traffic) is identical to the legacy scenario.
  for (std::size_t i = 0; i < 21; ++i) CHECK(a["entities"]["vessels"][i] == l["entities"]["vessels"][i]);
}

TEST_CASE("v3: the Hunter is told a larger area, never the zone; only the Runner sees its progress") {
  auto w = dft::make_world(parked_in_zone(32));
  const auto j = parked_in_zone(32);
  const auto hunter = w.observe(Role::Hunter);
  CHECK_FALSE(hunter.contains("mission"));
  CHECK(hunter["brief"]["transfer_area"]["radius_m"].get<double>() > j["mission"]["transfer"]["radius_m"].get<double>());
  CHECK(hunter["brief"]["transfer_area"]["required_ticks"] == j["mission"]["transfer"]["required_ticks"]);
  CHECK(hunter["brief"]["transfer_area"]["x_m"] != j["mission"]["transfer"]["x_m"]);
  CHECK(w.observe(Role::Runner)["mission"]["transfer"]["progress_ticks"] == 0);
  for (int i = 0; i < 5; ++i) w.step();
  CHECK(w.observe(Role::Runner)["mission"]["transfer"]["progress_ticks"] == 5);
  CHECK_FALSE(w.observe(Role::Hunter).dump().find("progress_ticks") != std::string::npos);
}

TEST_CASE("v3: only slow ticks inside the zone count toward the transfer") {
  auto j = parked_in_zone(33);
  j["entities"]["vessels"][0]["speed_mps"] = 3.0;  // inside the zone but moving too fast
  j["entities"]["vessels"][0]["course_deg"] = 90.0;
  auto w = dft::make_world(j);
  for (int i = 0; i < 2; ++i) w.step();
  CHECK(w.observe(Role::Runner)["mission"]["transfer"]["progress_ticks"] == 0);
}

TEST_CASE("v3 claims: unfounded before the transfer, correct after it, thin if the zone is never shown") {
  // Before the transfer is complete the right vessel with good evidence is an unfounded accusation.
  {
    auto w = dft::make_world(parked_in_zone(34));
    w.step();
    sar_on_runner(w);
    const auto early = claim(w);
    CHECK(early.name == "unfounded_claim");
    CHECK(early.hunter_score < 0);
    CHECK(early.runner_score > 0);
  }
  // Five loitering ticks is still short of the grounds threshold (6); seven is enough, though the transfer (12) is not done.
  {
    auto w = dft::make_world(parked_in_zone(34));
    for (int i = 0; i < 4; ++i) w.step();
    sar_on_runner(w);  // three more ticks pass while the pass executes: 7 in all
    CHECK(claim(w).name == "correct_supported");
    auto w5 = dft::make_world(parked_in_zone(34));
    for (int i = 0; i < 2; ++i) w5.step();
    sar_on_runner(w5);
    CHECK(claim(w5).name == "unfounded_claim");
  }
  // After 12 loitering ticks the same claim is correct and supported.
  {
    auto w = dft::make_world(parked_in_zone(34));
    for (int i = 0; i < 12; ++i) w.step();
    sar_on_runner(w);
    const auto late = claim(w);
    CHECK(late.name == "correct_supported");
    CHECK(late.hunter_score > 100);
    CHECK(late.runner_score == 0);
  }
  // Transfer done, but every contact filed shows the Runner well away from the zone: not enough.
  {
    auto w = dft::make_world(parked_in_zone(34));
    for (int i = 0; i < 12; ++i) w.step();
    REQUIRE(w.submit_action(dft::action(Role::Runner, w.tick(), "set_course_speed", {{"course_deg", 90.0}, {"speed_mps", 8.0}})).ok());
    for (int i = 0; i < 8; ++i) w.step();  // ~19 km away
    for (int k = 0; k < 2; ++k) sar_on_runner(w);
    CHECK(claim(w).name == "insufficient_support");
  }
}

TEST_CASE("v3 delivery: arriving without the transfer pays the Runner little; with it, in full") {
  df::json j = v3_scenario(35);
  j["mission"]["delivery"]["radius_m"] = 400000.0;  // trivially reachable
  auto w = dft::make_world(j);
  w.step();
  CHECK(w.outcome().name == "delivered_without_transfer");
  CHECK(w.outcome().runner_score == 20.0);
  CHECK(w.outcome().hunter_score == 0.0);

  df::json k = parked_in_zone(35);
  k["mission"]["transfer"]["required_ticks"] = 1;
  k["mission"]["transfer"]["grounds_ticks"] = 1;
  k["mission"]["delivery"]["radius_m"] = 400000.0;
  auto w2 = dft::make_world(k);
  w2.step();
  CHECK(w2.outcome().name == "runner_delivered");
  CHECK(w2.outcome().runner_score == 100.0);
}

TEST_CASE("v3: a recorded episode verifies by re-simulation, and the transfer is part of the state hash") {
  const auto j = parked_in_zone(36);
  auto w = dft::make_world(j);
  for (int i = 0; i < 12; ++i) w.step();
  sar_on_runner(w);
  claim(w);
  const auto v = df::verify_replay(j, w.replay());
  CHECK(v.ok());
  CHECK(v.outcome.name == "correct_supported");

  auto fast = parked_in_zone(36);
  fast["entities"]["vessels"][0]["speed_mps"] = 3.0;
  fast["entities"]["vessels"][0]["course_deg"] = 90.0;
  auto w2 = dft::make_world(fast), w3 = dft::make_world(j);
  w2.step();
  w3.step();
  CHECK(w2.state_hash() != w3.state_hash());
}
