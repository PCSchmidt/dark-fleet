#include <doctest/doctest.h>

#include "test_support.hpp"

using df::Role;

TEST_CASE("Hunter cannot issue runner-only actions (E_ROLE_FORBIDDEN)") {
  auto w = dft::make_world(dft::load_example());
  const std::string before = w.canonical_state();
  auto s = w.submit_action(dft::action(Role::Hunter, 0, "set_ais_mode", {{"mode", "dark"}}));
  CHECK(s.code == hr::ErrorCode::RoleForbidden);
  s = w.submit_action(dft::action(Role::Hunter, 0, "set_course_speed", {{"course_deg", 10.0}, {"speed_mps", 1.0}}));
  CHECK(s.code == hr::ErrorCode::RoleForbidden);
  CHECK(w.canonical_state() == before);
}

TEST_CASE("Runner cannot issue hunter-only actions") {
  auto w = dft::make_world(dft::load_example());
  auto s = w.submit_action(dft::action(Role::Runner, 0, "task_sensor", {{"sensor", "eo"}, {"x_m", 0.0}, {"y_m", 0.0}}));
  CHECK(s.code == hr::ErrorCode::RoleForbidden);
}

TEST_CASE("Rule and schema violations are rejected without changing state") {
  auto w = dft::make_world(dft::load_example());
  const std::string before = w.canonical_state();
  CHECK(w.submit_action(dft::action(Role::Runner, 0, "set_course_speed", {{"course_deg", 10.0}, {"speed_mps", 99.0}})).code ==
        hr::ErrorCode::RuleViolation);
  CHECK(w.submit_action(dft::action(Role::Runner, 0, "set_course_speed", {{"course_deg", 400.0}, {"speed_mps", 1.0}})).code ==
        hr::ErrorCode::Schema);
  CHECK(w.submit_action(dft::action(Role::Runner, 0, "set_ais_mode", {{"mode", "spoof"}})).code == hr::ErrorCode::Schema);
  CHECK(w.submit_action(dft::action(Role::Runner, 0, "set_ais_mode", {{"mode", "spoof"}, {"offset_x_m", 1e9}, {"offset_y_m", 0.0}})).code ==
        hr::ErrorCode::RuleViolation);
  CHECK(w.submit_action(dft::action(Role::Runner, 0, "teleport", df::json::object())).code == hr::ErrorCode::Schema);
  CHECK(w.submit_action(dft::action(Role::Runner, 7, "noop", df::json::object())).code == hr::ErrorCode::RuleViolation);
  CHECK(w.canonical_state() == before);
}

TEST_CASE("Valid runner actions change the world; the hunter sees only the rejection of its own bad actions") {
  auto w = dft::make_world(dft::load_example());
  CHECK(w.submit_action(dft::action(Role::Runner, 0, "set_course_speed", {{"course_deg", 90.0}, {"speed_mps", 5.0}})).ok());
  CHECK(w.submit_action(dft::action(Role::Runner, 0, "set_ais_mode", {{"mode", "spoof"}, {"offset_x_m", 5000.0}, {"offset_y_m", -3000.0}})).ok());
  CHECK(w.truth_vessels()[0].course_deg == 90.0);

  (void)w.submit_action(dft::action(Role::Hunter, 0, "set_ais_mode", {{"mode", "dark"}}));  // rejected
  const auto hunter = w.observe(Role::Hunter);
  const auto runner = w.observe(Role::Runner);
  bool hunter_saw_rejection = false, runner_saw_rejection = false;
  for (const auto& e : hunter["events"]) hunter_saw_rejection |= (e["kind"] == "action_rejected");
  for (const auto& e : runner["events"]) runner_saw_rejection |= (e["kind"] == "action_rejected");
  CHECK(hunter_saw_rejection);
  CHECK_FALSE(runner_saw_rejection);
}

TEST_CASE("Spoofed broadcast position is offset from the truth, yet truth never appears") {
  auto w = dft::make_world(dft::load_example());
  REQUIRE(w.submit_action(dft::action(Role::Runner, 0, "set_ais_mode", {{"mode", "spoof"}, {"offset_x_m", 20000.0}, {"offset_y_m", 0.0}})).ok());
  for (int i = 0; i < 5; ++i) w.step();
  const auto obs = w.observe(Role::Hunter);
  const double truth_x = w.truth_vessels()[0].x_m;
  bool found = false;
  for (const auto& c : obs["contacts"]) {
    if (c["claimed_mmsi"] == "999000001") {
      found = true;
      CHECK(c["x_m"].get<double>() > truth_x + 15000.0);
    }
  }
  CHECK(found);
}

TEST_CASE("JSON action entry point enforces versioning and fields") {
  auto w = dft::make_world(dft::load_example());
  df::json bad_version = {{"schema_version", "9.0.0"}, {"role", "runner"}, {"tick", 0}, {"action_type", "noop"}, {"params", df::json::object()}};
  CHECK(w.submit_action_json(bad_version).code == hr::ErrorCode::Version);
  df::json bad_role = {{"schema_version", "1.0.0"}, {"role", "admiral"}, {"tick", 0}, {"action_type", "noop"}, {"params", df::json::object()}};
  CHECK(w.submit_action_json(bad_role).code == hr::ErrorCode::Schema);
  df::json ok = {{"schema_version", "1.0.0"}, {"role", "runner"}, {"tick", 0}, {"action_type", "noop"}, {"params", df::json::object()}};
  CHECK(w.submit_action_json(ok).ok());
}
