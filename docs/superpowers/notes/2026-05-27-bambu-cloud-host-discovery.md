# Bambu Cloud Host — C ABI Discovery

## Plugin agent lifecycle

The plugin is a shared library (`.so` / `.dylib` / `.dll`) loaded via `dlopen` /
`LoadLibrary`. All function symbols are resolved with `dlsym` / `GetProcAddress`
through a thin wrapper called `BBLNetworkPlugin::get_function(name)`.

Before any per-call function (including `change_user`) is valid, the following
bootstrap sequence must be executed in order:

1. `BBLNetworkPlugin::initialize()` — loads the library and resolves all
   function pointers via `load_all_function_pointers()`.
2. `bambu_network_create_agent(log_dir)` — allocates the opaque `void* agent`
   object. Returns the handle; must be non-null before any agent function is
   called. This is the C-level equivalent of "construct the agent".
3. `bambu_network_init_log(agent)` — initialises the plugin's logging subsystem.
4. `bambu_network_set_config_dir(agent, config_dir)` — points the plugin at its
   config/cache directory.
5. `bambu_network_set_cert_file(agent, cert_folder, cert_filename)` — provides
   the TLS certificate bundle.
6. `bambu_network_set_country_code(agent, country_code)` — sets the routing
   region (e.g., `"US"`, `"CN"`).
7. `bambu_network_start(agent)` — starts internal threads / network subsystem.
8. (Optional) Register callback hooks via `set_on_user_login_fn`, etc.

Only after step 7 is `change_user` meaningful.

**Citations:**
- `BBLNetworkPlugin.cpp:676` — `m_create_agent = reinterpret_cast<func_create_agent>(get_function("bambu_network_create_agent"));`
- `BBLNetworkPlugin.cpp:678` — `m_init_log = reinterpret_cast<func_init_log>(get_function("bambu_network_init_log"));`
- `BBLNetworkPlugin.cpp:679` — `m_set_config_dir = reinterpret_cast<func_set_config_dir>(get_function("bambu_network_set_config_dir"));`
- `BBLNetworkPlugin.cpp:680` — `m_set_cert_file = reinterpret_cast<func_set_cert_file>(get_function("bambu_network_set_cert_file"));`
- `BBLNetworkPlugin.cpp:681` — `m_set_country_code = reinterpret_cast<func_set_country_code>(get_function("bambu_network_set_country_code"));`
- `GUI_App.cpp:3875-3889` — canonical bootstrap call order: `set_config_dir` → `init_log` → `set_cert_file` → `set_country_code` → `start`

---

## change_user

**dlsym name:** `bambu_network_change_user`

**C++ typedef (from `BBLNetworkPlugin.hpp:59`):**
```cpp
typedef int (*func_change_user)(void *agent, std::string user_info);
```

**C-style typedef (ABI-safe, no std::string on the boundary):**
```c
/* NOTE: The plugin was compiled as C++ and accepts std::string by value.
   The symbol takes (void*, std::string) — NOT (void*, const char*).
   Do NOT substitute const char* here; the std::string object must be
   constructed on the caller side and passed by value per the C++ ABI. */
typedef int (*change_user_fn)(void *agent, /* std::string */ const char *user_info_stdstring);
```

> **ABI warning:** The second parameter is `std::string` passed **by value**, not
> `const char*`. On the same platform with the same C++ stdlib (libstdc++ or
> libc++), passing a `std::string` by value across the `dlopen` boundary is safe
> as long as the host and plugin were compiled with the same ABI. In the host
> (`tools/bambu_cloud_host/`), construct a `std::string` and reinterpret the
> function pointer accordingly:
> ```cpp
> using change_user_fn = int(*)(void*, std::string);
> auto fn = reinterpret_cast<change_user_fn>(dlsym(handle, "bambu_network_change_user"));
> int rc = fn(agent, user_info_json_string);
> ```

**Prerequisites:**
- Must follow `bambu_network_create_agent` (agent handle must be non-null)
- Recommended: `set_config_dir`, `init_log`, `set_cert_file`, `set_country_code`,
  `start` should all have been called first (see lifecycle above)

**Call shape (from `BBLCloudServiceAgent.cpp:75-83`):**
```cpp
int BBLCloudServiceAgent::change_user(std::string user_info)
{
    auto& plugin = BBLNetworkPlugin::instance();
    auto agent = plugin.get_agent();
    auto func = plugin.get_change_user();
    if (func && agent) {
        return func(agent, user_info);
    }
    return -1;
}
```

**JSON payload shape (from `HttpServer.cpp:49-65`, `build_canonical_login_payload`):**

The `user_info` string must be a JSON-serialised object. Two supported forms:

*Form A — auth-code flow:*
```json
{
  "command": "user_login",
  "data": {
    "code": "<oauth_auth_code>",
    "state": "<oauth_state>"
  }
}
```

