# Bambu Cloud Status — C ABI Discovery

Discovered from OrcaSlicer-bambulab sources. All typedefs from
`BBLNetworkPlugin.hpp`; dlsym names from `BBLNetworkPlugin.cpp`;
call shapes from `BBLCloudServiceAgent.cpp`.

All string parameters are `std::string` **by value** (same ABI constraint as
`change_user` — see Phase 2 discovery notes). The plugin was compiled as C++17
and expects `std::string` SSO structs on the stack, NOT raw `const char*` pointers.

---

## connect_server

**dlsym name:** `bambu_network_connect_server`
**BBLNetworkPlugin.hpp line:** 44
**C++ typedef (verbatim):**
```cpp
typedef int (*func_connect_server)(void *agent);
```
**Host typedef:**
```cpp
using fn_connect_server = int(*)(void*);
```
**Sync/async:** Async — initiates the MQTT broker handshake and returns
immediately (does NOT block until connected). The `OnServerConnectedFn`
callback (registered via `bambu_network_set_on_server_connected_fn`) fires
when the handshake completes. This is confirmed by:
- The PJarczak bridge (`PJarczakBambuNetworkForwarderExports.cpp:352-358`),
  which calls `RpcClient::instance().invoke_int("net.connect_server", ...)` and
  updates `a->server_connected = ret == 0` locally — not from any callback.
- The existence of `bambu_network_is_server_connected(agent)` as a separate
  polling function, confirming that connected-state is tracked asynchronously.

**Return semantics:** 0 = handshake initiated; negative = error.
**Call shape (BBLCloudServiceAgent.cpp:257-266):**
```cpp
int BBLCloudServiceAgent::connect_server()
{
    auto& plugin = BBLNetworkPlugin::instance();
    auto agent = plugin.get_agent();
    auto func = plugin.get_connect_server();
    if (func && agent) { return func(agent); }
    return -1;
}
```

---

## start_subscribe

**dlsym name:** `bambu_network_start_subscribe`
**BBLNetworkPlugin.hpp line:** 47
**C++ typedef (verbatim):**
```cpp
typedef int (*func_start_subscribe)(void *agent, std::string module);
```
**Host typedef:**
```cpp
using fn_start_subscribe = int(*)(void*, std::string);
```
**Sync/async:** Returns immediately; subscription handshake completes
asynchronously via the MQTT layer.

**Parameter:** `module` — a string identifying the subscription category
(e.g. `"printer"` or `"studio"`). From PJarczak bridge:
`RpcClient::instance().invoke_int("net.start_subscribe", {{"module", module}})`.

**Return semantics:** 0 = subscription initiated; negative = error.
**Call shape (BBLCloudServiceAgent.cpp:290-299):**
```cpp
int BBLCloudServiceAgent::start_subscribe(std::string module)
{
    auto& plugin = BBLNetworkPlugin::instance();
    auto agent = plugin.get_agent();
    auto func = plugin.get_start_subscribe();
    if (func && agent) { return func(agent, module); }
    return -1;
}
```

---

## add_subscribe

**dlsym name:** `bambu_network_add_subscribe`
**BBLNetworkPlugin.hpp line:** 49
**C++ typedef (verbatim):**
```cpp
typedef int (*func_add_subscribe)(void *agent, std::vector<std::string> dev_list);
```
**Host typedef:**
```cpp
using fn_add_subscribe = int(*)(void*, std::vector<std::string>);
```
**Note on the plan template:** The plan said "the real type might be different".
It is confirmed: `std::vector<std::string>` by value. Not a reference.

**Sync/async:** Returns immediately after registering the device list with the
MQTT subscription layer.

**Return semantics:** 0 = ok; negative = error.
**Call shape (BBLCloudServiceAgent.cpp:312-321):**
```cpp
int BBLCloudServiceAgent::add_subscribe(std::vector<std::string> dev_list)
{
    auto& plugin = BBLNetworkPlugin::instance();
    auto agent = plugin.get_agent();
    auto func = plugin.get_add_subscribe();
    if (func && agent) { return func(agent, dev_list); }
    return -1;
}
```

