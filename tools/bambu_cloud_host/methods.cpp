// SPDX-License-Identifier: MIT
#include <stdexcept>

#include "event_queue.hpp"
#include "plugin_loader.hpp"
#include "rpc.hpp"

namespace bambu_host {

// Process-global EventQueue instance.  Plugin callbacks push here;
// bridge.poll_events drains it.  Defined here (once, ODR-safe) because
// methods.cpp is always linked.
EventQueue& global_event_queue() {
  static EventQueue g;
  return g;
}

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

// is_user_login — query whether the plugin holds a valid logged-in session.
// Returns {"is_login": bool}.  False when the agent is not bootstrapped.
json method_is_user_login(const json& /*params*/) {
  return {{"is_login", loader().is_user_login()}};
}

// user_logout — sign the current user out of the plugin session.
// Params: {"with_backend_notify": bool, optional, default true} — when true
// the plugin also revokes the session with Bambu's backend (mirrors
// OrcaSlicer's request_user_logout, which passes true).
// Returns {"rc": N} where N == 0 means success.
json method_user_logout(const json& params) {
  bool request = params.value("with_backend_notify", true);
  return {{"rc", loader().user_logout(request)}};
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

// get_user_print_info — return the user's bound device list using the
// plugin's logged-in session (no Python-side token required).
json method_get_user_print_info(const json& /*params*/) {
  return loader().get_user_print_info();
}

// set_user_selected_machine — open the cloud publish channel to a device so
// pushall/commands can be sent (cloud equivalent of connect_printer).
json method_set_user_selected_machine(const json& params) {
  std::string dev_id = params.at("dev_id").get<std::string>();
  return {{"rc", loader().set_user_selected_machine(dev_id)}};
}

// bridge.poll_events — drain all queued plugin callback events.
// Returns: {"events": [...]} where each element is an event JSON object.
// The queue is cleared atomically; calling again returns only new events.
json method_bridge_poll_events(const json& /*params*/) {
  auto events = global_event_queue().drain();
  return {{"events", events}};
}

// _test_push_event — test-only: push an arbitrary event onto the queue.
// The params dict is pushed as-is.  This method must NOT be exposed as a
// real operation but is safe to leave in production (negligible cost;
// significantly simplifies integration testing without the real .so).
json method_test_push_event(const json& params) {
  global_event_queue().push(params);
  return {{"queued", true}};
}

// connect_server — initiate cloud MQTT broker handshake (async).
// Returns {"rc": 0} on success; completes asynchronously via OnServerConnectedFn.
json method_connect_server(const json& /*params*/) {
  return {{"rc", loader().connect_server()}};
}

// is_server_connected — true once the cloud MQTT handshake has completed.
// ``available`` is false when the plugin build lacks the probe symbol, so the
// caller can proceed optimistically instead of blocking forever.
json method_is_server_connected(const json& /*params*/) {
  return {
    {"connected", loader().is_server_connected()},
    {"available", loader().has_server_connected_probe()},
  };
}

// start_subscribe — begin subscription for a given module.
// Params: {"module": "<string>"}  e.g. "printer" or "studio"
json method_start_subscribe(const json& params) {
  std::string module = params.at("module").get<std::string>();
  return {{"rc", loader().start_subscribe(module)}};
}

// add_subscribe — add a list of device serials to the active subscription.
// Params: {"dev_ids": ["<serial1>", "<serial2>", ...]}
json method_add_subscribe(const json& params) {
  auto vec = params.at("dev_ids").get<std::vector<std::string>>();
  return {{"rc", loader().add_subscribe(vec)}};
}

// start_print — submit a print job via the cloud plugin.
//
// Params (JSON keys map directly to PrintParams fields):
//   Required:
//     dev_id          (string) — printer serial number
//     filename        (string) — local path to the sliced 3MF/gcode file
//     connection_type (string) — "cloud" or "lan"
//     plate_index     (int)    — 1-based plate index
//   Optional (all have sane zero/false defaults):
//     task_name, project_name, preset_name, config_filename,
//     ftp_folder, ftp_file, ftp_file_md5, dst_file,
//     nozzle_mapping, ams_mapping, ams_mapping2, ams_mapping_info, nozzles_info,
//     comments, origin_profile_id, stl_design_id, origin_model_id, print_type,
//     dev_name, dev_ip, use_ssl_for_ftp, use_ssl_for_mqtt, username, password,
//     task_bed_leveling, task_flow_cali, task_vibration_cali, task_layer_inspect,
//     task_record_timelapse, task_use_ams, task_bed_type, extra_options,
//     auto_bed_leveling, auto_flow_cali, auto_offset_cali,
//     extruder_cali_manual_mode, task_ext_change_assist, try_emmc_print
//
// Returns {"rc": 0, "in_flight": true} immediately — the job runs in a worker
// thread.  Progress events arrive via bridge.poll_events as OnUpdateStatus frames.
// If another job is already in flight, returns {"rc": -98, "in_flight": false,
// "error": "another job in flight"} without touching the plugin.
json method_start_print(const json& p) {
  PrintParams pp;

  // Helper: get a string field or return a default.
  auto str = [&](const char* key, const std::string& def = "") -> std::string {
    return p.contains(key) ? p.at(key).get<std::string>() : def;
  };
  auto boo = [&](const char* key, bool def = false) -> bool {
    return p.contains(key) ? p.at(key).get<bool>() : def;
  };
  auto num = [&](const char* key, int def = 0) -> int {
    return p.contains(key) ? p.at(key).get<int>() : def;
  };

  pp.dev_id            = str("dev_id");
  pp.task_name         = str("task_name");
  pp.project_name      = str("project_name");
  pp.preset_name       = str("preset_name");
  pp.filename          = str("filename");
  pp.config_filename   = str("config_filename");
  pp.plate_index       = num("plate_index", 0);
  pp.ftp_folder        = str("ftp_folder");
  pp.ftp_file          = str("ftp_file");
  pp.ftp_file_md5      = str("ftp_file_md5");
  pp.nozzle_mapping    = str("nozzle_mapping");
  pp.ams_mapping       = str("ams_mapping");
  pp.ams_mapping2      = str("ams_mapping2");
  pp.ams_mapping_info  = str("ams_mapping_info");
  pp.nozzles_info      = str("nozzles_info");
  pp.connection_type   = str("connection_type", "cloud");
  pp.comments          = str("comments");
  pp.origin_profile_id = num("origin_profile_id", 0);
  pp.stl_design_id     = num("stl_design_id", 0);
  pp.origin_model_id   = str("origin_model_id");
  pp.print_type        = str("print_type");
  pp.dst_file          = str("dst_file");
  pp.dev_name          = str("dev_name");
  pp.dev_ip            = str("dev_ip");
  pp.use_ssl_for_ftp   = boo("use_ssl_for_ftp", false);
  pp.use_ssl_for_mqtt  = boo("use_ssl_for_mqtt", false);
  pp.username          = str("username");
  pp.password          = str("password");

  pp.task_bed_leveling     = boo("task_bed_leveling",     false);
  pp.task_flow_cali        = boo("task_flow_cali",        false);
  pp.task_vibration_cali   = boo("task_vibration_cali",   false);
  pp.task_layer_inspect    = boo("task_layer_inspect",    false);
  pp.task_record_timelapse = boo("task_record_timelapse", false);
  pp.task_use_ams          = boo("task_use_ams",          false);
  pp.task_bed_type         = str("task_bed_type");
  pp.extra_options         = str("extra_options");

  pp.auto_bed_leveling         = num("auto_bed_leveling",         0);
  pp.auto_flow_cali            = num("auto_flow_cali",            0);
  pp.auto_offset_cali          = num("auto_offset_cali",          0);
  pp.extruder_cali_manual_mode = num("extruder_cali_manual_mode", -1);
  pp.task_ext_change_assist    = boo("task_ext_change_assist",    false);
  pp.try_emmc_print            = boo("try_emmc_print",            false);

  // start_print returns 0 (worker launched), -98 (already in flight) or
  // -1 (agent not bootstrapped).  Any non-zero rc means NO worker was
  // spawned and no OnUpdateStatus frame will ever arrive — it must reach
  // the caller verbatim or Python waits on the progress queue forever.
  int rc = loader().start_print(std::move(pp));
  if (rc != 0) {
    return {{"rc", rc}, {"in_flight", false}};
  }
  return {{"rc", 0}, {"in_flight", true}};
}

// send_message — relay a JSON command to the printer via the cloud MQTT relay.
//
// Params:
//   dev_id  (string, required) — printer serial number
//   payload (string, required) — JSON command string (Bambu MQTT envelope)
//   qos     (int,    optional, default 0) — MQTT QoS level
//   flag    (int,    optional, default 0) — routing flag (always 0 for cloud)
//
// Returns {"rc": N} where N == 0 means the message was queued by the plugin.
//
// The call is synchronous and non-blocking — safe to call from the RPC loop
// without a worker thread.
json method_send_message(const json& params) {
  std::string dev_id  = params.at("dev_id").get<std::string>();
  std::string payload = params.at("payload").get<std::string>();
  int qos             = params.value("qos",  0);
  int flag            = params.value("flag", 0);
  return {{"rc", loader().send_message(dev_id, payload, qos, flag)}};
}

// connect_printer — open the plugin's authenticated LOCAL (LAN) MQTT
// connection to a printer. Required before send_message_to_printer works.
//
// Params:
//   dev_id   (string, required) — printer serial
//   dev_ip   (string, required) — printer LAN IP
//   username (string, optional, default "bblp")
//   password (string, required) — LAN access code
//   use_ssl  (bool,   optional, default true)
//
// Returns {"rc": N}. rc==0 means the request was accepted; readiness arrives
// asynchronously as an OnLocalConnected event (status==0). rc==-2 means the
// plugin build lacks LAN support.
json method_connect_printer(const json& params) {
  std::string dev_id   = params.at("dev_id").get<std::string>();
  std::string dev_ip   = params.at("dev_ip").get<std::string>();
  std::string username = params.value("username", std::string("bblp"));
  std::string password = params.at("password").get<std::string>();
  bool use_ssl         = params.value("use_ssl", true);
  return {{"rc", loader().connect_printer(dev_id, dev_ip, username, password,
                                          use_ssl)}};
}

// send_message_to_printer — relay a JSON command over the plugin's LAN
// connection (the authenticated local publish). Same params as send_message.
json method_send_message_to_printer(const json& params) {
  std::string dev_id  = params.at("dev_id").get<std::string>();
  std::string payload = params.at("payload").get<std::string>();
  int qos             = params.value("qos",  0);
  int flag            = params.value("flag", 0);
  return {{"rc", loader().send_message_to_printer(dev_id, payload, qos, flag)}};
}

// disconnect_printer — tear down the plugin's local connection.
json method_disconnect_printer(const json& /*params*/) {
  return {{"rc", loader().disconnect_printer()}};
}

// install_device_cert — unlock the secure cloud control channel. A
// sec_link:"secure" printer rejects every "print"-namespace cloud publish with
// -2 until its certificate is installed; this asks the plugin to install it.
// Fire-and-forget — success is reported later as a "device_cert_installed"
// OnMessage string. Params: dev_id (string), lan_only (bool, default false —
// false for a cloud printer). Returns {"dispatched": bool}.
json method_install_device_cert(const json& params) {
  std::string dev_id = params.at("dev_id").get<std::string>();
  bool lan_only      = params.value("lan_only", false);
  return {{"dispatched", loader().install_device_cert(dev_id, lan_only)}};
}

// set_extra_http_header — brand the plugin agent's cloud HTTP requests as a
// BambuStudio/slicer client. OrcaSlicer issues this once at agent init (before
// connect_server); without it the relay refuses "print"-namespace writes (-2).
// Params: headers (object of string->string). Returns {"rc": <plugin rc>}.
json method_set_extra_http_header(const json& params) {
  std::map<std::string, std::string> headers;
  const json& h = params.value("headers", json::object());
  for (auto it = h.begin(); it != h.end(); ++it) {
    if (it.value().is_string()) {
      headers[it.key()] = it.value().get<std::string>();
    }
  }
  return {{"rc", loader().set_extra_http_header(headers)}};
}

// start_discovery — run the plugin's LAN discovery so connect_printer works.
// Params: start (bool, default true), sending (bool, default false).
json method_start_discovery(const json& params) {
  bool start   = params.value("start",   true);
  bool sending = params.value("sending", false);
  return {{"ok", loader().start_discovery(start, sending)}};
}

// send_burst — relay several JSON commands back-to-back over the cloud relay,
// with NO RPC round-trip between them. Bambu's cloud publish channel is only
// open for a few ms around a send; a write issued from Python even ~20ms after
// a successful pushall (one event-loop hop later) misses the window. Sending
// the whole burst inside one RPC keeps every message within microseconds of the
// first, so a write rides the same open window as the pushall ahead of it.
//
// Params: dev_id (string), payloads (array of JSON strings), qos/flag (int,
// optional). Returns {"rcs": [rc, rc, ...]} — one per payload, in order.
json method_send_burst(const json& params) {
  std::string dev_id = params.at("dev_id").get<std::string>();
  auto payloads = params.at("payloads").get<std::vector<std::string>>();
  int qos  = params.value("qos",  0);
  int flag = params.value("flag", 0);
  json rcs = json::array();
  for (const auto& p : payloads) {
    rcs.push_back(loader().send_message(dev_id, p, qos, flag));
  }
  return {{"rcs", rcs}};
}

}  // namespace

json dispatch_method(const std::string& method, const json& params) {
  if (method == "echo")                return method_echo(params);
  if (method == "init_plugin")         return method_init_plugin(params);
  if (method == "change_user")         return method_change_user(params);
  if (method == "is_user_login")       return method_is_user_login(params);
  if (method == "user_logout")         return method_user_logout(params);
  if (method == "get_my_token")        return method_get_my_token(params);
  if (method == "get_user_print_info") return method_get_user_print_info(params);
  if (method == "set_user_selected_machine") return method_set_user_selected_machine(params);
  if (method == "bridge.poll_events")  return method_bridge_poll_events(params);
  if (method == "_test_push_event")    return method_test_push_event(params);
  if (method == "connect_server")      return method_connect_server(params);
  if (method == "is_server_connected") return method_is_server_connected(params);
  if (method == "start_subscribe")     return method_start_subscribe(params);
  if (method == "add_subscribe")       return method_add_subscribe(params);
  if (method == "start_print")         return method_start_print(params);
  if (method == "send_message")        return method_send_message(params);
  if (method == "connect_printer")     return method_connect_printer(params);
  if (method == "send_message_to_printer") return method_send_message_to_printer(params);
  if (method == "send_burst")          return method_send_burst(params);
  if (method == "disconnect_printer")  return method_disconnect_printer(params);
  if (method == "install_device_cert") return method_install_device_cert(params);
  if (method == "set_extra_http_header") return method_set_extra_http_header(params);
  if (method == "start_discovery")     return method_start_discovery(params);
  throw std::runtime_error("unknown method: " + method);
}

}  // namespace bambu_host
