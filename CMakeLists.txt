cmake_minimum_required(VERSION 3.21)
project(dark_fleet VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(DF_BUILD_TESTS "Build dark-fleet-core tests" ON)
option(DF_BUILD_PYTHON "Build the Python bindings" ON)

# Newer CMake rejects third-party projects declaring minimum versions below 3.5.
set(CMAKE_POLICY_VERSION_MINIMUM 3.5)

include(FetchContent)
if(DF_BUILD_TESTS)
  enable_testing()
endif()

# Platform core (git submodule). Its own tests are not built when consumed.
add_subdirectory(extern/hyperreal-platform)

FetchContent_Declare(nlohmann_json
  GIT_REPOSITORY https://github.com/nlohmann/json.git
  GIT_TAG v3.11.3
  GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(nlohmann_json)

add_subdirectory(core)

if(DF_BUILD_PYTHON)
  add_subdirectory(bindings)
endif()
