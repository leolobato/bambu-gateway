// SPDX-License-Identifier: MIT
//
// PluginLoader — dlopen + bootstrap wrapper for libbambu_networking.so.
//
// Function-pointer typedefs are based on the exact C++ ABI signatures from
// BBLNetworkPlugin.hpp.  All "string" parameters are std::string by value —
// passing const char* instead WILL segfault because the calling convention
// for std::string by-value places a 32-byte struct (SSO) on the stack, not
// a raw pointer.  See discovery notes:
//   docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md
#pragma once

#include <string>

#include "third_party/nlohmann/json.hpp"

namespace bambu_host {

// Loads libbambu_networking.so and resolves the function pointers we need.
// Throws std::runtime_error on dlopen / dlsym failure.
class PluginLoader {
 public:
  // Reads PJARCZAK_BAMBU_NETWORK_SO from the environment and dlopen-s it.
  // Throws std::runtime_error if the env var is missing or dlopen fails.
  void load_from_env();

  // Runs the 7-step bootstrap sequence (create_agent → set_config_dir →
  // init_log → set_cert_file → set_country_code → start).
  // Returns 0 on success, a non-zero plugin error code on failure.
  // Throws std::runtime_error if load_from_env() was not called first.
  int bootstrap();

  // Calls bambu_network_change_user(agent, user_info_json).
  // Returns the plugin's return code (0 = success, negative = error).
  // Returns -1 immediately if bootstrap() has not been called.
  int change_user(const std::string& canonical_login_json);

  // Calls bambu_network_get_my_token(agent, ticket, &http_code, &http_body).
  // Exchanges a Bambu ticket (from the paste-fallback OAuth redirect) for an
  // access-token bundle.  The plugin makes a synchronous HTTPS call and fills
  // the out-params; we parse the JSON body and return the token fields.
  //
  // Returns a JSON object with {access_token, refresh_token, expires_in,
  // refresh_expires_in, http_code}.  Throws std::runtime_error on plugin
  // error (non-zero rc) or if the response body cannot be parsed.
  nlohmann::json get_my_token(const std::string& ticket);

 private:
  void* dl_handle_  = nullptr;
  void* agent_      = nullptr;   // opaque handle returned by create_agent

  // -----------------------------------------------------------------------
  // Function-pointer typedefs — exact C++ ABI from BBLNetworkPlugin.hpp.
  // All std::string args are passed BY VALUE (the plugin was compiled as C++
  // and the ABI places the SSO struct on the stack, NOT a pointer).
  // -----------------------------------------------------------------------

  // void* bambu_network_create_agent(std::string log_dir)
  using fn_create_agent      = void*(*)(std::string);

  // int bambu_network_init_log(void *agent)
  using fn_init_log          = int(*)(void*);

  // int bambu_network_set_config_dir(void *agent, std::string config_dir)
  using fn_set_config_dir    = int(*)(void*, std::string);

  // int bambu_network_set_cert_file(void *agent, std::string folder, std::string filename)
  using fn_set_cert_file     = int(*)(void*, std::string, std::string);

  // int bambu_network_set_country_code(void *agent, std::string country_code)
  using fn_set_country_code  = int(*)(void*, std::string);

  // int bambu_network_start(void *agent)
  using fn_start             = int(*)(void*);

  // int bambu_network_change_user(void *agent, std::string user_info)
  using fn_change_user       = int(*)(void*, std::string);

  // int bambu_network_get_my_token(void *agent, std::string ticket,
  //                                unsigned int *http_code, std::string *http_body)
  // Synchronous — makes an HTTPS call to Bambu's token endpoint, fills
  // *http_code with the HTTP status and *http_body with the JSON response.
  // Returns 0 on success, negative on error (same codes as change_user).
  // Source: BBLNetworkPlugin.hpp:109, BBLCloudServiceAgent.cpp:620-629.
  using fn_get_my_token      = int(*)(void*, std::string, unsigned int*, std::string*);

  fn_create_agent     p_create_agent_    = nullptr;
  fn_init_log         p_init_log_        = nullptr;
  fn_set_config_dir   p_set_config_dir_  = nullptr;
  fn_set_cert_file    p_set_cert_file_   = nullptr;
  fn_set_country_code p_set_country_code_= nullptr;
  fn_start            p_start_           = nullptr;
  fn_change_user      p_change_user_     = nullptr;
  fn_get_my_token     p_get_my_token_    = nullptr;
};

}  // namespace bambu_host