*Form B — direct token flow (most useful for the gateway):*
```json
{
  "command": "user_login",
  "data": {
    "token":               "<access_token>",
    "access_token":        "<access_token>",
    "refresh_token":       "<refresh_token>",
    "expires_in":          "<seconds_string>",
    "refresh_expires_in":  "<seconds_string>",
    "user_id":             "<uid>",
    "uidStr":              "<uid>",
    "user": {
      "id":      "<uid>",
      "uid":     "<uid>",
      "uidStr":  "<uid>",
      "name":    "<display_name>",
      "account": "<account_name>",
      "avatar":  "<avatar_url>"
    }
  }
}
```

**Return semantics:**
| Value | Meaning |
|-------|---------|
| `0`   | `BAMBU_NETWORK_SUCCESS` — login accepted |
| `-1`  | `BAMBU_NETWORK_ERR_INVALID_HANDLE` — agent null, or payload parsed but auth failed (missing token/user_id, state mismatch) |
| `-19` | `BAMBU_NETWORK_ERR_INVALID_RESULT` — exception thrown while processing payload |
| other negative | generic error codes from `bambu_networking.hpp` |

After a successful call, `bambu_network_is_user_login(agent)` returns `true`.

**Threading:** `change_user` is **synchronous / blocking** on the calling thread.
The OrcaSlicer fork's implementation (`OrcaCloudServiceAgent::change_user`) parses
the JSON, validates tokens, and calls internal session setters all inline before
returning. It does **not** spawn a background thread. For the BBL plugin binary
(closed-source), the same blocking behaviour is expected given the call sites
(`HttpServer.cpp:319`, `HttpServer.cpp:358`, `HttpServer.cpp:402`) all follow
`change_user` with an immediate synchronous `agent->is_user_login()` check — there
is no yield or callback between them.

---

---

## Bootstrap function signatures (all resolved from libbambu_networking.so)

All discovered from `BBLNetworkPlugin.hpp` lines 25–31.  All string parameters
are `std::string` **by value** — same ABI constraint as `change_user`.

| dlsym name | C++ typedef |
|---|---|
| `bambu_network_create_agent`   | `void* (*)(std::string log_dir)` |
| `bambu_network_init_log`       | `int (*)(void* agent)` |
| `bambu_network_set_config_dir` | `int (*)(void* agent, std::string config_dir)` |
| `bambu_network_set_cert_file`  | `int (*)(void* agent, std::string folder, std::string filename)` |
| `bambu_network_set_country_code`| `int (*)(void* agent, std::string country_code)` |
| `bambu_network_start`          | `int (*)(void* agent)` |
| `bambu_network_change_user`    | `int (*)(void* agent, std::string user_info)` |

**Bootstrap call order** (from `GUI_App.cpp:3875-3889`):
1. `create_agent(log_dir)` → `void* agent`
2. `set_config_dir(agent, config_dir)`
3. `init_log(agent)`
4. `set_cert_file(agent, cert_folder, cert_filename)`
5. `set_country_code(agent, country_code)`
6. `start(agent)`

Note: OrcaSlicer uses `RTLD_LAZY`, not `RTLD_NOW`
(`BBLNetworkPlugin.cpp:232,240`).  The library has a `.tbss` (TLS) section;
using `RTLD_NOW` causes **SIGBUS** on some systems (specifically under
OrbStack/QEMU x86_64 emulation on Apple Silicon) because glibc cannot
dynamically extend the TLS block after process startup.  `RTLD_LAZY` avoids
this because TLS relocations are deferred until first call.  On native x86_64
Linux `RTLD_NOW` would likely work, but `RTLD_LAZY` is safer and matches
OrcaSlicer's own loading strategy.

**TLS note:** The library's `PT_TLS` segment has `filesz=0` (no static TLS
data), only a `.tbss` section.  Despite `DT_FLAGS` NOT having `DF_STATIC_TLS`
set, the emulation-layer TLS handling still causes SIGBUS under QEMU x86 on
Apple Silicon.  This is an emulation limitation only; native x86_64 Linux is
unaffected.

---

## Source references

| File | Lines | Notes |
|------|-------|-------|
| `BBLNetworkPlugin.hpp` | 59 | `func_change_user` typedef |
| `BBLNetworkPlugin.hpp` | 314 | accessor `get_change_user()` |
| `BBLNetworkPlugin.hpp` | 450 | member `m_change_user` |
| `BBLNetworkPlugin.cpp` | 710 | `dlsym` call: `"bambu_network_change_user"` |
| `BBLCloudServiceAgent.cpp` | 75–83 | wrapper that calls the pointer |
| `HttpServer.cpp` | 38–65 | `build_canonical_login_payload` — canonical JSON shape |
| `HttpServer.cpp` | 318–319, 357–358, 401–402 | three call sites |
| `GUI_App.cpp` | 3875–3889 | bootstrap call order |
| `bambu_networking.hpp` | 22–50 | error code `#define`s |