---

## set_on_message_fn

**dlsym name:** `bambu_network_set_on_message_fn`
**BBLNetworkPlugin.hpp line:** 39
**C++ typedef (verbatim):**
```cpp
typedef int (*func_set_on_message_fn)(void *agent, OnMessageFn fn);
```
**`OnMessageFn` definition (verbatim, `bambu_networking.hpp:120`):**
```cpp
typedef std::function<void(std::string dev_id, std::string msg)> OnMessageFn;
```

**CRITICAL note:** This is `std::function`, NOT a raw function pointer.
The plugin function signature takes `OnMessageFn fn` **by value** — a full
`std::function` object. This allows passing a C++ lambda/closure across the
dlopen boundary, but requires both the host and plugin to be compiled with the
same C++ stdlib ABI (`std::function` layout is ABI-specific).

**Parameter count:** 2 parameters (`dev_id`, `msg`). NOT 3.
The plan template said "possibly int chan" — the actual signature has NO `chan`
parameter. Adjust the trampoline accordingly.

**Host typedef:**
```cpp
using on_message_fn = std::function<void(std::string dev_id, std::string msg)>;
using fn_set_on_message_fn = int(*)(void* agent, on_message_fn fn);
```

**Threading:** The callback is invoked from the plugin's internal MQTT
network thread. It MAY be called concurrently if the plugin multiplexes
multiple broker connections across threads (spec §5.4 says "yes — concurrent
calls are possible"). Our trampoline's `EventQueue::push` takes a mutex, so
it is reentrant and safe.

**Registration call shape (from PJarczakBambuNetworkForwarderExports.cpp:345):**
```cpp
// fn is stored by std::move, so the closure's state is transferred:
PJBRIDGE_EXPORT int bambu_network_set_on_message_fn(void* agent, OnMessageFn fn) {
    auto* a = require_agent(agent);
    if (!a) return invalid_handle();
    a->on_message = std::move(fn);  // stored in agent for later invocation
    return register_remote_callback("net.set_on_message_fn", a);
}
```

**OrcaSlicer host registration shape (NetworkAgent.cpp:298-308):**
```cpp
int NetworkAgent::set_on_message_fn(OnMessageFn fn)
{
    std::lock_guard<std::mutex> lock(m_agent_mutex);
    m_printer_callbacks.on_message_fn = fn;
    if (printer_agent) return printer_agent->set_on_message_fn(fn);
    return -1;
}
```

---

## Source references

| File | Line | Notes |
|------|------|-------|
| `bambu_networking.hpp` | 120 | `OnMessageFn` typedef |
| `BBLNetworkPlugin.hpp` | 39 | `func_set_on_message_fn` typedef |
| `BBLNetworkPlugin.hpp` | 44 | `func_connect_server` typedef |
| `BBLNetworkPlugin.hpp` | 47 | `func_start_subscribe` typedef |
| `BBLNetworkPlugin.hpp` | 49 | `func_add_subscribe` typedef |
| `BBLNetworkPlugin.cpp` | 690 | dlsym `"bambu_network_set_on_message_fn"` |
| `BBLNetworkPlugin.cpp` | 695 | dlsym `"bambu_network_connect_server"` |
| `BBLNetworkPlugin.cpp` | 698 | dlsym `"bambu_network_start_subscribe"` |
| `BBLNetworkPlugin.cpp` | 700 | dlsym `"bambu_network_add_subscribe"` |
| `BBLCloudServiceAgent.cpp` | 257–266 | `connect_server` wrapper |
| `BBLCloudServiceAgent.cpp` | 290–299 | `start_subscribe` wrapper |
| `BBLCloudServiceAgent.cpp` | 312–321 | `add_subscribe` wrapper |
| `PJarczakBambuNetworkForwarderExports.cpp` | 345 | `set_on_message_fn` export |
| `PJarczakBambuNetworkForwarderExports.cpp` | 352–373 | `connect_server`, `start_subscribe`, `add_subscribe` exports |
| `NetworkAgent.cpp` | 298–308 | `set_on_message_fn` host-side registration |
