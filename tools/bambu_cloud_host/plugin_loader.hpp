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
//
// start_print ABI note (Phase A discovery):
//   bambu_networking.hpp:124 — OnUpdateStatusFn is std::function<void(int,int,string)>
//   There is NO bambu_network_set_on_update_status_fn symbol.  The callback is
//   passed as a per-call argument to bambu_network_start_print (and siblings).
//   PrintParams is a struct passed BY VALUE — no JSON shortcut exists.
#pragma once

#include <atomic>
#include <functional>
#include <map>
#include <string>
#include <vector>

#include "third_party/nlohmann/json.hpp"

namespace bambu_host {

// ---------------------------------------------------------------------------
// PrintParams — mirrors the plugin's struct byte-for-byte.
//
// WARNING: This struct MUST match the layout in the loaded plugin .so exactly.
// Any field added, removed, or reordered in a future Bambu plugin release will
// silently corrupt memory.  Pin the plugin .so version and audit on each upgrade.
//
// Source: bambu_networking.hpp:217-260 (plugin version 02.05.02.58)
// ---------------------------------------------------------------------------
struct PrintParams {
  // Identity
  std::string dev_id;
  std::string task_name;
  std::string project_name;
  std::string preset_name;

  // Files
  std::string filename;
  std::string config_filename;
  int         plate_index    = 0;
  std::string ftp_folder;
  std::string ftp_file;
  std::string ftp_file_md5;

  // AMS / Filament mapping
  std::string nozzle_mapping;
  std::string ams_mapping;
  std::string ams_mapping2;
  std::string ams_mapping_info;
  std::string nozzles_info;

  // Routing
  std::string connection_type;    // "lan" or "cloud"
  std::string comments;

  // Origin / Provenance
  int         origin_profile_id = 0;
  int         stl_design_id     = 0;
  std::string origin_model_id;
  std::string print_type;
  std::string dst_file;

  // LAN credentials (leave empty for pure-cloud path)
  std::string dev_name;
  std::string dev_ip;
  bool        use_ssl_for_ftp  = false;
  bool        use_ssl_for_mqtt = false;
  std::string username;
  std::string password;

  // Print-option flags
  bool        task_bed_leveling      = false;
  bool        task_flow_cali         = false;
  bool        task_vibration_cali    = false;
  bool        task_layer_inspect     = false;
  bool        task_record_timelapse  = false;
  bool        task_use_ams           = false;
  std::string task_bed_type;
  std::string extra_options;

  // Auto-calibration overrides (newer plugin versions)
  int         auto_bed_leveling        = 0;
  int         auto_flow_cali           = 0;
  int         auto_offset_cali         = 0;
  int         extruder_cali_manual_mode = -1;

  // Additional flags
  bool        task_ext_change_assist = false;
  bool        try_emmc_print         = false;
};

// Callback types matching bambu_networking.hpp:124-126
// OnUpdateStatusFn: per-call arg to start_print (no separate setter function).
using on_update_status_fn = std::function<void(int stage, int code, std::string msg)>;
using was_cancelled_fn    = std::function<bool()>;
using on_wait_fn          = std::function<bool(int status, std::string job_info)>;

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

  // Calls bambu_network_is_user_login(agent).
  // Returns false if bootstrap() has not been called.
  bool is_user_login();

  // Calls bambu_network_user_logout(agent, request).
  // request=true also revokes the session with Bambu's backend, matching
  // OrcaSlicer's logout flow (GUI_App::request_user_logout passes true).
  // Returns -1 immediately if bootstrap() has not been called.
  int user_logout(bool request);

  // Calls bambu_network_get_my_token(agent, ticket, &http_code, &http_body).
  // Exchanges a Bambu ticket (from the paste-fallback OAuth redirect) for an
  // access-token bundle.  The plugin makes a synchronous HTTPS call and fills
  // the out-params; we parse the JSON body and return the token fields.
  //
  // Returns a JSON object with {access_token, refresh_token, expires_in,
  // refresh_expires_in, http_code}.  Throws std::runtime_error on plugin
  // error (non-zero rc) or if the response body cannot be parsed.
  nlohmann::json get_my_token(const std::string& ticket);

