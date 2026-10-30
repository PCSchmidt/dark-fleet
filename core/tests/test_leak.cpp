// Information-leak harness (Spike S1 / FR-023).
//
// Strategy: seeded fuzzing of scenarios and actions; every observation for every
// role is serialized and scanned for
//   (a) truth-only strings (ids, secret notes) and truth-only key names,
//   (b) exact truth numbers (any non-integral double equal to a truth value ever
//       held by any vessel at any tick).
// A mutation test proves the harness is not vacuous: an intentionally leaky
// observer must be detected.
#include <doctest/doctest.h>

#include <algorithm>
#include <cctype>
#include <set>
#include <sstream>

#include "df/world.hpp"
#include "hr/rng.hpp"
#include "test_support.hpp"

namespace {

struct TruthLedger {
  std::set<std::string> secret_strings;
  std::set<double> secret_numbers;

  void record(const df::World& w) {
    for (const auto& v : w.truth_vessels()) {
      secret_strings.insert(v.id);
      if (!v.secret_note.empty()) secret_strings.insert(v.secret_note);
      for (double d : {v.x_m, v.y_m, v.speed_mps, v.course_deg, v.max_speed_mps}) {
        if (d != static_cast<double>(static_cast<long long>(d))) secret_numbers.insert(d);
      }
    }
  }
};

bool key_is_forbidden(const std::string& key) {
  std::string k;
  for (char c : key) k.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
  return k.find("truth") != std::string::npos || k.find("secret") != std::string::npos ||
         k.find("hidden") != std::string::npos || k.rfind("true_", 0) == 0;
}

void scan(const df::json& j, const TruthLedger& ledger, std::vector<std::string>& findings, const std::string& path) {
  if (j.is_object()) {
    for (auto it = j.begin(); it != j.end(); ++it) {
      if (key_is_forbidden(it.key())) findings.push_back("forbidden key at " + path + "/" + it.key());
      scan(it.value(), ledger, findings, path + "/" + it.key());
    }
  } else if (j.is_array()) {
    for (std::size_t i = 0; i < j.size(); ++i) scan(j[i], ledger, findings, path + "/" + std::to_string(i));
  } else if (j.is_string()) {
    const std::string s = j.get<std::string>();
    for (const auto& secret : ledger.secret_strings)
      if (s.find(secret) != std::string::npos) findings.push_back("secret string at " + path);
  } else if (j.is_number_float()) {
    const double d = j.get<double>();
    if (ledger.secret_numbers.count(d)) findings.push_back("exact truth number at " + path);
  }
}

// Scans a Hunter observation. The runner's own state is legitimately visible in the
// Runner's "own" block, so numbers are exempt there (but strings/keys are not).
std::vector<std::string> audit(const df::json& obs, const TruthLedger& ledger, df::Role role) {
  std::vector<std::string> findings;
  if (role == df::Role::Runner && obs.contains("own")) {
    df::json copy = obs;
    copy.erase("own");
    scan(copy, ledger, findings, "");
    // strings/keys inside "own" still checked
    TruthLedger strings_only;
    strings_only.secret_strings = ledger.secret_strings;
    scan(obs.at("own"), strings_only, findings, "/own");
  } else {
    scan(obs, ledger, findings, "");
  }
  return findings;
}

df::json random_scenario(hr::Rng& rng, std::uint64_t seed) {
  df::json s = dft::load_example();
  s["seed"] = std::to_string(seed);
  s["sensors"]["ais"]["sigma_m"] = 5.0 + rng.uniform01() * 200.0 + 0.001;
  s["sensors"]["ais"]["report_prob"] = 0.3 + rng.uniform01() * 0.7;
  const std::size_t n = 3 + rng.below(10);
  df::json vessels = df::json::array();
  const std::size_t runner = rng.below(n);
  const char* modes[] = {"on", "dark", "spoof"};
  for (std::size_t i = 0; i < n; ++i) {
    df::json v;
    v["id"] = "truth-" + std::to_string(seed) + "-" + std::to_string(i);
    v["name"] = "FICTIONAL SHIP " + std::to_string(i);
    v["class"] = "bulk";
    v["mmsi"] = std::to_string(999000000 + static_cast<long long>(seed % 1000) * 1000 + static_cast<long long>(i));
    v["x_m"] = (rng.uniform01() - 0.5) * 100000.0 + 0.123456789;
    v["y_m"] = (rng.uniform01() - 0.5) * 100000.0 + 0.987654321;
    v["max_speed_mps"] = 9.5;
    v["speed_mps"] = rng.uniform01() * 9.0 + 0.0001;
    v["course_deg"] = rng.uniform01() * 359.0 + 0.0001;
    v["is_runner"] = (i == runner);
    v["ais_mode"] = modes[rng.below(3)];
    v["secret_note"] = "CANARY-" + std::to_string(rng.next_u64());
    if (v["ais_mode"] == "spoof") v["ais_mode"] = "on";  // spoof offsets are set via actions
    vessels.push_back(std::move(v));
  }
  s["entities"]["vessels"] = std::move(vessels);
  return s;
}

df::json random_action_doc(hr::Rng& rng, std::uint64_t tick) {
  const char* roles[] = {"hunter", "runner", "admiral"};
  const char* types[] = {"noop", "set_ais_mode", "set_course_speed", "task_sensor", "teleport"};
  const char* modes[] = {"on", "dark", "spoof", "bogus"};
  df::json a;
  a["schema_version"] = "1.0.0";
  a["role"] = roles[rng.below(3)];
  a["tick"] = rng.below(5) == 0 ? tick + 1 : tick;
  a["action_type"] = types[rng.below(5)];
  df::json p = df::json::object();
  switch (rng.below(4)) {
    case 0: p["mode"] = modes[rng.below(4)]; p["offset_x_m"] = (rng.uniform01() - 0.5) * 1e5; p["offset_y_m"] = (rng.uniform01() - 0.5) * 1e5; break;
    case 1: p["course_deg"] = rng.uniform01() * 400.0; p["speed_mps"] = rng.uniform01() * 12.0; break;
    case 2: p["sensor"] = rng.below(2) ? "eo" : "sar"; p["x_m"] = rng.uniform01() * 1000; p["y_m"] = rng.uniform01() * 1000; break;
    default: break;
  }
  a["params"] = p;
  return a;
}

}  // namespace

