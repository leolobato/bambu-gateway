// SPDX-License-Identifier: MIT
// Single-header helper for newline-delimited JSON RPC over stdin/stdout.
#pragma once

#include <cstdio>
#include <optional>
#include <string>

#include "third_party/nlohmann/json.hpp"

namespace bambu_host {

using json = nlohmann::json;

// Read one '\n'-terminated line from stdin. Returns std::nullopt on EOF.
inline std::optional<std::string> read_line(FILE* in) {
  std::string line;
  for (;;) {
    int c = std::fgetc(in);
    if (c == EOF) {
      return line.empty() ? std::nullopt : std::optional<std::string>{line};
    }
    if (c == '\n') return line;
    line.push_back(static_cast<char>(c));
  }
}

// Write one JSON object as a single line + flush.
inline void write_frame(FILE* out, const json& payload) {
  std::string s = payload.dump();
  std::fwrite(s.data(), 1, s.size(), out);
  std::fputc('\n', out);
  std::fflush(out);
}

// Build an error response with the matching request id.
inline json error_response(long long id, const std::string& message,
                           int code = -1) {
  return {{"id", id}, {"error", {{"code", code}, {"message", message}}}};
}

inline json ok_response(long long id, json result) {
  return {{"id", id}, {"result", std::move(result)}};
}

}  // namespace bambu_host