  // Calls bambu_network_get_user_print_info(agent, &http_code, &http_body)
  // using the plugin's logged-in session. Returns the parsed JSON object
  // ({"devices":[...], "http_code":N}). Throws on non-zero rc / parse error.
  nlohmann::json get_user_print_info();

  // Calls bambu_network_set_user_selected_machine(agent, dev_id) — the cloud
  // equivalent of connect_printer. Opens the publish channel to the device so
  // send_message stops returning -2 (CONNECT_FAILED). Returns the plugin rc.
  int set_user_selected_machine(const std::string& dev_id);

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

  // Installs a trampoline pushing an {"kind":"OnPrinterConnected","dev_id":..}
  // event when the plugin reports a device's publish channel is ready.
  void register_printer_connected_callback();

  // Calls bambu_network_connect_server(agent).
  // Initiates the cloud MQTT broker handshake asynchronously.
  // Returns 0 = initiated; negative = error.
  int connect_server();

  // Calls bambu_network_is_server_connected(agent). connect_server() only
  // *initiates* the cloud MQTT handshake; subscribing/selecting before it
  // actually completes leaves the subscription dead. Poll this until true.
  // Returns false if the symbol is unavailable or the agent isn't up.
  bool is_server_connected();

  // Whether the is_server_connected probe symbol is available in this plugin.
  bool has_server_connected_probe() const {
    return p_is_server_connected_ != nullptr;
  }

  // Calls bambu_network_start_subscribe(agent, module).
  // module — e.g. "printer" or "studio".
  // Returns 0 = ok; negative = error.
  int start_subscribe(const std::string& module);

  // Calls bambu_network_add_subscribe(agent, dev_ids).
  // Adds a list of device serials to the active MQTT subscription.
  // Returns 0 = ok; negative = error.
  int add_subscribe(const std::vector<std::string>& dev_ids);

  // Dispatches bambu_network_start_print on a detached worker thread so the
  // RPC loop remains free to service bridge.poll_events concurrently.
  //
  // Returns immediately with 0 if the job was launched, or -98 if another job
  // is already in flight (only one concurrent job is supported).
  //
  // Progress is reported via OnUpdateStatus events pushed to the global
  // EventQueue.  On worker completion the thread is automatically joined via
  // detach; no further action is needed from the caller.
  //
  // Returns -1 immediately if bootstrap() has not been called.
  int start_print(PrintParams params);

  // Calls bambu_network_send_message(agent, dev_id, payload, qos, flag).
  // Relays a pre-serialised JSON command string to the printer via the cloud
  // MQTT relay.  The call is synchronous and non-blocking — the plugin enqueues
  // the message with its internal MQTT client and returns immediately.
  //
  // dev_id  — printer serial number
  // payload — JSON command string (same envelope as the LAN MQTT path)
  // qos     — MQTT QoS level (0 = at-most-once, 1 = at-least-once)
  // flag    — routing flag; 0 for normal cloud relay (OrcaSlicer always passes 0)
  //
  // Returns 0 on success, negative on error.
  // Throws std::runtime_error if bootstrap() was not called first.
  //
  // Source: BBLNetworkPlugin.hpp:52, BBLPrinterAgent.cpp:163-176
  int send_message(const std::string& dev_id,
                   const std::string& payload,
                   int qos  = 0,
                   int flag = 0);

  // -----------------------------------------------------------------------
  // LAN / local-mode publishing (the authenticated path for printers reached
  // over the local network). Newer Bambu firmware ignores writes published
  // straight onto the printer's local MQTT socket — only the plugin's
  // authenticated local connection is honoured. Mirrors OrcaSlicer's
  // MachineObject::connect() → local_publish_json() path.
  // -----------------------------------------------------------------------

  // Installs the OnLocalConnected trampoline, pushing an
  // {"kind":"OnLocalConnected","dev_id":..,"status":N,"msg":..} event when the
  // plugin reports a local connection state change. status 0 == ready.
  // No-op (logged) if the plugin lacks the symbol.
  void register_local_connect_callback();

