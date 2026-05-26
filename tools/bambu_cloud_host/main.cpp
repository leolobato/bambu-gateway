// SPDX-License-Identifier: MIT
//
// bambu_cloud_host — JSONL RPC bridge to libbambu_networking.so.
//
// Reads one JSON object per line from stdin: {"id":N, "method":"...", "params":{...}}.
// Replies with {"id":N, "result":...} or {"id":N, "error":{...}} on stdout.
// Logs go to stderr (the host process never writes anything but framed
// responses to stdout, so the parent's RPC parser stays clean).

#include <cstdio>
#include <iostream>
#include <string>

#include "rpc.hpp"

namespace bambu_host {
json dispatch_method(const std::string& method, const json& params);
}

int main() {
  using namespace bambu_host;
  // Unbuffer stdout so the parent sees responses immediately.
  std::setvbuf(stdout, nullptr, _IONBF, 0);

  std::fprintf(stderr, "bambu_cloud_host: started\n");

  while (auto line = read_line(stdin)) {
    long long id = -1;
    try {
      json req = json::parse(*line);
      id = req.value("id", -1LL);
      std::string method = req.at("method").get<std::string>();
      json params = req.value("params", json::object());
      json result = dispatch_method(method, params);
      write_frame(stdout, ok_response(id, std::move(result)));
    } catch (const std::exception& exc) {
      write_frame(stdout, error_response(id, exc.what()));
    }
  }
  std::fprintf(stderr, "bambu_cloud_host: stdin closed, exiting\n");
  return 0;
}
