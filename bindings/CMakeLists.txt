FetchContent_Declare(pybind11
  GIT_REPOSITORY https://github.com/pybind/pybind11.git
  GIT_TAG v3.0.1
  GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(pybind11)

pybind11_add_module(darkfleet_core module.cpp)
target_link_libraries(darkfleet_core PRIVATE df::core)

# Place the module in a predictable directory for the Python tests, for single- and multi-config generators.
set(_df_py_out ${CMAKE_BINARY_DIR}/python)
set_target_properties(darkfleet_core PROPERTIES
  LIBRARY_OUTPUT_DIRECTORY ${_df_py_out}
  LIBRARY_OUTPUT_DIRECTORY_DEBUG ${_df_py_out}
  LIBRARY_OUTPUT_DIRECTORY_RELEASE ${_df_py_out}
  RUNTIME_OUTPUT_DIRECTORY ${_df_py_out}
  RUNTIME_OUTPUT_DIRECTORY_DEBUG ${_df_py_out}
  RUNTIME_OUTPUT_DIRECTORY_RELEASE ${_df_py_out})
