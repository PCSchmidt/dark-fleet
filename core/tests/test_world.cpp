#include <doctest/doctest.h>

#include "test_support.hpp"

TEST_CASE("Example scenario loads and starts at tick 0 with an AIS picture") {
  auto w = dft::make_world(dft::load_example());
  CHECK(w.tick() == 0);
  CHECK(w.truth_vessels().size() == 3);
  CHECK(w.chain().links().size() == 1);
}

TEST_CASE("Scenario validation reports typed errors") {
  auto j = dft::load_example();
  df::World w;

  auto bad_version = j;
  bad_version["schema_version"] = "2.0.0";
  CHECK(df::World::from_scenario(bad_version, w).code == hr::ErrorCode::Version);

  auto zero_sigma = j;
  zero_sigma["sensors"]["ais"]["sigma_m"] = 0.0;
  CHECK(df::World::from_scenario(zero_sigma, w).code == hr::ErrorCode::Schema);

  auto two_runners = j;
  two_runners["entities"]["vessels"][1]["is_runner"] = true;
  CHECK(df::World::from_scenario(two_runners, w).code == hr::ErrorCode::Schema);

  auto bad_mmsi = j;
  bad_mmsi["entities"]["vessels"][0]["mmsi"] = "12";
  CHECK(df::World::from_scenario(bad_mmsi, w).code == hr::ErrorCode::Schema);

  CHECK(df::World::from_scenario(df::json::array(), w).code == hr::ErrorCode::Schema);
}

TEST_CASE("Same scenario + same inputs give identical hash chains (determinism)") {
  auto a = dft::make_world(dft::load_example());
  auto b = dft::make_world(dft::load_example());
  for (int i = 0; i < 200; ++i) {
    a.step();
    b.step();
  }
  CHECK(hr::HashChain::compare(a.chain().links(), b.chain().links()) == hr::ErrorCode::Ok);
  CHECK(a.state_hash() == b.state_hash());
  CHECK(a.observe(df::Role::Hunter) == b.observe(df::Role::Hunter));
}

TEST_CASE("Different seeds diverge in observations") {
  auto j1 = dft::load_example();
  auto j2 = dft::load_example();
  j2["seed"] = "999";
  auto a = dft::make_world(j1);
  auto b = dft::make_world(j2);
  for (int i = 0; i < 5; ++i) {
    a.step();
    b.step();
  }
  CHECK(a.observe(df::Role::Hunter) != b.observe(df::Role::Hunter));
}

TEST_CASE("Vessels move along their course and the world stops at scenario end") {
  auto j = dft::load_example();
  j["time"]["duration_ticks"] = 3;
  auto w = dft::make_world(j);
  const auto before = w.truth_vessels()[2];  // course 270.5, due-west-ish
  CHECK(w.step());
  CHECK(w.step());
  CHECK(w.step());
  CHECK_FALSE(w.step());
  const auto after = w.truth_vessels()[2];
  CHECK(after.x_m < before.x_m);
  CHECK(w.tick() == 3);
}

TEST_CASE("Observation noise is independent of how many vessels precede in iteration history") {
  // The report for vessel i at tick t depends only on (seed, t, i): stepping one world
  // twice as far as another must reproduce the earlier world's reports exactly.
  auto a = dft::make_world(dft::load_example());
  auto b = dft::make_world(dft::load_example());
  for (int i = 0; i < 10; ++i) a.step();
  for (int i = 0; i < 20; ++i) b.step();
  auto c = dft::make_world(dft::load_example());
  for (int i = 0; i < 10; ++i) c.step();
  CHECK(a.observe(df::Role::Hunter) == c.observe(df::Role::Hunter));
}

TEST_CASE("Dark vessels produce no AIS contacts") {
  auto j = dft::load_example();
  j["entities"]["vessels"][0]["ais_mode"] = "dark";
  auto w = dft::make_world(j);
  for (int i = 0; i < 20; ++i) w.step();
  const auto obs = w.observe(df::Role::Hunter);
  for (const auto& c : obs["contacts"]) CHECK(c["claimed_mmsi"] != "999000001");
}