  // Installs the SSDP-message trampoline, pushing each discovered-device JSON
  // as an {"kind":"OnSsdpMsg","dev_info":..} event. OrcaSlicer registers this
  // before start_discovery; the plugin's local-device registry (which
  // connect_printer needs) is populated as these arrive. No-op if absent.
  void register_ssdp_callback();

  // Installs the OnLocalMessage trampoline, pushing each LAN MQTT report as an
  // {"kind":"OnMessage","dev_id":..,"payload":..} event — the SAME kind the
  // cloud path emits, so the existing handler updates status identically.
  // OrcaSlicer registers this before connect_printer; the plugin may refuse a
  // local connection with no message sink. No-op if the symbol is absent.
  void register_local_message_callback();

  // Calls bambu_network_connect_printer(agent, dev_id, dev_ip, username,
  // password, use_ssl). Initiates the local MQTT handshake asynchronously —
  // returns 0 when the request was accepted; readiness arrives via the
  // OnLocalConnected callback. Returns -2 if the plugin lacks the symbol.
  int connect_printer(const std::string& dev_id,
                      const std::string& dev_ip,
                      const std::string& username,
                      const std::string& password,
                      bool use_ssl);

  // Calls bambu_network_send_message_to_printer(agent, dev_id, payload, qos,
  // flag) — the LAN equivalent of send_message. Returns -2 if unavailable.
  int send_message_to_printer(const std::string& dev_id,
                              const std::string& payload,
                              int qos  = 0,
                              int flag = 0);

  // Calls bambu_network_disconnect_printer(agent). Tears down the local
  // connection. Returns -2 if the plugin lacks the symbol.
  int disconnect_printer();

  // Calls bambu_network_install_device_cert(agent, dev_id, lan_only). For a
  // sec_link:"secure" printer the plugin refuses every "print"-namespace cloud
  // publish with -2 (CONNECT_FAILED) until this device's certificate is
  // installed; OrcaSlicer issues it on OnPrinterConnected. Fire-and-forget
  // (returns void) — the plugin confirms by delivering a plain-string
  // "device_cert_installed" through the OnMessage callback. No-op if the symbol
  // is unavailable. Returns true if the call was dispatched.
  bool install_device_cert(const std::string& dev_id, bool lan_only);

  // Calls bambu_network_set_extra_http_header(agent, headers). Brands the
  // plugin agent's own cloud HTTP requests as a BambuStudio/slicer client
  // (X-BBL-* identity). OrcaSlicer issues this once at agent init, before
  // connect_server; without it the cloud session is unbranded and the relay
  // refuses "print"-namespace writes with -2. Returns the plugin rc, or -2 if
  // the symbol is unavailable.
  int set_extra_http_header(const std::map<std::string, std::string>& headers);

  // Calls bambu_network_start_discovery(agent, start, sending). The plugin's
  // own LAN discovery — populates its internal local-device registry, WITHOUT
  // which connect_printer fails before opening a socket. OrcaSlicer calls
  // start_discovery(true, false) right after login. Returns the plugin's bool;
  // false if the symbol is unavailable.
  bool start_discovery(bool start, bool sending);

  // True while a start_print worker thread is running.
  bool print_in_flight() const { return print_in_flight_.load(); }

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

  // bool bambu_network_is_user_login(void *agent)
  // Source: BBLNetworkPlugin.hpp:60
  using fn_is_user_login     = bool(*)(void*);

  // int bambu_network_user_logout(void *agent, bool request)
  // Source: BBLNetworkPlugin.hpp:61
  using fn_user_logout       = int(*)(void*, bool);

  // int bambu_network_get_my_token(void *agent, std::string ticket,
  //                                unsigned int *http_code, std::string *http_body)
  // Synchronous — makes an HTTPS call to Bambu's token endpoint, fills
  // *http_code with the HTTP status and *http_body with the JSON response.
  // Returns 0 on success, negative on error (same codes as change_user).
  // Source: BBLNetworkPlugin.hpp:109, BBLCloudServiceAgent.cpp:620-629.
  using fn_get_my_token      = int(*)(void*, std::string, unsigned int*, std::string*);

