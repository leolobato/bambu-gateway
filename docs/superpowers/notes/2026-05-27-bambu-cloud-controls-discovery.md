# Cloud Controls C ABI Discovery — `send_message`

Sources examined:
- `src/slic3r/Utils/BBLNetworkPlugin.hpp` (typedef + dlsym site)
- `src/slic3r/Utils/BBLNetworkPlugin.cpp` (dlsym site: line 703)
- `src/slic3r/Utils/BBLPrinterAgent.cpp` (call site: lines 163–176)

---

## 1. `send_message` — exact C ABI

### Symbol name (from `BBLNetworkPlugin.cpp:703`)

```
"bambu_network_send_message"
```

### Typedef (from `BBLNetworkPlugin.hpp:52`)

```cpp
typedef int (*func_send_message)(
    void*       agent,      // opaque agent handle from bambu_network_create_agent
    std::string dev_id,     // printer serial number (BY VALUE — SSO struct on stack)
    std::string json_str,   // command JSON payload (BY VALUE)
    int         qos,        // MQTT QoS level (0 or 1)
    int         flag        // routing flag (0 for normal cloud relay)
);
```

### Legacy variant (from `BBLNetworkPlugin.hpp:130`)

Older plugin versions export the same symbol without the `flag` parameter:

```cpp
typedef int (*func_send_message_legacy)(
    void*       agent,
    std::string dev_id,
    std::string json_str,
    int         qos
);
```

OrcaSlicer checks `plugin.use_legacy_network()` at the call site
(`BBLPrinterAgent.cpp:169-173`) and dispatches to the legacy form when true.
For the gateway host binary, we target the current (non-legacy) ABI.

### Return value

- `0` — message accepted / queued for cloud relay
- negative — error code (same sign convention as `start_print`)

### Threading

The call is **synchronous and cheap** — it enqueues the message with the
plugin's internal MQTT client and returns immediately. No blocking I/O occurs
on the calling thread. Safe to call from the RPC dispatch loop without spawning
a worker thread.

---

## 2. Call site (from `BBLPrinterAgent.cpp:163–176`)

```cpp
int BBLPrinterAgent::send_message(std::string dev_id, std::string json_str,
                                   int qos, int flag)
{
    auto& plugin = BBLNetworkPlugin::instance();
    auto agent = plugin.get_agent();
    auto func = plugin.get_send_message();
    if (func && agent) {
        if (plugin.use_legacy_network()) {
            auto legacy_func = reinterpret_cast<func_send_message_legacy>(func);
            return legacy_func(agent, dev_id, json_str, qos);
        }
        return func(agent, dev_id, json_str, qos, flag);
    }
    return -1;
}
```

---

## 3. `send_message_to_printer` (NOT used)

`BBLNetworkPlugin.hpp:55` also defines a companion:

```cpp
typedef int (*func_send_message_to_printer)(
    void* agent, std::string dev_id, std::string json_str, int qos, int flag
);
// dlsym symbol: "bambu_network_send_message_to_printer"
```

This sends the message **directly over LAN MQTT** (bypassing the cloud relay)
and is not needed here — our gateway already handles LAN commands through its
own paho-mqtt connection.

---

## 4. Prerequisites

`send_message` requires that `bootstrap()` has completed successfully (agent
was created, config dir set, log initialised, cert file set, country code set,
`bambu_network_start` called, and `change_user` called with valid credentials).
The cloud MQTT relay must also be active, which requires `connect_server` +
`start_subscribe` to have been called beforehand (these are Phase 5 operations
already implemented).

---

## 5. Implementation guidance for Phase B.1

```cpp
// Typedef (in plugin_loader.hpp):
using fn_send_message = int(*)(void*, std::string, std::string, int, int);

// Resolver (in plugin_loader.cpp → load_from_env()):
p_send_message_ = must_resolve<fn_send_message>(dl_handle_,
                                                 "bambu_network_send_message");

// Wrapper (in plugin_loader.cpp):
int PluginLoader::send_message(const std::string& dev_id,
                                const std::string& payload,
                                int qos,
                                int flag) {
  if (!agent_) throw std::runtime_error("agent not bootstrapped");
  return p_send_message_(agent_, dev_id, payload, qos, flag);
}

// RPC handler (in methods.cpp):
json method_send_message(const json& params) {
  std::string dev_id  = params.at("dev_id").get<std::string>();
  std::string payload = params.at("payload").get<std::string>();
  int qos             = params.value("qos",  0);
  int flag            = params.value("flag", 0);
  return {{"rc", loader().send_message(dev_id, payload, qos, flag)}};
}
```

The `flag` parameter defaults to `0` for normal cloud relay. OrcaSlicer does
not pass a non-zero flag in any examined call site.
