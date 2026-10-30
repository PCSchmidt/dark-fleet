#include <doctest/doctest.h>

#include <string>

#include "df/replay.hpp"
#include "df/traffic.hpp"
#include "hr/sha256.hpp"
#include "test_support.hpp"

using df::Role;

namespace {

struct Run {
  df::json scenario;
  df::json replay;
};

// A scripted episode that exercises both roles, SAR, rejected actions and a claim.
Run play(std::uint64_t seed, bool with_claim) {
  df::GenParams p;
  p.seed = seed;
  p.sar_tasks = 6;
  Run run;
  run.scenario = df::generate_scenario(p);
  run.scenario["sensors"]["sar"]["p_detect"] = {{"large", 1.0}, {"medium", 1.0}, {"small", 1.0}};
  auto w = dft::make_world(run.scenario);
  for (int t = 0; t < 40 && w.state() == df::EpisodeState::Running; ++t) {
    if (t == 5) (void)w.submit_action(dft::action(Role::Runner, w.tick(), "set_ais_mode", {{"mode", "dark"}}));
    if (t == 6) (void)w.submit_action(dft::action(Role::Hunter, w.tick(), "set_ais_mode", {{"mode", "dark"}}));  // rejected
    if (t % 8 == 0) {
      const auto& r = w.truth_vessels()[0];
      (void)w.submit_action(dft::action(Role::Hunter, w.tick(), "task_sensor", {{"sensor", "sar"}, {"x_m", r.x_m}, {"y_m", r.y_m}}));
    }
    if (t == 20) (void)w.submit_action(dft::action(Role::Runner, w.tick(), "set_course_speed", {{"course_deg", 95.0}, {"speed_mps", 6.5}}));
    w.step();
  }
  if (with_claim) {
    std::vector<std::string> sar;
    std::string any_ais;
    for (const auto& c : w.observe(Role::Hunter)["contacts"]) {
      if (c["source"] == "sar") sar.push_back(c["contact_id"]);
      else any_ais = c["contact_id"];
    }
    if (!sar.empty()) (void)w.submit_action(dft::action(Role::Hunter, w.tick(), "file_claim", {{"contact_id", sar[0]}, {"evidence", df::json(sar)}}));
  }
  run.replay = w.replay();
  return run;
}

}  // namespace

TEST_CASE("A recorded run verifies against its scenario and reproduces the outcome") {
  for (std::uint64_t seed : {3ULL, 14ULL, 15ULL, 92ULL}) {
    const auto run = play(seed, true);
    const auto v = df::verify_replay(run.scenario, run.replay);
    INFO(v.message);
    CHECK(v.ok());
    CHECK(v.outcome.name == run.replay["result"]["outcome"].get<std::string>());
  }
}

TEST_CASE("Replays round-trip through JSON text unchanged") {
  const auto run = play(7, true);
  const auto parsed = df::json::parse(run.replay.dump());
  CHECK(df::verify_replay(run.scenario, parsed).ok());
  const auto scenario = df::json::parse(run.scenario.dump());
  CHECK(df::verify_replay(scenario, parsed).ok());
}

namespace {
// An attacker who edits a replay and recomputes its content hash: only re-simulation can catch it.
df::json rehash(df::json r) {
  r.erase("content_hash");
  r["content_hash"] = hr::Sha256::hash_hex(r.dump());
  return r;
}
}  // namespace

TEST_CASE("Tamper: changed input is caught by re-simulation even with a fixed-up content hash") {
  const auto run = play(3, false);
  REQUIRE(run.replay["inputs"].size() > 3);
  auto bad = run.replay;
  for (auto& in : bad["inputs"])
    if (in["doc"]["action_type"] == "set_course_speed") in["doc"]["params"]["course_deg"] = 270.0;
  CHECK(df::verify_replay(run.scenario, bad).code == hr::ErrorCode::Hash);  // naive edit: content hash catches it
  const auto v = df::verify_replay(run.scenario, rehash(bad));
  CHECK(v.code == hr::ErrorCode::Desync);
  CHECK(v.first_bad_tick > 0);
}

TEST_CASE("Tamper: removed input, forged result, forged chain link, wrong scenario") {
  const auto run = play(14, true);
  REQUIRE(df::verify_replay(run.scenario, run.replay).ok());

  {  // delete a runner input
    auto r = run.replay;
    auto& in = r["inputs"];
    for (std::size_t i = 0; i < in.size(); ++i) {
      if (in[i]["doc"]["action_type"] == "set_ais_mode" && in[i]["doc"]["role"] == "runner") {
        in.erase(i);
        break;
      }
    }
    CHECK(df::verify_replay(run.scenario, rehash(r)).code == hr::ErrorCode::Desync);
  }
  {  // forge the recorded winner
    auto r = run.replay;
    r["result"]["hunter_score"] = 9999.0;
    CHECK(df::verify_replay(run.scenario, rehash(r)).code == hr::ErrorCode::Desync);
  }
  {  // forge one chain link
    auto r = run.replay;
    r["chain"][10]["hex"] = std::string(64, 'a');
    const auto v = df::verify_replay(run.scenario, rehash(r));
    CHECK(v.code == hr::ErrorCode::Desync);
    CHECK(v.first_bad_tick == r["chain"][10]["tick"].get<std::uint64_t>());
  }
  {  // modify without re-hashing
    auto r = run.replay;
    r["result"]["outcome"] = "wrong_identity";
    CHECK(df::verify_replay(run.scenario, r).code == hr::ErrorCode::Hash);
  }
  {  // replay of a different scenario
    auto other = run.scenario;
    other["seed"] = "424242";
    CHECK(df::verify_replay(other, run.replay).code == hr::ErrorCode::Hash);
  }
  {  // wrong ruleset
    auto r = run.replay;
    r["rules_version"] = "df-rules-0";
    CHECK(df::verify_replay(run.scenario, rehash(r)).code == hr::ErrorCode::Version);
  }
  {  // garbage
    CHECK(df::verify_replay(run.scenario, df::json::array()).code == hr::ErrorCode::Schema);
    CHECK(df::verify_replay(run.scenario, df::json::object()).code != hr::ErrorCode::Ok);
  }
}

TEST_CASE("Trailing actions after the last step are covered by the final state hash") {
  auto run = play(15, false);
  // Re-run, adding one more accepted action after the last step; replay must still verify.
  df::GenParams p;
  p.seed = 15;
  auto sc = df::generate_scenario(p);
  auto w = dft::make_world(sc);
  for (int i = 0; i < 5; ++i) w.step();
  (void)w.submit_action(dft::action(Role::Runner, w.tick(), "set_ais_mode", {{"mode", "dark"}}));
  const auto r = w.replay();
  CHECK(df::verify_replay(sc, r).ok());
  // Dropping that trailing input must be detected via the final hash (the chain alone would not see it).
  auto forged = r;
  forged["inputs"].erase(forged["inputs"].size() - 1);
  CHECK(df::verify_replay(sc, rehash(forged)).code == hr::ErrorCode::Desync);
}

TEST_CASE("Determinism: two independent runs of the same script produce identical replays") {
  const auto a = play(31, true);
  const auto b = play(31, true);
  CHECK(a.replay.dump() == b.replay.dump());
}