  // int bambu_network_get_user_print_info(void *agent, unsigned int* http_code,
  //                                       std::string* http_body)
  // Source: BBLNetworkPlugin.hpp:92. Uses the plugin's stored session token.
  using fn_get_user_print_info = int(*)(void*, unsigned int*, std::string*);

  // int bambu_network_set_user_selected_machine(void *agent, std::string dev_id)
  // Source: BBLNetworkPlugin.hpp:76.
  using fn_set_user_selected_machine = int(*)(void*, std::string);

  // OnMessageFn — std::function callback type matching bambu_networking.hpp:120.
  // EXACTLY 2 parameters: dev_id and msg (NO chan).
  using on_message_fn = std::function<void(std::string dev_id, std::string msg)>;

  // int bambu_network_set_on_message_fn(void *agent, OnMessageFn fn)
  // Registers a callback invoked from the plugin's internal MQTT thread.
  // Source: BBLNetworkPlugin.hpp:39, bambu_networking.hpp:120
  using fn_set_on_message_fn = int(*)(void*, on_message_fn);

  // OnPrinterConnectedFn — fired when a cloud device's publish channel is
  // ready (the signal that send_message stops returning -2 CONNECT_FAILED).
  // 1 param: dev_id. Source: BBLNetworkPlugin.hpp set_on_printer_connected_fn.
  using on_printer_connected_fn = std::function<void(std::string dev_id)>;
  using fn_set_on_printer_connected_fn = int(*)(void*, on_printer_connected_fn);

  // int bambu_network_connect_server(void *agent)
  // Source: BBLNetworkPlugin.hpp:44
  using fn_connect_server    = int(*)(void*);

  // bool bambu_network_is_server_connected(void *agent)
  // Source: BBLNetworkPlugin.hpp:45
  using fn_is_server_connected = bool(*)(void*);

  // int bambu_network_start_subscribe(void *agent, std::string module)
  // Source: BBLNetworkPlugin.hpp:47
  using fn_start_subscribe   = int(*)(void*, std::string);

  // int bambu_network_add_subscribe(void *agent, std::vector<std::string> dev_list)
  // Source: BBLNetworkPlugin.hpp:49
  using fn_add_subscribe     = int(*)(void*, std::vector<std::string>);

  // int bambu_network_start_print(void* agent, PrintParams params,
  //                                OnUpdateStatusFn update_fn,
  //                                WasCancelledFn cancel_fn,
  //                                OnWaitFn wait_fn)
  // PrintParams is passed BY VALUE — the full struct is copied to the stack.
  // Source: BBLNetworkPlugin.hpp:77, bambu_networking.hpp:217-260
  using fn_start_print = int(*)(void*, PrintParams,
                                on_update_status_fn,
                                was_cancelled_fn,
                                on_wait_fn);

  // int bambu_network_send_message(void* agent, std::string dev_id,
  //                                std::string json_str, int qos, int flag)
  // Relays a JSON command string to the printer via the cloud MQTT relay.
  // std::string args are BY VALUE (C++ ABI — SSO struct on stack).
  // Source: BBLNetworkPlugin.hpp:52, BBLPrinterAgent.cpp:163-176
  using fn_send_message = int(*)(void*, std::string, std::string, int, int);

  // OnLocalConnectedFn — fired when a LAN device's local connection changes
  // state: (status, dev_id, msg). status 0 == ready (ConnectStatusOk).
  // Source: bambu_networking.hpp:118, BBLNetworkPlugin.hpp:41
  using on_local_connect_fn =
      std::function<void(int, std::string, std::string)>;
  using fn_set_on_local_connect_fn = int(*)(void*, on_local_connect_fn);

  // int bambu_network_set_on_local_message_fn(void* agent, OnMessageFn fn) —
  // same OnMessageFn (dev_id, msg) as the cloud path. Source: BBLNetworkPlugin.hpp:42
  using fn_set_on_local_message_fn = int(*)(void*, on_message_fn);

