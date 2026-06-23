// SPDX-License-Identifier: MIT
//
// Plugin loader — dlopen + bootstrap for libbambu_networking.so.
//
// Bootstrap sequence (from GUI_App.cpp:3838-3889 and discovery notes):
//   1. bambu_network_create_agent(log_dir)       → void* agent
//   2. bambu_network_set_config_dir(agent, dir)
//   3. bambu_network_init_log(agent)
//   4. bambu_network_set_cert_file(agent, folder, filename)
//   5. bambu_network_set_country_code(agent, cc)
//   6. bambu_network_start(agent)
//   7. <change_user and other user-facing calls now valid>
//
// All string parameters are std::string BY VALUE (C++ ABI — do NOT pass
// const char* pointers).  See plugin_loader.hpp for the full ABI note.

#include "plugin_loader.hpp"

#include <dlfcn.h>
#include <sys/stat.h>

#include "event_queue.hpp"

#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <thread>

namespace bambu_host {

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

namespace {

// Resolve a symbol or throw std::runtime_error.
template <typename FnT>
FnT must_resolve(void* h, const char* name) {
  // dlsym returns void*; reinterpret_cast to the correct function pointer type.
  void* raw = dlsym(h, name);
  if (!raw) {
    throw std::runtime_error(std::string("dlsym failed: ") + name +
                             " — " + dlerror());
  }
  FnT fn{};
  // Use memcpy to sidestep strict-aliasing: copy the void* bits into the
  // function pointer.  This is the standards-conforming way when
  // sizeof(void*) == sizeof(FnT) (true on all targets we care about).
  static_assert(sizeof(void*) == sizeof(FnT),
                "function pointer size mismatch");
  std::memcpy(&fn, &raw, sizeof(FnT));
  return fn;
}

// Resolve a symbol or return nullptr (for optional ABI functions that older
// plugin builds may not export). Unlike must_resolve, never throws.
template <typename FnT>
FnT try_resolve(void* h, const char* name) {
  void* raw = dlsym(h, name);
  if (!raw) return nullptr;
  FnT fn{};
  static_assert(sizeof(void*) == sizeof(FnT),
                "function pointer size mismatch");
  std::memcpy(&fn, &raw, sizeof(FnT));
  return fn;
}

// Return env var or a default string (never null).
const char* env_or(const char* name, const char* fallback) {
  const char* v = std::getenv(name);
  return (v && *v) ? v : fallback;
}

// Ensure a directory exists; create it (and any parents) if not.
// Returns 0 on success, -1 on failure (sets errno).
int ensure_dir(const std::string& path) {
  try {
    std::filesystem::create_directories(path);
    return 0;
  } catch (...) {
    return -1;
  }
}

}  // namespace

// ---------------------------------------------------------------------------
// PluginLoader implementation
// ---------------------------------------------------------------------------

void PluginLoader::load_from_env() {
  const char* so_path = std::getenv("PJARCZAK_BAMBU_NETWORK_SO");
  if (!so_path || !*so_path) {
    throw std::runtime_error(
        "PJARCZAK_BAMBU_NETWORK_SO env var is not set or is empty");
  }

  // Use RTLD_LAZY (not RTLD_NOW) to match OrcaSlicer-bambulab's own usage
  // (BBLNetworkPlugin.cpp:232,240).  RTLD_NOW causes SIGBUS on some glibc
  // versions because the TLS block cannot be extended for dynamically-dlopened
  // libraries with a PT_TLS segment after process startup.  RTLD_LAZY defers
  // symbol binding so those TLS relocations only fire when the function is
  // first called, at which point glibc handles them via __tls_get_addr.
  //
  // RTLD_NOLOAD check: if the caller pre-loaded the library via LD_PRELOAD
  // (or a prior dlopen), reuse the existing mapping.  This avoids double-init
  // and works around TLS-slot exhaustion on older or emulated glibc.
  dl_handle_ = dlopen(so_path, RTLD_LAZY | RTLD_NOLOAD);
  if (dl_handle_) {
    std::fprintf(stderr,
                 "bambu_cloud_host: reusing already-loaded %s\n", so_path);
  } else {
    // Not already loaded — open it fresh.
    // RTLD_GLOBAL ensures the library's symbols are visible for its own
    // internal cross-symbol references (some versions of libbambu_networking
    // require this for their own dlopen'd sub-libraries).
    dl_handle_ = dlopen(so_path, RTLD_LAZY | RTLD_GLOBAL);
  }
  if (!dl_handle_) {
    throw std::runtime_error(std::string("dlopen failed: ") + dlerror());
  }

  // Resolve all 7 symbols.  Any missing symbol throws immediately so the
  // caller gets a clear error message before we try to call anything.
  p_create_agent_      = must_resolve<fn_create_agent>     (dl_handle_, "bambu_network_create_agent");
  p_init_log_          = must_resolve<fn_init_log>          (dl_handle_, "bambu_network_init_log");
  p_set_config_dir_    = must_resolve<fn_set_config_dir>    (dl_handle_, "bambu_network_set_config_dir");
  p_set_cert_file_     = must_resolve<fn_set_cert_file>     (dl_handle_, "bambu_network_set_cert_file");
  p_set_country_code_  = must_resolve<fn_set_country_code>  (dl_handle_, "bambu_network_set_country_code");
  p_start_             = must_resolve<fn_start>             (dl_handle_, "bambu_network_start");
  p_change_user_       = must_resolve<fn_change_user>       (dl_handle_, "bambu_network_change_user");
  p_is_user_login_     = must_resolve<fn_is_user_login>     (dl_handle_, "bambu_network_is_user_login");
  p_user_logout_       = must_resolve<fn_user_logout>       (dl_handle_, "bambu_network_user_logout");
  p_get_my_token_      = must_resolve<fn_get_my_token>      (dl_handle_, "bambu_network_get_my_token");
  p_get_user_print_info_ = must_resolve<fn_get_user_print_info>(dl_handle_, "bambu_network_get_user_print_info");
  p_set_user_selected_machine_ = must_resolve<fn_set_user_selected_machine>(dl_handle_, "bambu_network_set_user_selected_machine");
  p_set_on_message_fn_ = must_resolve<fn_set_on_message_fn> (dl_handle_, "bambu_network_set_on_message_fn");
  p_set_on_printer_connected_fn_ = must_resolve<fn_set_on_printer_connected_fn>(dl_handle_, "bambu_network_set_on_printer_connected_fn");
  p_connect_server_    = must_resolve<fn_connect_server>    (dl_handle_, "bambu_network_connect_server");
  p_is_server_connected_ = try_resolve<fn_is_server_connected>(dl_handle_, "bambu_network_is_server_connected");
  p_start_subscribe_   = must_resolve<fn_start_subscribe>   (dl_handle_, "bambu_network_start_subscribe");
  p_add_subscribe_     = must_resolve<fn_add_subscribe>     (dl_handle_, "bambu_network_add_subscribe");
  p_start_print_       = must_resolve<fn_start_print>       (dl_handle_, "bambu_network_start_print");
  p_send_message_      = must_resolve<fn_send_message>      (dl_handle_, "bambu_network_send_message");

  // LAN-mode publishing (optional — soft-resolve so a plugin without these
  // symbols still boots with the cloud path intact).
  p_set_on_local_connect_fn_ = try_resolve<fn_set_on_local_connect_fn>(dl_handle_, "bambu_network_set_on_local_connect_fn");
  p_set_on_local_message_fn_ = try_resolve<fn_set_on_local_message_fn>(dl_handle_, "bambu_network_set_on_local_message_fn");
  p_set_on_ssdp_msg_fn_      = try_resolve<fn_set_on_ssdp_msg_fn>     (dl_handle_, "bambu_network_set_on_ssdp_msg_fn");
  p_connect_printer_         = try_resolve<fn_connect_printer>        (dl_handle_, "bambu_network_connect_printer");
  p_send_message_to_printer_ = try_resolve<fn_send_message_to_printer>(dl_handle_, "bambu_network_send_message_to_printer");
  p_disconnect_printer_      = try_resolve<fn_disconnect_printer>     (dl_handle_, "bambu_network_disconnect_printer");
  p_start_discovery_         = try_resolve<fn_start_discovery>        (dl_handle_, "bambu_network_start_discovery");
  p_install_device_cert_     = try_resolve<fn_install_device_cert>   (dl_handle_, "bambu_network_install_device_cert");
  p_set_extra_http_header_   = try_resolve<fn_set_extra_http_header> (dl_handle_, "bambu_network_set_extra_http_header");
  std::fprintf(stderr,
               "bambu_cloud_host: LAN symbols: connect_printer=%s "
               "send_message_to_printer=%s on_local_connect=%s disconnect=%s "
               "install_device_cert=%s\n",
               p_connect_printer_         ? "ok" : "MISSING",
               p_send_message_to_printer_ ? "ok" : "MISSING",
               p_set_on_local_connect_fn_ ? "ok" : "MISSING",
               p_disconnect_printer_      ? "ok" : "MISSING",
               p_install_device_cert_     ? "ok" : "MISSING");
}

int PluginLoader::bootstrap() {
  if (!dl_handle_) {
    throw std::runtime_error("bootstrap() called before load_from_env()");
  }

  // Determine the plugin state directory.
  //
  // Priority:
  //   1. $PJARCZAK_BAMBU_PLUGIN_DIR/state  (set by the Python host)
  //   2. /tmp/bambu-plugin-state           (fallback for manual testing)
  //
  // Both log_dir and config_dir point at the same directory; the plugin uses
  // them to write logs and cache config respectively.
  std::string state_dir;
  const char* plugin_dir = std::getenv("PJARCZAK_BAMBU_PLUGIN_DIR");
  if (plugin_dir && *plugin_dir) {
    state_dir = std::string(plugin_dir) + "/state";
  } else {
    state_dir = "/tmp/bambu-plugin-state";
  }

  if (ensure_dir(state_dir) != 0) {
    // Non-fatal; the plugin might still work if the dir exists from a previous
    // run.  Log to stderr and continue.
    std::fprintf(stderr,
                 "bambu_cloud_host: warning: could not create state dir '%s'\n",
                 state_dir.c_str());
  }

  const std::string log_dir    = state_dir;
  const std::string config_dir = state_dir;

  // TLS cert bundle — use the Debian/Ubuntu system bundle inside the
  // container.  If this doesn't work (plugin returns non-zero from
  // set_cert_file), set $BAMBU_CERT_FOLDER / $BAMBU_CERT_FILE to override.
  const std::string cert_folder   = env_or("BAMBU_CERT_FOLDER",   "/etc/ssl/certs");
  const std::string cert_filename = env_or("BAMBU_CERT_FILE",     "ca-certificates.crt");

  const std::string country_code  = env_or("BAMBU_CLOUD_REGION",  "US");

  std::fprintf(stderr,
               "bambu_cloud_host: bootstrap: log_dir=%s config_dir=%s "
               "cert=%s/%s country=%s\n",
               log_dir.c_str(), config_dir.c_str(),
               cert_folder.c_str(), cert_filename.c_str(),
               country_code.c_str());

  // --- Step 1: create the agent object ---
  // bambu_network_create_agent(std::string log_dir) → void*
  // Passing std::string by value as required by the C++ ABI.
  agent_ = p_create_agent_(log_dir);
  if (!agent_) {
    std::fprintf(stderr, "bambu_cloud_host: create_agent returned null\n");
    return -1;
  }
  std::fprintf(stderr, "bambu_cloud_host: create_agent OK, agent=%p\n", agent_);

  // --- Step 2: set config dir ---
  int rc = p_set_config_dir_(agent_, config_dir);
  std::fprintf(stderr, "bambu_cloud_host: set_config_dir rc=%d\n", rc);
  if (rc != 0) return rc;

  // --- Step 3: init log ---
  rc = p_init_log_(agent_);
  std::fprintf(stderr, "bambu_cloud_host: init_log rc=%d\n", rc);
  if (rc != 0) return rc;

  // --- Step 4: set cert file ---
  rc = p_set_cert_file_(agent_, cert_folder, cert_filename);
  std::fprintf(stderr, "bambu_cloud_host: set_cert_file rc=%d\n", rc);
  if (rc != 0) return rc;

  // --- Step 5: set country code ---
  rc = p_set_country_code_(agent_, country_code);
  std::fprintf(stderr, "bambu_cloud_host: set_country_code rc=%d\n", rc);
  if (rc != 0) return rc;

  // --- Step 6: start ---
  rc = p_start_(agent_);
  std::fprintf(stderr, "bambu_cloud_host: start rc=%d\n", rc);
  if (rc != 0) return rc;

  // --- Step 7: register OnMessage trampoline (non-fatal) ---
  // Wrap in try/catch: if this fails the change_user / get_my_token use case
  // still works fine without a subscription callback.
  try {
    register_message_callback();
    std::fprintf(stderr, "bambu_cloud_host: register_message_callback OK\n");
  } catch (const std::exception& e) {
    std::fprintf(stderr,
                 "bambu_cloud_host: register_message_callback WARN: %s\n",
                 e.what());
    // Non-fatal — continue without the callback.
  }

  try {
    register_printer_connected_callback();
    std::fprintf(stderr,
                 "bambu_cloud_host: register_printer_connected_callback OK\n");
  } catch (const std::exception& e) {
    std::fprintf(stderr,
                 "bambu_cloud_host: register_printer_connected_callback WARN: %s\n",
                 e.what());
  }

  try {
    register_local_connect_callback();
    std::fprintf(stderr,
                 "bambu_cloud_host: register_local_connect_callback OK\n");
  } catch (const std::exception& e) {
    std::fprintf(stderr,
                 "bambu_cloud_host: register_local_connect_callback WARN: %s\n",
                 e.what());
  }

  try {
    register_local_message_callback();
    std::fprintf(stderr,
                 "bambu_cloud_host: register_local_message_callback OK\n");
  } catch (const std::exception& e) {
    std::fprintf(stderr,
                 "bambu_cloud_host: register_local_message_callback WARN: %s\n",
                 e.what());
  }

  try {
    register_ssdp_callback();
    std::fprintf(stderr, "bambu_cloud_host: register_ssdp_callback OK\n");
  } catch (const std::exception& e) {
    std::fprintf(stderr,
                 "bambu_cloud_host: register_ssdp_callback WARN: %s\n", e.what());
  }

  return 0;
}

int PluginLoader::change_user(const std::string& canonical_login_json) {
  if (!agent_) {
    // bootstrap() was not called or returned error.
    return -1;
  }
  // bambu_network_change_user(void* agent, std::string user_info)
  // std::string is passed BY VALUE — construct here, pass directly.
  int rc = p_change_user_(agent_, canonical_login_json);
  std::fprintf(stderr, "bambu_cloud_host: change_user rc=%d\n", rc);
  return rc;
}

bool PluginLoader::is_user_login() {
  if (!agent_) return false;
  return p_is_user_login_(agent_);
}

int PluginLoader::user_logout(bool request) {
  if (!agent_) return -1;
  int rc = p_user_logout_(agent_, request);
  std::fprintf(stderr, "bambu_cloud_host: user_logout request=%d rc=%d\n",
               request ? 1 : 0, rc);
  return rc;
}

// bambu_network_get_my_token — synchronous ticket-to-token exchange.
//
// C signature (BBLNetworkPlugin.hpp:109):
//   typedef int (*func_get_my_token)(void *agent, std::string ticket,
//                                    unsigned int *http_code,
//                                    std::string *http_body);
//
// The plugin makes an HTTPS call to Bambu's token endpoint, fills *http_code
// with the HTTP status code (200 on success) and *http_body with a JSON
// string containing the token fields (accessToken / access_token, etc.).
// Returns 0 on success, negative on error.  This matches the "sync with
// out-params" pattern used by change_user and all other plugin functions.
//
// OrcaSlicer call site (HttpServer.cpp:294-304):
//   const int token_result = agent->get_my_token(ticket, &token_http_code,
//                                                &token_http_body);
//   if (token_result == 0) {
//       token_j = json::parse(token_http_body);
//       access_token = json_string_first(token_j,
//                          {"accessToken", "access_token", "token"});
//   }
nlohmann::json PluginLoader::get_my_token(const std::string& ticket) {
  if (!agent_) {
    throw std::runtime_error("get_my_token: agent not bootstrapped");
  }

  unsigned int http_code = 0;
  std::string  http_body;

  // Call is synchronous — blocks until the HTTPS round-trip completes.
  int rc = p_get_my_token_(agent_, ticket, &http_code, &http_body);
  std::fprintf(stderr,
               "bambu_cloud_host: get_my_token rc=%d http_code=%u body_len=%zu\n",
               rc, http_code, http_body.size());

  if (rc != 0) {
    throw std::runtime_error("get_my_token rc=" + std::to_string(rc) +
                             " http_code=" + std::to_string(http_code));
  }

  // Parse the JSON body returned by the plugin.  OrcaSlicer (HttpServer.cpp:304)
  // normalises via json_string_first({"accessToken","access_token","token"});
  // we return the raw parsed object so the Python side can normalise the same way.
  nlohmann::json body_j;
  try {
    body_j = nlohmann::json::parse(http_body);
  } catch (const std::exception& e) {
    throw std::runtime_error(
        std::string("get_my_token: failed to parse response body: ") + e.what() +
        " body=" + http_body.substr(0, 200));
  }

  // Surface http_code alongside the token fields so the caller can log it.
  body_j["http_code"] = http_code;
  return body_j;
}

int PluginLoader::set_user_selected_machine(const std::string& dev_id) {
  if (!agent_) {
    throw std::runtime_error("set_user_selected_machine: agent not bootstrapped");
  }
  int rc = p_set_user_selected_machine_(agent_, dev_id);
  std::fprintf(stderr,
               "bambu_cloud_host: set_user_selected_machine dev_id=%s rc=%d\n",
               dev_id.c_str(), rc);
  return rc;
}

nlohmann::json PluginLoader::get_user_print_info() {
  if (!agent_) {
    throw std::runtime_error("get_user_print_info: agent not bootstrapped");
  }

  unsigned int http_code = 0;
  std::string  http_body;

  // Synchronous HTTPS round-trip using the plugin's own logged-in session —
  // no Python-side token needed. Returns {"devices":[{dev_id, dev_name,
  // dev_online, dev_model_name, dev_product_name, ...}]} (DevManager.cpp:719).
  int rc = p_get_user_print_info_(agent_, &http_code, &http_body);
  std::fprintf(stderr,
               "bambu_cloud_host: get_user_print_info rc=%d http_code=%u body_len=%zu\n",
               rc, http_code, http_body.size());

  if (rc != 0) {
    throw std::runtime_error("get_user_print_info rc=" + std::to_string(rc) +
                             " http_code=" + std::to_string(http_code));
  }

  nlohmann::json body_j;
  try {
    body_j = nlohmann::json::parse(http_body);
  } catch (const std::exception& e) {
    throw std::runtime_error(
        std::string("get_user_print_info: failed to parse response body: ") +
        e.what() + " body=" + http_body.substr(0, 200));
  }

  body_j["http_code"] = http_code;
  return body_j;
}

// register_message_callback — installs a trampoline that pushes every incoming
// cloud MQTT message into the process-global EventQueue.
//
// The trampoline captures nothing (pure function with global side-effect) so it
// is safe to call from multiple plugin threads concurrently.  EventQueue::push
// takes a mutex, ensuring thread safety.
//
// Discovery (Phase A): OnMessageFn is std::function<void(string, string)> — ONLY
// 2 params (dev_id, msg).  There is NO chan/channel parameter.  See:
//   bambu_networking.hpp:120, BBLNetworkPlugin.hpp:39
void PluginLoader::register_message_callback() {
  if (!agent_ || !p_set_on_message_fn_) {
    throw std::runtime_error("register_message_callback: agent not bootstrapped");
  }
  // Trampoline: capture nothing — push into the process-global queue.
  on_message_fn cb = [](std::string dev_id, std::string msg) {
    json event = {
      {"kind",    "OnMessage"},
      {"dev_id",  std::move(dev_id)},
      {"payload", std::move(msg)},
    };
    global_event_queue().push(std::move(event));
  };
  int rc = p_set_on_message_fn_(agent_, std::move(cb));
  if (rc != 0) {
    throw std::runtime_error(
        "set_on_message_fn rc=" + std::to_string(rc));
  }
}

void PluginLoader::register_printer_connected_callback() {
  if (!agent_ || !p_set_on_printer_connected_fn_) {
    throw std::runtime_error(
        "register_printer_connected_callback: agent not bootstrapped");
  }
  on_printer_connected_fn cb = [](std::string dev_id) {
    std::fprintf(stderr,
                 "bambu_cloud_host: on_printer_connected dev_id=%s\n",
                 dev_id.c_str());
    json event = {
      {"kind",   "OnPrinterConnected"},
      {"dev_id", std::move(dev_id)},
    };
    global_event_queue().push(std::move(event));
  };
  int rc = p_set_on_printer_connected_fn_(agent_, std::move(cb));
  if (rc != 0) {
    throw std::runtime_error(
        "set_on_printer_connected_fn rc=" + std::to_string(rc));
  }
}

void PluginLoader::register_local_connect_callback() {
  if (!agent_ || !p_set_on_local_connect_fn_) {
    // Optional symbol — absent on older plugin builds. Not fatal.
    throw std::runtime_error(
        "register_local_connect_callback: symbol unavailable");
  }
  on_local_connect_fn cb =
      [](int status, std::string dev_id, std::string msg) {
    std::fprintf(stderr,
                 "bambu_cloud_host: on_local_connect dev_id=%s status=%d msg=%s\n",
                 dev_id.c_str(), status, msg.c_str());
    json event = {
      {"kind",   "OnLocalConnected"},
      {"dev_id", std::move(dev_id)},
      {"status", status},
      {"msg",    std::move(msg)},
    };
    global_event_queue().push(std::move(event));
  };
  int rc = p_set_on_local_connect_fn_(agent_, std::move(cb));
  if (rc != 0) {
    throw std::runtime_error(
        "set_on_local_connect_fn rc=" + std::to_string(rc));
  }
}

void PluginLoader::register_local_message_callback() {
  if (!agent_ || !p_set_on_local_message_fn_) {
    throw std::runtime_error(
        "register_local_message_callback: symbol unavailable");
  }
  // Emit the SAME "OnMessage" kind as the cloud path so the existing handler
  // routes LAN reports into the printer's status identically.
  on_message_fn cb = [](std::string dev_id, std::string msg) {
    json event = {
      {"kind",    "OnMessage"},
      {"dev_id",  std::move(dev_id)},
      {"payload", std::move(msg)},
    };
    global_event_queue().push(std::move(event));
  };
  int rc = p_set_on_local_message_fn_(agent_, std::move(cb));
  if (rc != 0) {
    throw std::runtime_error(
        "set_on_local_message_fn rc=" + std::to_string(rc));
  }
}

void PluginLoader::register_ssdp_callback() {
  if (!agent_ || !p_set_on_ssdp_msg_fn_) {
    throw std::runtime_error("register_ssdp_callback: symbol unavailable");
  }
  on_ssdp_msg_fn cb = [](std::string dev_info) {
    std::fprintf(stderr, "bambu_cloud_host: on_ssdp_msg %s\n", dev_info.c_str());
    json event = {
      {"kind",     "OnSsdpMsg"},
      {"dev_info", std::move(dev_info)},
    };
    global_event_queue().push(std::move(event));
  };
  int rc = p_set_on_ssdp_msg_fn_(agent_, std::move(cb));
  if (rc != 0) {
    throw std::runtime_error(
        "set_on_ssdp_msg_fn rc=" + std::to_string(rc));
  }
}

int PluginLoader::connect_printer(const std::string& dev_id,
                                  const std::string& dev_ip,
                                  const std::string& username,
                                  const std::string& password,
                                  bool use_ssl) {
  if (!agent_) return -1;
  if (!p_connect_printer_) return -2;  // plugin lacks LAN support
  int rc = p_connect_printer_(agent_, dev_id, dev_ip, username, password,
                              use_ssl);
  std::fprintf(stderr,
               "bambu_cloud_host: connect_printer dev_id=%s ip=%s ssl=%d rc=%d\n",
               dev_id.c_str(), dev_ip.c_str(), use_ssl ? 1 : 0, rc);
  return rc;
}

int PluginLoader::send_message_to_printer(const std::string& dev_id,
                                          const std::string& payload,
                                          int qos,
                                          int flag) {
  if (!agent_) return -1;
  if (!p_send_message_to_printer_) return -2;
  int rc = p_send_message_to_printer_(agent_, dev_id, payload, qos, flag);
  std::fprintf(stderr,
               "bambu_cloud_host: send_message_to_printer dev_id=%s qos=%d "
               "flag=%d rc=%d\n",
               dev_id.c_str(), qos, flag, rc);
  return rc;
}

int PluginLoader::disconnect_printer() {
  if (!agent_) return -1;
  if (!p_disconnect_printer_) return -2;
  int rc = p_disconnect_printer_(agent_);
  std::fprintf(stderr, "bambu_cloud_host: disconnect_printer rc=%d\n", rc);
  return rc;
}

bool PluginLoader::install_device_cert(const std::string& dev_id,
                                       bool lan_only) {
  if (!agent_ || !p_install_device_cert_) {
    std::fprintf(stderr,
                 "bambu_cloud_host: install_device_cert dev_id=%s SKIPPED "
                 "(symbol %s)\n",
                 dev_id.c_str(), p_install_device_cert_ ? "present, no agent"
                                                        : "MISSING");
    return false;
  }
  // void return — the plugin installs the cert asynchronously and confirms by
  // delivering a "device_cert_installed" string through the OnMessage callback.
  p_install_device_cert_(agent_, dev_id, lan_only);
  std::fprintf(stderr,
               "bambu_cloud_host: install_device_cert dev_id=%s lan_only=%d\n",
               dev_id.c_str(), lan_only ? 1 : 0);
  return true;
}

int PluginLoader::set_extra_http_header(
    const std::map<std::string, std::string>& headers) {
  if (!agent_) return -1;
  if (!p_set_extra_http_header_) return -2;
  int rc = p_set_extra_http_header_(agent_, headers);
  std::fprintf(stderr,
               "bambu_cloud_host: set_extra_http_header (%zu headers) rc=%d\n",
               headers.size(), rc);
  return rc;
}

bool PluginLoader::start_discovery(bool start, bool sending) {
  if (!agent_ || !p_start_discovery_) return false;
  bool ok = p_start_discovery_(agent_, start, sending);
  std::fprintf(stderr, "bambu_cloud_host: start_discovery start=%d sending=%d ok=%d\n",
               start ? 1 : 0, sending ? 1 : 0, ok ? 1 : 0);
  return ok;
}

int PluginLoader::connect_server() {
  if (!agent_) return -1;
  int rc = p_connect_server_(agent_);
  std::fprintf(stderr, "bambu_cloud_host: connect_server rc=%d\n", rc);
  return rc;
}

bool PluginLoader::is_server_connected() {
  if (!agent_ || !p_is_server_connected_) return false;
  return p_is_server_connected_(agent_);
}

int PluginLoader::start_subscribe(const std::string& module) {
  if (!agent_) return -1;
  int rc = p_start_subscribe_(agent_, module);
  std::fprintf(stderr, "bambu_cloud_host: start_subscribe module=%s rc=%d\n",
               module.c_str(), rc);
  return rc;
}

int PluginLoader::add_subscribe(const std::vector<std::string>& dev_ids) {
  if (!agent_) return -1;
  int rc = p_add_subscribe_(agent_, dev_ids);
  std::fprintf(stderr, "bambu_cloud_host: add_subscribe n=%zu rc=%d\n",
               dev_ids.size(), rc);
  return rc;
}

// start_print — non-blocking cloud print dispatch.
//
// Spawns a detached worker thread that calls bambu_network_start_print and
// returns 0 immediately so the RPC loop can continue servicing poll_events
// while the plugin does its blocking work.
//
// At most one job can be in-flight at a time.  A second call while the first
// is still running returns -98 without touching the plugin.
//
// The worker thread:
//   1. Calls the plugin (blocking — may take many seconds).
//   2. Clears the in-flight flag.
//   3. On exception, pushes a sentinel OnUpdateStatus(stage=7, code=-99) so
//      the Python side sees an ERROR frame rather than a silent stall.
//
// ABI note: PrintParams is captured by value into the lambda so it outlives
// the calling stack frame.  std::string members are owned by the copy.
int PluginLoader::start_print(PrintParams params) {
  if (!agent_) return -1;

  // Guard: only one concurrent job.
  bool expected = false;
  if (!print_in_flight_.compare_exchange_strong(expected, true)) {
    std::fprintf(stderr, "bambu_cloud_host: start_print rejected — job already in flight\n");
    return -98;
  }

  std::fprintf(stderr,
               "bambu_cloud_host: start_print launching worker dev_id=%s "
               "filename=%s connection_type=%s plate=%d\n",
               params.dev_id.c_str(),
               params.filename.c_str(),
               params.connection_type.c_str(),
               params.plate_index);

  // Capture everything the worker needs by value so the lambda is self-contained.
  // agent_ is a raw pointer (void*) that outlives the process — safe to capture.
  void* agent                    = agent_;
  fn_start_print p_start_print   = p_start_print_;
  std::atomic<bool>& in_flight   = print_in_flight_;

  std::thread worker([agent, p_start_print, &in_flight,
                      pp = std::move(params)]() mutable {
    // OnUpdateStatus trampoline.  Carries the printer serial so the Python
    // side can route progress frames to the right client instead of
    // guessing by "whichever printer has a job in flight".
    std::string dev_id = pp.dev_id;
    on_update_status_fn update_fn =
        [dev_id](int stage, int code, std::string msg) {
      json event = {
        {"kind",   "OnUpdateStatus"},
        {"dev_id", dev_id},
        {"stage",  stage},
        {"code",   code},
        {"msg",    std::move(msg)},
      };
      global_event_queue().push(std::move(event));
    };

    was_cancelled_fn cancel_fn = []() -> bool { return false; };

    on_wait_fn wait_fn = [](int /*status*/, std::string /*job_info*/) -> bool {
      return true;
    };

    try {
      int rc = p_start_print(agent,
                             std::move(pp),
                             std::move(update_fn),
                             std::move(cancel_fn),
                             std::move(wait_fn));
      std::fprintf(stderr, "bambu_cloud_host: start_print worker finished rc=%d\n", rc);
    } catch (const std::exception& e) {
      std::fprintf(stderr,
                   "bambu_cloud_host: start_print worker exception: %s\n",
                   e.what());
      // Push a sentinel ERROR frame so Python doesn't stall waiting for events.
      json err_event = {
        {"kind",  "OnUpdateStatus"},
        {"stage", 7},         // SendingPrintJobStage::PrintingStageERROR
        {"code",  -99},       // internal host error
        {"msg",   std::string("host exception: ") + e.what()},
      };
      global_event_queue().push(std::move(err_event));
    } catch (...) {
      std::fprintf(stderr, "bambu_cloud_host: start_print worker unknown exception\n");
      json err_event = {
        {"kind",  "OnUpdateStatus"},
        {"stage", 7},
        {"code",  -99},
        {"msg",   "host exception: unknown"},
      };
      global_event_queue().push(std::move(err_event));
    }

    // Always clear the flag so another job can be submitted.
    in_flight.store(false);
  });

  worker.detach();
  return 0;
}

// send_message — relay a JSON command to the printer via the cloud MQTT relay.
//
// The call is synchronous and non-blocking: the plugin enqueues the message
// with its internal MQTT client and returns immediately.  No worker thread is
// needed (unlike start_print).
//
// ABI note: std::string args are BY VALUE — the plugin was compiled as C++ and
// the ABI places the SSO struct on the stack, not a raw pointer.  The `flag`
// parameter defaults to 0; OrcaSlicer always passes 0 at this call site.
//
// Source: BBLNetworkPlugin.hpp:52, BBLPrinterAgent.cpp:163-176
int PluginLoader::send_message(const std::string& dev_id,
                                const std::string& payload,
                                int qos,
                                int flag) {
  if (!agent_) {
    throw std::runtime_error("send_message: agent not bootstrapped");
  }
  int rc = p_send_message_(agent_, dev_id, payload, qos, flag);
  std::fprintf(stderr,
               "bambu_cloud_host: send_message dev_id=%s qos=%d flag=%d rc=%d\n",
               dev_id.c_str(), qos, flag, rc);
  return rc;
}

}  // namespace bambu_host