TEST_CASE("Leak fuzz: no truth-only data reaches any role's observation (2000 seeds)") {
  std::size_t observations = 0, actions_ok = 0, actions_rejected = 0;
  for (std::uint64_t seed = 1; seed <= 2000; ++seed) {
    hr::Rng rng(seed, 100);
    df::World w;
    const auto st = df::World::from_scenario(random_scenario(rng, seed), w);
    INFO(st.message);
    REQUIRE(st.ok());
    TruthLedger ledger;
    ledger.record(w);
    for (int t = 0; t < 25; ++t) {
      for (int k = 0; k < 3; ++k) {
        const std::string before = w.canonical_state();
        const auto s = w.submit_action_json(random_action_doc(rng, w.tick()));
        if (s.ok()) {
          ++actions_ok;
        } else {
          ++actions_rejected;
          // A rejected action must leave simulation state untouched.
          REQUIRE(w.canonical_state() == before);
        }
      }
      w.step();
      ledger.record(w);
      for (df::Role role : {df::Role::Hunter, df::Role::Runner}) {
        const auto findings = audit(w.observe(role), ledger, role);
        ++observations;
        if (!findings.empty()) FAIL("leak at seed " << seed << " tick " << t << ": " << findings.front());
      }
    }
  }
  CHECK(observations == 2000u * 25u * 2u);
  CHECK(actions_ok > 1000);
  CHECK(actions_rejected > 1000);
}

TEST_CASE("Mutation test: the harness detects a deliberately leaky observer") {
  std::size_t detected = 0, total = 0;
  for (std::uint64_t seed = 1; seed <= 200; ++seed) {
    hr::Rng rng(seed, 100);
    df::World w;
    REQUIRE(df::World::from_scenario(random_scenario(rng, seed), w).ok());
    TruthLedger ledger;
    for (int t = 0; t < 5; ++t) {
      w.step();
      ledger.record(w);
    }
    const auto& v = w.truth_vessels();
    const auto it = std::find_if(v.begin(), v.end(), [](const df::Vessel& x) { return x.is_runner; });
    // Three distinct leak classes, each injected into a Hunter observation.
    for (int kind = 0; kind < 3; ++kind) {
      df::json obs = w.observe(df::Role::Hunter);
      if (kind == 0) obs["contacts"].push_back({{"contact_id", "c-deadbeef"}, {"x_m", it->x_m}});        // exact truth number
      if (kind == 1) obs["debug"] = {{"note", it->secret_note}};                                         // secret string
      if (kind == 2) obs["truth_state"] = df::json::object();                                            // forbidden key
      ++total;
      if (!audit(obs, ledger, df::Role::Hunter).empty()) ++detected;
    }
  }
  CHECK(total == 600);
  CHECK(detected == total);
}

TEST_CASE("Runner sees its own state but the Hunter never gets an 'own' block") {
  auto w = dft::make_world(dft::load_example());
  CHECK(w.observe(df::Role::Runner).contains("own"));
  CHECK_FALSE(w.observe(df::Role::Hunter).contains("own"));
}

TEST_CASE("A runner that goes dark disappears from the Hunter picture but not from its own view") {
  auto j = dft::load_example();
  j["sensors"]["ais"]["report_prob"] = 1.0;
  auto w = dft::make_world(j);
  REQUIRE(w.submit_action(dft::action(df::Role::Runner, 0, "set_ais_mode", {{"mode", "dark"}})).ok());
  for (int i = 0; i < 40; ++i) w.step();  // beyond max_age_ticks (30)
  for (const auto& c : w.observe(df::Role::Hunter)["contacts"]) CHECK(c["claimed_mmsi"] != "999000001");
  CHECK(w.observe(df::Role::Runner)["own"]["ais_mode"] == "dark");
}
