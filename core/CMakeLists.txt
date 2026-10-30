add_library(df_core
  src/world.cpp
  src/observe.cpp)
add_library(df::core ALIAS df_core)
target_include_directories(df_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(df_core PUBLIC hr::core nlohmann_json::nlohmann_json)
set_target_properties(df_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

if(DF_BUILD_TESTS)
  FetchContent_Declare(doctest
    GIT_REPOSITORY https://github.com/doctest/doctest.git
    GIT_TAG v2.4.11)
  FetchContent_MakeAvailable(doctest)
  add_executable(df_core_tests
    tests/test_main.cpp
    tests/test_world.cpp
    tests/test_actions.cpp
    tests/test_leak.cpp)
  target_link_libraries(df_core_tests PRIVATE df::core doctest::doctest)
  target_compile_definitions(df_core_tests PRIVATE
    DF_EXAMPLE_SCENARIO="${CMAKE_SOURCE_DIR}/schemas/examples/scenario_basic.json")
  add_test(NAME df_core_tests COMMAND df_core_tests)
endif()
