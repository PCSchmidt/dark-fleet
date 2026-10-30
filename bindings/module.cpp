// Python bindings for dark-fleet-core. The surface is deliberately JSON-in/JSON-out so
// the Python side (tests, agents, ML environments) can only see role-filtered documents.
//
// The submodule `darkfleet_core.evaluation` exposes TRUTH for scoring and evaluation
// harnesses only. Agent, bot and tracker code must never import it; python/tests
// enforces this with a source scan.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <stdexcept>

#include "df/replay.hpp"
#include "df/traffic.hpp"
#include "df/world.hpp"

namespace py = pybind11;

namespace {

df::Role parse_role(const std::string& s) {
  df::Role r;
  if (!df::role_from_string(s, r)) throw std::invalid_argument("role must be 'hunter' or 'runner'");
  return r;
}

df::json parse_json(const std::string& text, const char* what) {
  try {
    return df::json::parse(text);
  } catch (const df::json::exception& e) {
    throw std::invalid_argument(std::string("E_SCHEMA: invalid JSON in ") + what + ": " + e.what());
  }
}

class PyWorld {
 public:
  explicit PyWorld(const std::string& scenario_json) {
    const df::json doc = parse_json(scenario_json, "scenario");
    const df::Status s = df::World::from_scenario(doc, world_);
    if (!s.ok()) throw std::invalid_argument(std::string(hr::to_string(s.code)) + ": " + s.message);
  }

  std::uint64_t tick() const { return world_.tick(); }
  std::uint64_t duration_ticks() const { return world_.duration_ticks(); }
  std::string state() const { return df::to_string(world_.state()); }
  bool ended() const { return world_.state() != df::EpisodeState::Running; }

  std::uint64_t step(std::uint64_t n) {
    std::uint64_t done = 0;
    while (done < n && world_.step()) ++done;
    return done;
  }

  // Returns the typed error name ("OK" or e.g. "E_ROLE_FORBIDDEN").
  std::string submit_action(const std::string& action_json) {
    df::json doc;
    try {
      doc = df::json::parse(action_json);
    } catch (const df::json::exception&) {
      return std::string(hr::to_string(hr::ErrorCode::Schema));
    }
    return std::string(hr::to_string(world_.submit_action_json(doc).code));
  }

  std::string observe(const std::string& role) const { return world_.observe(parse_role(role)).dump(); }
  std::string state_hash() const { return world_.state_hash(); }
  std::string replay() const { return world_.replay().dump(); }
  std::string outcome() const {
    const auto& o = world_.outcome();
    return df::json{{"outcome", o.name}, {"hunter_score", o.hunter_score}, {"runner_score", o.runner_score}}.dump();
  }

  // Evaluation-only: the runner's true position and velocity.
  std::string eval_truth_runner() const {
    for (const auto& v : world_.truth_vessels())
      if (v.is_runner) return df::json{{"x_m", v.x_m}, {"y_m", v.y_m}, {"speed_mps", v.speed_mps}, {"course_deg", v.course_deg}}.dump();
    return "{}";
  }

 private:
  df::World world_;
};

}  // namespace

PYBIND11_MODULE(darkfleet_core, m) {
  m.doc() = "Dark Fleet deterministic core (role-filtered JSON interface)";
  py::class_<PyWorld>(m, "World")
      .def(py::init<const std::string&>(), py::arg("scenario_json"))
      .def_property_readonly("tick", &PyWorld::tick)
      .def_property_readonly("duration_ticks", &PyWorld::duration_ticks)
      .def_property_readonly("state", &PyWorld::state)
      .def_property_readonly("ended", &PyWorld::ended)
      .def("step", &PyWorld::step, py::arg("n") = 1)
      .def("submit_action", &PyWorld::submit_action, py::arg("action_json"))
      .def("observe", &PyWorld::observe, py::arg("role"))
      .def("state_hash", &PyWorld::state_hash)
      .def("replay", &PyWorld::replay)
      .def("outcome", &PyWorld::outcome);

  m.def(
      "generate_scenario",
      [](std::uint64_t seed, int n_background, std::uint64_t duration_ticks, std::uint32_t sar_tasks) {
        df::GenParams p;
        p.seed = seed;
        p.n_background = n_background;
        p.duration_ticks = duration_ticks;
        p.sar_tasks = sar_tasks;
        return df::generate_scenario(p).dump();
      },
      py::arg("seed"), py::arg("n_background") = 20, py::arg("duration_ticks") = 144, py::arg("sar_tasks") = 6);

  // Returns {"code": "OK"|"E_...", "message": str, "first_bad_tick": int, "outcome": str}.
  m.def("verify_replay", [](const std::string& scenario_json, const std::string& replay_json) {
    const df::json scenario = parse_json(scenario_json, "scenario");
    const df::json replay = parse_json(replay_json, "replay");
    const df::VerifyResult r = df::verify_replay(scenario, replay);
    return df::json{{"code", std::string(hr::to_string(r.code))},
                    {"message", r.message},
                    {"first_bad_tick", r.first_bad_tick},
                    {"outcome", r.outcome.name}}
        .dump();
  });

  py::module_ ev = m.def_submodule("evaluation", "TRUTH access for evaluation harnesses only. Agents must not import this.");
  ev.def("runner_truth", [](const PyWorld& w) { return w.eval_truth_runner(); });
}
