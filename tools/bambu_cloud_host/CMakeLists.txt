cmake_minimum_required(VERSION 3.20)
project(bambu_cloud_host CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Static, position-independent, hardened — we ship one binary inside Docker.
add_compile_options(
  -Wall -Wextra -Wpedantic -Wshadow
  -fno-strict-aliasing
  -O2 -g
)

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/third_party)

add_executable(bambu_cloud_host
  main.cpp
  plugin_loader.cpp
  methods.cpp
)

# libdl for dlopen, pthread for the event queue lock
target_link_libraries(bambu_cloud_host PRIVATE dl pthread)

install(TARGETS bambu_cloud_host RUNTIME DESTINATION bin)
