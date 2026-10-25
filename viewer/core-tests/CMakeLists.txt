cmake_minimum_required(VERSION 3.16)
project(rlreplay_core_tests CXX)

# Unreal Engine 5 builds with C++20; test the core with the same standard.
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

enable_testing()

foreach(name replay_core telemetry_core)
  add_executable(test_${name} test_${name}.cpp)
  if(MSVC)
    target_compile_options(test_${name} PRIVATE /W4 /WX)
  else()
    target_compile_options(test_${name} PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endif()
  add_test(NAME ${name} COMMAND test_${name})
endforeach()
