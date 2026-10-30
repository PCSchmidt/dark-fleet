#include "df/replay.hpp"

#include "hr/sha256.hpp"

namespace df {

namespace {

VerifyResult fail(hr::ErrorCode c, std::string msg, std::uint64_t tick = 0) {
  VerifyResult r;
  r.code = c;
  r.message = std::move(msg);
  r.first_bad_tick = tick;
  return r;
}

}  // namespace

VerifyResult verify_replay(const json& scenario, const json& replay) {
  try {
    if (!replay.is_object()) return fail(hr::ErrorCode::Schema, "replay must be an object");
    const std::string version = replay.value("schema_version", "");
    if (version.rfind("1.", 0) != 0) return fail(hr::ErrorCode::Version, "unsupported replay schema_version");
    if (replay.value("title_id", "") != "dark-fleet") return fail(hr::ErrorCode::Schema, "title_id");
    if (replay.value("rules_version", "") != kRulesVersion)
      return fail(hr::ErrorCode::Version, "rules_version mismatch: replay was recorded under different rules");
    for (const char* k : {"inputs", "chain", "n_ticks", "final_state_hash", "result", "content_hash", "scenario_hash"})
      if (!replay.contains(k)) return fail(hr::ErrorCode::Schema, std::string("missing field: ") + k);

    // 1. Content hash (tamper evidence for the whole document).
    json body = replay;
    const std::string recorded_content_hash = body.value("content_hash", "");
    body.erase("content_hash");
    if (hr::Sha256::hash_hex(body.dump()) != recorded_content_hash)
      return fail(hr::ErrorCode::Hash, "content hash mismatch: replay document was modified");

    // 2. Scenario binding.
    if (hr::Sha256::hash_hex(scenario.dump()) != replay.at("scenario_hash").get<std::string>())
      return fail(hr::ErrorCode::Hash, "scenario hash mismatch: replay does not belong to this scenario");

    // 3. Re-simulate.
    World w;
    const Status s = World::from_scenario(scenario, w);
    if (!s.ok()) return fail(s.code, "scenario rejected: " + s.message);

    const std::uint64_t n_ticks = replay.at("n_ticks").get<std::uint64_t>();
    const json& inputs = replay.at("inputs");
    const json& chain = replay.at("chain");
    std::size_t next_input = 0;
    std::size_t link = 0;

    auto check_link = [&](std::uint64_t tick) -> bool {
      if (link >= chain.size()) return false;
      const json& l = chain[link++];
      const auto& mine = w.chain().links().back();
      return l.value("tick", ~std::uint64_t{0}) == tick && mine.tick == tick && l.value("hex", "") == mine.hex;
    };

    // Link 0 is the initial state.
    if (!check_link(0)) return fail(hr::ErrorCode::Desync, "initial state hash mismatch", 0);

    for (std::uint64_t t = 0; t <= n_ticks; ++t) {
      while (next_input < inputs.size() && inputs[next_input].at("at_tick").get<std::uint64_t>() == t) {
        (void)w.submit_action_json(inputs[next_input].at("doc"));
        ++next_input;
      }
      if (t == n_ticks) break;
      if (!w.step()) return fail(hr::ErrorCode::Desync, "episode ended earlier than recorded", t);
      if (!check_link(t + 1)) return fail(hr::ErrorCode::Desync, "state hash mismatch", t + 1);
    }
    if (next_input != inputs.size()) return fail(hr::ErrorCode::Desync, "inputs recorded beyond the final tick", n_ticks);
    if (link != chain.size()) return fail(hr::ErrorCode::Desync, "recorded chain longer than re-simulation", n_ticks);

    // 4. End-of-run state and recorded result.
    if (w.final_state_hash() != replay.at("final_state_hash").get<std::string>())
      return fail(hr::ErrorCode::Desync, "final state hash mismatch", n_ticks);
    const json& res = replay.at("result");
    if (res.value("state", "") != to_string(w.state()) || res.value("outcome", "") != w.outcome().name ||
        res.value("hunter_score", 1e300) != w.outcome().hunter_score ||
        res.value("runner_score", 1e300) != w.outcome().runner_score)
      return fail(hr::ErrorCode::Desync, "recorded result differs from re-simulated result", n_ticks);

    VerifyResult ok;
    ok.outcome = w.outcome();
    return ok;
  } catch (const json::exception& e) {
    return fail(hr::ErrorCode::Schema, std::string("malformed replay: ") + e.what());
  }
}

}  // namespace df
