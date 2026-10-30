// Python bindings for dark-fleet-core. The surface is deliberately JSON-in/JSON-out so
// the Python side (tests, agents, ML environments) can only see role-filtered documents.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <stdexcept>

#include "df/world.hpp"

namespace py = pybind11;

namespace {

df::Role parse_role(const std::string& s) {
  df::Role r;
  if (!df::role_from_string(s, r)) throw std::invalid_argument("role must be 'hunter' or 'runner'");
  return r;
}

class PyWorld {
 public:
  explicit PyWorld(const std::string& scenario_json) {
    df::json doc;
    try {
      doc = df::json::parse(scenario_json);
    } catch (const df::json::exception& e) {
      throw std::invalid_argument(std::string("E_SCHEMA: invalid JSON: ") + e.what());
    }
    const df::Status s = df::World::from_scenario(doc, world_);
    if (!s.ok()) throw std::invalid_argument(std::string(hr::to_string(s.code)) + ": " + s.message);
  }

  std::uint64_t tick() const { return world_.tick(); }
  std::uint64_t duration_ticks() const { return world_.duration_ticks(); }

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
      .def("step", &PyWorld::step, py::arg("n") = 1)
      .def("submit_action", &PyWorld::submit_action, py::arg("action_json"))
      .def("observe", &PyWorld::observe, py::arg("role"))
      .def("state_hash", &PyWorld::state_hash);
}
