// SPDX-License-Identifier: MIT
#include "rpc.hpp"

namespace bambu_host {

// Forward declaration; main.cpp calls this.
json dispatch_method(const std::string& method, const json& params);

namespace {

// echo just returns its params back. Useful as a liveness check.
json method_echo(const json& params) {
  return params;
}

}  // namespace

json dispatch_method(const std::string& method, const json& params) {
  if (method == "echo") return method_echo(params);
  throw std::runtime_error("unknown method: " + method);
}

}  // namespace bambu_host
