// SPDX-License-Identifier: MIT
#include <stdexcept>

#include "plugin_loader.hpp"
#include "rpc.hpp"

namespace bambu_host {

namespace {

// One process-global PluginLoader.  The host binary is single-threaded for
// RPC dispatch (events come in asynchronously via the callback queue), so
// no additional locking is required here.
PluginLoader& loader() {
  static PluginLoader g;
  return g;
}

// echo — returns params unchanged.  Used as a liveness / round-trip check.
json method_echo(const json& params) {
  return params;
}

// init_plugin — loads the .so and runs the 7-step bootstrap.
// Expected env vars (set by the Python PluginHost before spawning this binary):
//   PJARCZAK_BAMBU_NETWORK_SO — path to libbambu_networking.so
//   PJARCZAK_BAMBU_PLUGIN_DIR — parent dir for state/ subdir
//   BAMBU_CLOUD_REGION        — country code, e.g. "US"
//
// Returns {"bootstrap_rc": N} where N == 0 means full success.
json method_init_plugin(const json& /*params*/) {
  loader().load_from_env();
  int rc = loader().bootstrap();
  return {{"bootstrap_rc", rc}};
}

// change_user — passes a JSON-serialised login payload to the plugin.
// Params: {"canonical_login": "<json string>"}
// Returns {"rc": N} where N == 0 means login accepted.
json method_change_user(const json& params) {
  std::string payload = params.at("canonical_login").get<std::string>();
  int rc = loader().change_user(payload);
  return {{"rc", rc}};
}

// get_my_token — exchange a Bambu ticket for an access-token bundle.
// Params: {"ticket": "<ticket string from OAuth redirect>"}
// Returns the token JSON returned by the plugin (accessToken/access_token,
// refresh_token, expires_in, refresh_expires_in, http_code).
// Throws (→ RPC error response) if the plugin call fails.
json method_get_my_token(const json& params) {
  std::string ticket = params.at("ticket").get<std::string>();
  return loader().get_my_token(ticket);
}

}  // namespace

json dispatch_method(const std::string& method, const json& params) {
  if (method == "echo")           return method_echo(params);
  if (method == "init_plugin")    return method_init_plugin(params);
  if (method == "change_user")    return method_change_user(params);
  if (method == "get_my_token")   return method_get_my_token(params);
  throw std::runtime_error("unknown method: " + method);
}

}  // namespace bambu_host
