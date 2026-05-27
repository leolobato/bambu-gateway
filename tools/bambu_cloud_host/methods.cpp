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

// get_my_token — exchange a Bambu ticket for an access-token bundle.
// Params: {"ticket": "<ticket string from OAuth redirect>"}
// Returns the token JSON returned by the plugin (accessToken/access_token,
// refresh_token, expires_in, refresh_expires_in, http_code).
// Throws (→ RPC error response) if the plugin call fails.
json method_get_my_token(const json& params) {
  std::string ticket = params.at("ticket").get<std::string>();
  return loader().get_my_token(ticket);
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
// Returns {"rc": N} where N == 0 means the job was accepted by the cloud/printer.
// Negative rc values are BAMBU_NETWORK_ERR_* codes — see discovery notes §6.
//
// Progress events are pushed to the global event queue as the call proceeds;
// drain them with bridge.poll_events while this call is running.
// NOTE: start_print is synchronous — it BLOCKS until the job finishes or errors.
// The Python caller must run this in a thread and poll bridge.poll_events
// concurrently to receive OnUpdateStatus progress events.
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

  int rc = loader().start_print(std::move(pp));
  return {{"rc", rc}};
}

}  // namespace

json dispatch_method(const std::string& method, const json& params) {
  if (method == "echo")                return method_echo(params);
  if (method == "init_plugin")         return method_init_plugin(params);
  if (method == "change_user")         return method_change_user(params);
  if (method == "get_my_token")        return method_get_my_token(params);
  if (method == "bridge.poll_events")  return method_bridge_poll_events(params);
  if (method == "_test_push_event")    return method_test_push_event(params);
  if (method == "connect_server")      return method_connect_server(params);
  if (method == "start_subscribe")     return method_start_subscribe(params);
  if (method == "add_subscribe")       return method_add_subscribe(params);
  if (method == "start_print")         return method_start_print(params);
  throw std::runtime_error("unknown method: " + method);
}

}  // namespace bambu_host
