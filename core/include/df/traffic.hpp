// Deterministic scenario + background-traffic generator (FR-004).
//
// Region: a fictional 500 km x 500 km local frame (meters, ENU) loosely inspired by
// strait-approach shipping lanes. Vessel names, owners and MMSIs are fictional.
// Same GenParams => byte-identical scenario JSON on the same build.
#pragma once
#include <cstdint>

#include "df/world.hpp"

namespace df {

struct GenParams {
  std::uint64_t seed = 1;
  int n_background = 20;
  std::uint64_t duration_ticks = 144;
  int tick_s = 300;
  double ais_sigma_m = 30.0;
  double ais_report_prob = 0.95;
  double outage_probability = 0.3;   // chance a background vessel has an innocent AIS fault window
  std::uint32_t sar_tasks = 6;
  bool brief_profile = false;        // true: Hunter is briefed with class + last-known area, not the MMSI
};

json generate_scenario(const GenParams& p);

}  // namespace df
