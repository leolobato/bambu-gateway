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

#include <functional>
#include <string>
#include <vector>

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

  // Registers a trampoline with the plugin that pushes every incoming cloud
  // MQTT message into the process-global EventQueue.
  //
  // The OnMessageFn callback is invoked from the plugin's internal MQTT thread.
  // Our trampoline only calls EventQueue::push (mutex-guarded) so it is
  // reentrant and safe for concurrent invocations.
  //
  // Discovery note: OnMessageFn has 2 params (dev_id, msg) — no chan param.
  // Source: bambu_networking.hpp:120
  //
  // Throws std::runtime_error if bootstrap() was not called first or if the
  // plugin returns a non-zero error code.
  void register_message_callback();

  // Calls bambu_network_connect_server(agent).
  // Initiates the cloud MQTT broker handshake asynchronously.
  // Returns 0 = initiated; negative = error.
  int connect_server();

  // Calls bambu_network_start_subscribe(agent, module).
  // module — e.g. "printer" or "studio".
  // Returns 0 = ok; negative = error.
  int start_subscribe(const std::string& module);

  // Calls bambu_network_add_subscribe(agent, dev_ids).
  // Adds a list of device serials to the active MQTT subscription.
  // Returns 0 = ok; negative = error.
  int add_subscribe(const std::vector<std::string>& dev_ids);

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

  // OnMessageFn — std::function callback type matching bambu_networking.hpp:120.
  // EXACTLY 2 parameters: dev_id and msg (NO chan).
  using on_message_fn = std::function<void(std::string dev_id, std::string msg)>;

  // int bambu_network_set_on_message_fn(void *agent, OnMessageFn fn)
  // Registers a callback invoked from the plugin's internal MQTT thread.
  // Source: BBLNetworkPlugin.hpp:39, bambu_networking.hpp:120
  using fn_set_on_message_fn = int(*)(void*, on_message_fn);

  // int bambu_network_connect_server(void *agent)
  // Source: BBLNetworkPlugin.hpp:44
  using fn_connect_server    = int(*)(void*);

  // int bambu_network_start_subscribe(void *agent, std::string module)
  // Source: BBLNetworkPlugin.hpp:47
  using fn_start_subscribe   = int(*)(void*, std::string);

  // int bambu_network_add_subscribe(void *agent, std::vector<std::string> dev_list)
  // Source: BBLNetworkPlugin.hpp:49
  using fn_add_subscribe     = int(*)(void*, std::vector<std::string>);

  fn_create_agent       p_create_agent_       = nullptr;
  fn_init_log           p_init_log_           = nullptr;
  fn_set_config_dir     p_set_config_dir_     = nullptr;
  fn_set_cert_file      p_set_cert_file_      = nullptr;
  fn_set_country_code   p_set_country_code_   = nullptr;
  fn_start              p_start_              = nullptr;
  fn_change_user        p_change_user_        = nullptr;
  fn_get_my_token       p_get_my_token_       = nullptr;
  fn_set_on_message_fn  p_set_on_message_fn_  = nullptr;
  fn_connect_server     p_connect_server_     = nullptr;
  fn_start_subscribe    p_start_subscribe_    = nullptr;
  fn_add_subscribe      p_add_subscribe_      = nullptr;
};

}  // namespace bambu_host
