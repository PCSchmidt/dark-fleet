#pragma once
#include <fstream>
#include <sstream>
#include <string>

#include "df/world.hpp"

namespace dft {

inline df::json load_example() {
  std::ifstream f(DF_EXAMPLE_SCENARIO);
  std::stringstream ss;
  ss << f.rdbuf();
  return df::json::parse(ss.str());
}

inline df::World make_world(const df::json& scenario) {
  df::World w;
  const df::Status s = df::World::from_scenario(scenario, w);
  if (!s.ok()) throw std::runtime_error("scenario rejected: " + s.message);
  return w;
}

inline df::Action action(df::Role role, std::uint64_t tick, const std::string& type, df::json params) {
  df::Action a;
  a.role = role;
  a.tick = tick;
  a.type = type;
  a.params = std::move(params);
  return a;
}

}  // namespace dft
