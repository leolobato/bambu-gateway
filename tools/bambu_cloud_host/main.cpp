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
#include <unistd.h>  // dup, dup2, STDOUT_FILENO, STDERR_FILENO

#include "rpc.hpp"

namespace bambu_host {
json dispatch_method(const std::string& method, const json& params);
}

int main() {
  using namespace bambu_host;

  // The dlopen'd Bambu plugin writes diagnostic text straight to FD 1
  // (stdout) — which is our JSON-RPC channel to the parent — corrupting frames
  // (the "malformed frame from host" errors, which can desync poll_events and
  // drop cloud device reports). Isolate the channel: dup the real stdout to a
  // PRIVATE fd used only for RPC responses, then point FD 1 at stderr so any
  // library writes to stdout become harmless log noise on the parent's stderr
  // drain instead of corrupting RPC.
  int rpc_fd = dup(STDOUT_FILENO);
  dup2(STDERR_FILENO, STDOUT_FILENO);
  FILE* rpc_out = fdopen(rpc_fd, "w");
  std::setvbuf(rpc_out, nullptr, _IONBF, 0);  // unbuffered: parent sees replies now

  std::fprintf(stderr, "bambu_cloud_host: started\n");

  while (auto line = read_line(stdin)) {
    long long id = -1;
    try {
      json req = json::parse(*line);
      id = req.value("id", -1LL);
      std::string method = req.at("method").get<std::string>();
      json params = req.value("params", json::object());
      json result = dispatch_method(method, params);
      write_frame(rpc_out, ok_response(id, std::move(result)));
    } catch (const std::exception& exc) {
      write_frame(rpc_out, error_response(id, exc.what()));
    }
  }
  std::fprintf(stderr, "bambu_cloud_host: stdin closed, exiting\n");
  return 0;
}
