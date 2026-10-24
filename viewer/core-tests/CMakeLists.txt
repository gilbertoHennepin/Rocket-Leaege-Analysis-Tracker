cmake_minimum_required(VERSION 3.16)
project(rlreplay_core_tests CXX)

# Unreal Engine 5 builds with C++20; test the core with the same standard.
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(test_replay_core test_replay_core.cpp)

if(MSVC)
  target_compile_options(test_replay_core PRIVATE /W4 /WX)
else()
  target_compile_options(test_replay_core PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()

enable_testing()
add_test(NAME replay_core COMMAND test_replay_core)