  // OnMsgArrivedFn — one arg: discovered-device info JSON.
  // int bambu_network_set_on_ssdp_msg_fn(void* agent, OnMsgArrivedFn fn).
  // Source: bambu_networking.hpp:127, BBLNetworkPlugin.hpp:32
  using on_ssdp_msg_fn = std::function<void(std::string)>;
  using fn_set_on_ssdp_msg_fn = int(*)(void*, on_ssdp_msg_fn);

  // int bambu_network_connect_printer(void* agent, std::string dev_id,
  //     std::string dev_ip, std::string username, std::string password,
  //     bool use_ssl). Source: BBLNetworkPlugin.hpp:53
  using fn_connect_printer =
      int(*)(void*, std::string, std::string, std::string, std::string, bool);

  // int bambu_network_send_message_to_printer(void* agent, std::string dev_id,
  //     std::string json_str, int qos, int flag). Source: BBLNetworkPlugin.hpp:55
  using fn_send_message_to_printer =
      int(*)(void*, std::string, std::string, int, int);

  // int bambu_network_disconnect_printer(void* agent).
  // Source: BBLNetworkPlugin.hpp:54
  using fn_disconnect_printer = int(*)(void*);

  // void bambu_network_install_device_cert(void* agent, std::string dev_id,
  //     bool lan_only). Source: BBLNetworkPlugin.hpp:57
  using fn_install_device_cert = void(*)(void*, std::string, bool);

  // int bambu_network_set_extra_http_header(void* agent,
  //     std::map<std::string,std::string> extra_headers).
  // Source: BBLNetworkPlugin.hpp:89
  using fn_set_extra_http_header =
      int(*)(void*, std::map<std::string, std::string>);

  // bool bambu_network_start_discovery(void* agent, bool start, bool sending).
  // Source: BBLNetworkPlugin.hpp:58
  using fn_start_discovery = bool(*)(void*, bool, bool);

  fn_create_agent       p_create_agent_       = nullptr;
  fn_init_log           p_init_log_           = nullptr;
  fn_set_config_dir     p_set_config_dir_     = nullptr;
  fn_set_cert_file      p_set_cert_file_      = nullptr;
  fn_set_country_code   p_set_country_code_   = nullptr;
  fn_start              p_start_              = nullptr;
  fn_change_user        p_change_user_        = nullptr;
  fn_is_user_login      p_is_user_login_      = nullptr;
  fn_user_logout        p_user_logout_        = nullptr;
  fn_get_my_token       p_get_my_token_       = nullptr;
  fn_get_user_print_info p_get_user_print_info_ = nullptr;
  fn_set_user_selected_machine p_set_user_selected_machine_ = nullptr;
  fn_set_on_message_fn  p_set_on_message_fn_  = nullptr;
  fn_set_on_printer_connected_fn p_set_on_printer_connected_fn_ = nullptr;
  fn_connect_server     p_connect_server_     = nullptr;
  fn_is_server_connected p_is_server_connected_ = nullptr;
  fn_start_subscribe    p_start_subscribe_    = nullptr;
  fn_add_subscribe      p_add_subscribe_      = nullptr;
  fn_start_print        p_start_print_        = nullptr;
  fn_send_message       p_send_message_       = nullptr;
  // LAN-mode symbols are soft-resolved: older plugin builds may lack them, in
  // which case the cloud path still works and these stay null.
  fn_set_on_local_connect_fn   p_set_on_local_connect_fn_   = nullptr;
  fn_set_on_local_message_fn   p_set_on_local_message_fn_   = nullptr;
  fn_set_on_ssdp_msg_fn        p_set_on_ssdp_msg_fn_        = nullptr;
  fn_connect_printer           p_connect_printer_           = nullptr;
  fn_send_message_to_printer   p_send_message_to_printer_   = nullptr;
  fn_disconnect_printer        p_disconnect_printer_        = nullptr;
  fn_install_device_cert       p_install_device_cert_       = nullptr;
  fn_set_extra_http_header     p_set_extra_http_header_     = nullptr;
  fn_start_discovery           p_start_discovery_           = nullptr;

  // True while a start_print worker thread is executing.
  // compare_exchange ensures only one thread can own the "in-flight" slot.
  std::atomic<bool> print_in_flight_{false};
};

}  // namespace bambu_host
