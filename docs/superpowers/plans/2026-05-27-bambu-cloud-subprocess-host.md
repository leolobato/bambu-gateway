# Bambu Cloud Subprocess Host — Implementation Plan (Phases 2 + 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** End-to-end working pipeline from Python FastAPI → JSONL RPC → C++ subprocess → `dlopen`'d Bambu plugin, with **one real plugin method (`change_user`) round-tripping** and the framework ready to accept additional methods in follow-up plans.

**Architecture:**

```
┌──────────────────────────────────────────────┐
│  FastAPI (Python)                            │
│    ┌──────────────────────────────────────┐  │
│    │ PluginHost (asyncio subprocess mgr)  │  │
│    │   - spawn host binary                │  │
│    │   - JSONL stdin/stdout RPC client    │  │
│    │   - request/response future tracking │  │
│    │   - supervisor: restart on crash     │  │
│    └──────────────────────────────────────┘  │
│    ┌──────────────────────────────────────┐  │
│    │ EventPump (asyncio task)             │  │
│    │   - polls bridge.poll_events 80/250ms│  │
│    └──────────────────────────────────────┘  │
└────────────────────┬─────────────────────────┘
                     │ stdin / stdout
                     ▼
┌──────────────────────────────────────────────┐
│  bambu-cloud-host  (C++ binary, ~one file)   │
│    - JSONL framing                           │
│    - method dispatch table                   │
│    - dlopen libbambu_networking.so           │
│    - C trampolines for set_on_*_fn callbacks │
│    - in-memory event queue (drained by       │
│      bridge.poll_events RPC)                 │
└────────────────────┬─────────────────────────┘
                     │ dlopen / dlsym
                     ▼
┌──────────────────────────────────────────────┐
│  libbambu_networking.so  (closed source)     │
└──────────────────────────────────────────────┘
```

**Tech Stack:** Python 3.13+ (asyncio, subprocess), C++17 (host binary), CMake (build), nlohmann/json (C++ JSON), pytest with `asyncio_mode=auto`, Docker multi-stage build.

**Spec:** `docs/superpowers/specs/2026-05-26-bambu-cloud-plugin-design.md`

**Phase 0 + 1 outputs in scope:** `docs/superpowers/notes/2026-05-26-bambu-cloud-discovery.md`, `app/cloud/plugin_downloader.py`, the `.so` files end up in `${BAMBU_CLOUD_PLUGIN_DIR}/active/` at startup.

---

## File Structure

**New files (C++ subprocess host):**

| File | Purpose |
|---|---|
| `tools/bambu_cloud_host/CMakeLists.txt` | Build script — pulls in nlohmann/json, links libdl, outputs single binary |
| `tools/bambu_cloud_host/main.cpp` | Entry point: read JSONL from stdin, dispatch, write JSONL to stdout |
| `tools/bambu_cloud_host/rpc.hpp` | Frame parsing + write (header-only) |
| `tools/bambu_cloud_host/plugin_loader.hpp` | dlopen + dlsym helpers, function pointer table |
| `tools/bambu_cloud_host/plugin_loader.cpp` | Implementation; resolves the symbols we need from libbambu_networking.so |
| `tools/bambu_cloud_host/event_queue.hpp` | Thread-safe deque for plugin callbacks; drained by `bridge.poll_events` RPC |
| `tools/bambu_cloud_host/methods.cpp` | Method dispatch table; one handler per supported RPC |
| `tools/bambu_cloud_host/README.md` | How to build, what env vars it expects, the JSONL contract |

**New files (Python orchestration):**

| File | Purpose |
|---|---|
| `app/cloud/plugin_host.py` | `PluginHost` class: subprocess manager + RPC client, request/response futures |
| `app/cloud/event_pump.py` | `EventPump`: asyncio task that polls `bridge.poll_events` and dispatches handlers |
| `tests/test_cloud_plugin_host.py` | Python tests using a **Python fake host** (no real C++ needed) |
| `tests/cloud_fake_host.py` | Standalone Python script that mimics the C++ host's JSONL contract |
| `docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md` | Phase A output: C ABI signatures we depend on |

**Modified files:**

| File | Change |
|---|---|
| `Dockerfile` | Add a C++ build stage that compiles the host binary, COPY it into the runtime image |
| `app/config.py` | Add `bambu_cloud_host_binary` setting (path to the compiled binary) |
| `app/main.py` | Lifespan starts the `PluginHost` after the downloader succeeds (cloud mode only); stops it on shutdown |

---

## Phase A — Discovery: minimum C ABI we need for `change_user`

`change_user` is the smallest end-to-end test of the entire pipeline. We don't need to discover all 15 methods upfront — just enough to make `change_user` work.

### Task A.1: Identify the C ABI of `change_user`

**Files:**
- Create/modify: `docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md`

- [ ] **Step 1: Read the BBL plugin loader header**

Read `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLNetworkPlugin.hpp` end-to-end. Find the function-pointer typedef and the dlsym name for `change_user`.

Then `grep -rn "change_user" /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLNetworkPlugin.cpp` to find the actual `dlsym` call.

- [ ] **Step 2: Read `BBLCloudServiceAgent::change_user` to confirm the call shape**

Read `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLCloudServiceAgent.cpp` and find the `change_user` method. Note:
- What does it pass to the plugin? (Just the JSON string? Anything else?)
- What does it expect back? (Return value type and semantics)
- Does it require any prior plugin state (e.g., agent must be created first)?

- [ ] **Step 3: Document findings**

Write/append to `docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md`:

```markdown
# Bambu Cloud Host — C ABI Discovery

## Plugin agent lifecycle

(How is the plugin's internal "agent" object created? Does dlopen need to be
followed by a `create_agent`-style call before other methods are valid?)

**Citations:**
- `BBLNetworkPlugin.cpp:<line>` — `<verbatim>`
- ...

## change_user

**dlsym name:** `<exact symbol>`

**C signature:**
```c
<typedef the function pointer here as it would appear in a header>
```

**Prerequisites:** (e.g., must follow `create_agent` and `set_country_code`)

**Call shape (from BBLCloudServiceAgent.cpp:<line>):**
```cpp
<verbatim code that constructs args and calls the pointer>
```

**Return semantics:** `0 = success, non-zero = error code <list known>`

**Threading:** does this block? does it spawn its own thread?
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md
git commit -m "Cloud host: discover change_user C ABI"
```

### Task A.2: Identify the minimum bootstrap sequence

`change_user` likely cannot be called against a freshly-dlopen'd plugin without first creating an "agent" object and possibly setting the country code. We need to know the exact bootstrap sequence the OrcaSlicer-bambulab fork performs between `dlopen` and the first `change_user` call.

**Files:**
- Modify: `docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md`

- [ ] **Step 1: Read `BBLCloudServiceAgent::start()` (or equivalent)**

Find the initialization in `BBLCloudServiceAgent.cpp`. Look for a `start()`, `init()`, or constructor that runs the create_agent → set_country_code → set_callbacks sequence.

Grep helpfully: `grep -n "create_agent\|init_agent\|set_country_code\|start_subscribe" /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLCloudServiceAgent.cpp | head -30`

- [ ] **Step 2: Document the sequence**

Append to the discovery notes:

```markdown
## Bootstrap sequence

After `dlopen("libbambu_networking.so")`:

1. `<call 1>` — purpose, C signature
2. `<call 2>` — purpose, C signature
3. ...

For each method in the sequence, list:
- dlsym name
- C signature
- args populated from where (env var? hardcoded? config?)
- whether the return value matters

The minimum sequence we need to call `change_user`:

1. ...
N. `change_user(canonical_login_json)`
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md
git commit -m "Cloud host: discover bootstrap sequence for change_user"
```

---

## Phase B — Build infrastructure

We add a C++ build stage to the existing Dockerfile so the host binary ships inside the runtime image. The build uses CMake (industry standard for C++ on Linux) and nlohmann/json (header-only, vendored or fetched).

### Task B.1: Vendor `nlohmann/json.hpp`

**Files:**
- Create: `tools/bambu_cloud_host/third_party/nlohmann/json.hpp`

- [ ] **Step 1: Download the single-header release into the repo**

```bash
mkdir -p tools/bambu_cloud_host/third_party/nlohmann
curl -L -o tools/bambu_cloud_host/third_party/nlohmann/json.hpp \
  https://github.com/nlohmann/json/releases/download/v3.11.3/json.hpp
```

Verify file size is roughly 900KB (the full single-header amalgamation):

```bash
ls -la tools/bambu_cloud_host/third_party/nlohmann/json.hpp
```

- [ ] **Step 2: Commit**

```bash
git add tools/bambu_cloud_host/third_party/nlohmann/json.hpp
git commit -m "Cloud host: vendor nlohmann/json v3.11.3 (single header)"
```

### Task B.2: Create `CMakeLists.txt`

**Files:**
- Create: `tools/bambu_cloud_host/CMakeLists.txt`

- [ ] **Step 1: Write minimal CMakeLists.txt**

```cmake
cmake_minimum_required(VERSION 3.20)
project(bambu_cloud_host CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Static, position-independent, hardened — we ship one binary inside Docker.
add_compile_options(
  -Wall -Wextra -Wpedantic -Wshadow
  -fno-strict-aliasing
  -O2 -g
)

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/third_party)

add_executable(bambu_cloud_host
  main.cpp
  plugin_loader.cpp
  methods.cpp
)

# libdl for dlopen, pthread for the event queue lock
target_link_libraries(bambu_cloud_host PRIVATE dl pthread)

install(TARGETS bambu_cloud_host RUNTIME DESTINATION bin)
```

- [ ] **Step 2: Commit**

```bash
git add tools/bambu_cloud_host/CMakeLists.txt
git commit -m "Cloud host: minimal CMake build script"
```

### Task B.3: Add C++ build stage to the Dockerfile

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1: Read the existing Dockerfile**

The existing Dockerfile is a single-stage Python image. We need to add a C++ build stage that produces the host binary, then COPY it into the final image. Read the current Dockerfile end-to-end before editing.

- [ ] **Step 2: Add a builder stage at the top**

```dockerfile
# ------------------------------------------------------------------
# Stage 1: build the Bambu cloud subprocess host (C++ binary).
# ------------------------------------------------------------------
FROM debian:bookworm-slim AS cloud_host_builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential cmake ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY tools/bambu_cloud_host /src
RUN cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build /build --parallel
```

- [ ] **Step 3: COPY the binary into the runtime stage**

In whatever line(s) follow the existing `FROM python:...` directive, add:

```dockerfile
COPY --from=cloud_host_builder /build/bambu_cloud_host /usr/local/bin/bambu_cloud_host
```

If the existing Dockerfile already has a runtime stage labeled differently, integrate accordingly. Don't break existing behavior.

- [ ] **Step 4: Test the build locally**

```bash
docker build -t bambu-gateway:cloud-host-test . 2>&1 | tail -30
```

Expected: builds successfully, no errors. The binary won't do anything functional yet (the source files don't exist), so initially this command will fail at the `cmake --build` step. **That's OK** — confirm the build failure is "no input files" or "cannot find main.cpp" and move on. The Dockerfile change is correct; Tasks C.1+ will provide the source files.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile
git commit -m "Cloud host: add C++ build stage to Dockerfile"
```

---

## Phase C — Bare-bones host skeleton (echo only)

Get a C++ binary that reads JSONL lines from stdin, parses them, replies with `{"id":N,"result":{"echo":...}}`. No plugin yet — just RPC framing.

### Task C.1: Write `rpc.hpp` — JSONL frame helpers

**Files:**
- Create: `tools/bambu_cloud_host/rpc.hpp`

- [ ] **Step 1: Write the header**

```cpp
// SPDX-License-Identifier: MIT
// Single-header helper for newline-delimited JSON RPC over stdin/stdout.
#pragma once

#include <cstdio>
#include <optional>
#include <string>

#include "third_party/nlohmann/json.hpp"

namespace bambu_host {

using json = nlohmann::json;

// Read one '\n'-terminated line from stdin. Returns std::nullopt on EOF.
inline std::optional<std::string> read_line(FILE* in) {
  std::string line;
  for (;;) {
    int c = std::fgetc(in);
    if (c == EOF) {
      return line.empty() ? std::nullopt : std::optional<std::string>{line};
    }
    if (c == '\n') return line;
    line.push_back(static_cast<char>(c));
  }
}

// Write one JSON object as a single line + flush.
inline void write_frame(FILE* out, const json& payload) {
  std::string s = payload.dump();
  std::fwrite(s.data(), 1, s.size(), out);
  std::fputc('\n', out);
  std::fflush(out);
}

// Build an error response with the matching request id.
inline json error_response(long long id, const std::string& message,
                           int code = -1) {
  return {{"id", id}, {"error", {{"code", code}, {"message", message}}}};
}

inline json ok_response(long long id, json result) {
  return {{"id", id}, {"result", std::move(result)}};
}

}  // namespace bambu_host
```

- [ ] **Step 2: Commit**

```bash
git add tools/bambu_cloud_host/rpc.hpp
git commit -m "Cloud host: JSONL frame helpers (rpc.hpp)"
```

### Task C.2: Write `main.cpp` — skeleton dispatcher

**Files:**
- Create: `tools/bambu_cloud_host/main.cpp`
- Create: `tools/bambu_cloud_host/methods.cpp`

- [ ] **Step 1: Write methods.cpp with one method (`echo`)**

```cpp
// SPDX-License-Identifier: MIT
#include "rpc.hpp"

namespace bambu_host {

// Forward declaration; main.cpp calls this.
json dispatch_method(const std::string& method, const json& params);

namespace {

// echo just returns its params back. Useful as a liveness check.
json method_echo(const json& params) {
  return params;
}

}  // namespace

json dispatch_method(const std::string& method, const json& params) {
  if (method == "echo") return method_echo(params);
  throw std::runtime_error("unknown method: " + method);
}

}  // namespace bambu_host
```

- [ ] **Step 2: Write main.cpp**

```cpp
// SPDX-License-Identifier: MIT
//
// bambu_cloud_host — JSONL RPC bridge to libbambu_networking.so.
//
// Reads one JSON object per line from stdin: {"id":N, "method":"...", "params":{...}}.
// Replies with {"id":N, "result":...} or {"id":N, "error":{...}} on stdout.
// Logs go to stderr (the host process never writes anything but framed
// responses to stdout, so the parent's RPC parser stays clean).

#include <cstdio>
#include <iostream>
#include <string>

#include "rpc.hpp"

namespace bambu_host {
json dispatch_method(const std::string& method, const json& params);
}

int main() {
  using namespace bambu_host;
  // Unbuffer stdout so the parent sees responses immediately.
  std::setvbuf(stdout, nullptr, _IONBF, 0);

  std::fprintf(stderr, "bambu_cloud_host: started\n");

  while (auto line = read_line(stdin)) {
    long long id = -1;
    try {
      json req = json::parse(*line);
      id = req.value("id", -1LL);
      std::string method = req.at("method").get<std::string>();
      json params = req.value("params", json::object());
      json result = dispatch_method(method, params);
      write_frame(stdout, ok_response(id, std::move(result)));
    } catch (const std::exception& exc) {
      write_frame(stdout, error_response(id, exc.what()));
    }
  }
  std::fprintf(stderr, "bambu_cloud_host: stdin closed, exiting\n");
  return 0;
}
```

- [ ] **Step 3: Add `plugin_loader.cpp` placeholder**

CMakeLists.txt lists three sources. Provide a placeholder so the build succeeds before we have real loader logic:

```cpp
// SPDX-License-Identifier: MIT
// Placeholder — real implementation lands in Task D.1.
```

Save as `tools/bambu_cloud_host/plugin_loader.cpp`.

- [ ] **Step 4: Build inside Docker to confirm it compiles**

```bash
docker build -t bambu-gateway:cloud-host-test . 2>&1 | tail -20
```

Expected: builds successfully. If you can build directly on Linux without Docker, run `cmake -S tools/bambu_cloud_host -B /tmp/build && cmake --build /tmp/build` instead.

- [ ] **Step 5: Smoke test the echo method**

Spawn the binary and confirm it round-trips:

```bash
docker run --rm -i bambu-gateway:cloud-host-test \
  /usr/local/bin/bambu_cloud_host \
  <<< '{"id":1,"method":"echo","params":{"hello":"world"}}'
```

Expected stdout: `{"id":1,"result":{"hello":"world"}}`

- [ ] **Step 6: Commit**

```bash
git add tools/bambu_cloud_host/main.cpp tools/bambu_cloud_host/methods.cpp \
        tools/bambu_cloud_host/plugin_loader.cpp
git commit -m "Cloud host: skeleton with echo method"
```

---

## Phase D — Plugin loader + `change_user`

### Task D.1: Implement `plugin_loader.hpp` + `plugin_loader.cpp`

Fill in real loader logic. The discovery from Task A.1 + A.2 tells us exactly which symbols to resolve and how to call them.

**Files:**
- Create: `tools/bambu_cloud_host/plugin_loader.hpp`
- Modify: `tools/bambu_cloud_host/plugin_loader.cpp`

- [ ] **Step 1: Write the header**

```cpp
// SPDX-License-Identifier: MIT
#pragma once

#include <string>

namespace bambu_host {

// Loads libbambu_networking.so and resolves the function pointers we need.
// Throws std::runtime_error on dlopen / dlsym failure.
class PluginLoader {
 public:
  // Path comes from the PJARCZAK_BAMBU_NETWORK_SO env var (matching the
  // env-var convention OrcaSlicer-bambulab's bridge host uses, so we can
  // keep one config style across both tools).
  void load_from_env();

  // Returns 0 on success, non-zero error code on failure. The plugin
  // performs the bootstrap sequence captured in
  // docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md §"Bootstrap sequence"
  // before the first user-facing method is called.
  int bootstrap();

  // Implements the `change_user` RPC method.
  int change_user(const std::string& canonical_login_json);

 private:
  void* dl_handle_ = nullptr;
  void* agent_handle_ = nullptr;  // opaque pointer the plugin returns from create_agent
  // Function-pointer typedefs — see Task A.1 / A.2 for exact signatures.
  // Replace these with whatever the discovery notes say.
  // Example shape:
  // using create_agent_fn = void*(*)();
  // using set_country_code_fn = int(*)(void*, const char*);
  // using change_user_fn = int(*)(void*, const char*);
  // create_agent_fn p_create_agent_ = nullptr;
  // set_country_code_fn p_set_country_code_ = nullptr;
  // change_user_fn p_change_user_ = nullptr;
};

}  // namespace bambu_host
```

> **Critical:** the function-pointer typedefs in the example above are placeholders — you MUST replace them with the real signatures from `docs/superpowers/notes/2026-05-27-bambu-cloud-host-discovery.md` Task A.1 / A.2 before writing the implementation. If the discovery notes don't specify a method we need, report BLOCKED.

- [ ] **Step 2: Write the implementation**

```cpp
// SPDX-License-Identifier: MIT
#include "plugin_loader.hpp"

#include <dlfcn.h>
#include <cstdlib>
#include <stdexcept>

namespace bambu_host {

namespace {
template <typename FnT>
FnT must_resolve(void* h, const char* name) {
  void* p = dlsym(h, name);
  if (!p) throw std::runtime_error(std::string("dlsym failed: ") + name);
  return reinterpret_cast<FnT>(p);
}
}  // namespace

void PluginLoader::load_from_env() {
  const char* so_path = std::getenv("PJARCZAK_BAMBU_NETWORK_SO");
  if (!so_path) {
    throw std::runtime_error(
      "PJARCZAK_BAMBU_NETWORK_SO env var is not set");
  }
  dl_handle_ = dlopen(so_path, RTLD_NOW | RTLD_LOCAL);
  if (!dl_handle_) {
    throw std::runtime_error(std::string("dlopen failed: ") + dlerror());
  }
  // Resolve each function pointer using the real names from discovery.
  // p_create_agent_ = must_resolve<create_agent_fn>(
  //   dl_handle_, "<real symbol from Task A.2>");
  // p_set_country_code_ = must_resolve<set_country_code_fn>(
  //   dl_handle_, "<real symbol from Task A.2>");
  // p_change_user_ = must_resolve<change_user_fn>(
  //   dl_handle_, "<real symbol from Task A.1>");
}

int PluginLoader::bootstrap() {
  // Replace with the real bootstrap sequence from Task A.2.
  // agent_handle_ = p_create_agent_();
  // if (!agent_handle_) return -1;
  // const char* region = std::getenv("BAMBU_CLOUD_REGION");
  // if (!region) region = "US";
  // return p_set_country_code_(agent_handle_, region);
  return 0;
}

int PluginLoader::change_user(const std::string& canonical_login_json) {
  if (!agent_handle_) return -1;
  // return p_change_user_(agent_handle_, canonical_login_json.c_str());
  return 0;
}

}  // namespace bambu_host
```

> Same critical note: the commented-out lines are placeholders. The implementer doing this task MUST fill them in from the discovery notes. If the discovery is missing a symbol, report BLOCKED and we'll do a targeted discovery pass.

- [ ] **Step 3: Add `init_plugin` + `change_user` methods to `methods.cpp`**

```cpp
// Replace the contents of methods.cpp with:

#include "rpc.hpp"
#include "plugin_loader.hpp"

namespace bambu_host {

namespace {

// One process-global loader. The host is single-threaded for RPC dispatch
// (events come in async via the callback queue), so no locking needed here.
PluginLoader& loader() {
  static PluginLoader g;
  return g;
}

json method_echo(const json& params) {
  return params;
}

json method_init_plugin(const json& /*params*/) {
  loader().load_from_env();
  int rc = loader().bootstrap();
  return {{"bootstrap_rc", rc}};
}

json method_change_user(const json& params) {
  std::string payload = params.at("canonical_login").get<std::string>();
  int rc = loader().change_user(payload);
  return {{"rc", rc}};
}

}  // namespace

json dispatch_method(const std::string& method, const json& params) {
  if (method == "echo") return method_echo(params);
  if (method == "init_plugin") return method_init_plugin(params);
  if (method == "change_user") return method_change_user(params);
  throw std::runtime_error("unknown method: " + method);
}

}  // namespace bambu_host
```

- [ ] **Step 4: Build and confirm it compiles**

```bash
docker build -t bambu-gateway:cloud-host-test . 2>&1 | tail -20
```

Expected: builds successfully.

- [ ] **Step 5: Commit**

```bash
git add tools/bambu_cloud_host/plugin_loader.hpp \
        tools/bambu_cloud_host/plugin_loader.cpp \
        tools/bambu_cloud_host/methods.cpp
git commit -m "Cloud host: dlopen plugin + change_user method"
```

---

## Phase E — Python orchestration (`PluginHost` + tests)

Now the Python side. The `PluginHost` class manages the subprocess, sends JSONL frames, awaits responses keyed by request id. Tests use a Python fake host so we can iterate without rebuilding the container.

### Task E.1: Write the Python fake host

**Files:**
- Create: `tests/cloud_fake_host.py`

This is a Python script that mimics the C++ host's JSONL contract. Tests can spawn it with the same API as the real host.

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Fake bambu_cloud_host that speaks the same JSONL RPC as the C++ binary.

Used by tests so we can exercise PluginHost without needing the real .so or
a built C++ binary. Returns canned successes for `echo`, `init_plugin`, and
`change_user`. Each request can be observed via the FAKE_HOST_RECORD_FILE
env var (a JSONL file the fake writes one line per request to).
"""
from __future__ import annotations

import json
import os
import sys


def _record(request: dict) -> None:
    path = os.environ.get("FAKE_HOST_RECORD_FILE")
    if not path:
        return
    with open(path, "a") as fh:
        fh.write(json.dumps(request) + "\n")


def _dispatch(method: str, params: dict) -> dict:
    if method == "echo":
        return params
    if method == "init_plugin":
        return {"bootstrap_rc": 0}
    if method == "change_user":
        if "canonical_login" not in params:
            raise ValueError("change_user requires canonical_login")
        return {"rc": 0}
    raise ValueError(f"unknown method: {method}")


def main() -> int:
    print("fake bambu_cloud_host: started", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            _record(req)
            rid = req.get("id", -1)
            method = req["method"]
            params = req.get("params", {})
            result = _dispatch(method, params)
            print(json.dumps({"id": rid, "result": result}), flush=True)
        except Exception as exc:
            print(
                json.dumps(
                    {"id": rid if "rid" in locals() else -1,
                     "error": {"code": -1, "message": str(exc)}}
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x tests/cloud_fake_host.py
```

- [ ] **Step 3: Commit**

```bash
git add tests/cloud_fake_host.py
git commit -m "Cloud host: Python fake host for testing"
```

### Task E.2: Implement `PluginHost`

**Files:**
- Create: `app/cloud/plugin_host.py`
- Create: `tests/test_cloud_plugin_host.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cloud_plugin_host.py`:

```python
"""Tests for the Python PluginHost using the Python fake host."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from app.cloud.plugin_host import PluginHost, PluginHostError


FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


@pytest.fixture
def fake_host_cmd():
    return [sys.executable, str(FAKE_HOST)]


async def test_plugin_host_round_trips_echo(fake_host_cmd):
    async with PluginHost(cmd=fake_host_cmd) as host:
        result = await host.call("echo", {"x": 1, "y": "two"})
        assert result == {"x": 1, "y": "two"}


async def test_plugin_host_supports_concurrent_calls(fake_host_cmd):
    async with PluginHost(cmd=fake_host_cmd) as host:
        results = await asyncio.gather(
            host.call("echo", {"i": 0}),
            host.call("echo", {"i": 1}),
            host.call("echo", {"i": 2}),
        )
    assert sorted(r["i"] for r in results) == [0, 1, 2]


async def test_plugin_host_surfaces_rpc_errors(fake_host_cmd):
    async with PluginHost(cmd=fake_host_cmd) as host:
        with pytest.raises(PluginHostError) as exc_info:
            await host.call("does_not_exist", {})
        assert "unknown method" in str(exc_info.value)


async def test_plugin_host_init_and_change_user(fake_host_cmd, tmp_path):
    record_file = tmp_path / "requests.jsonl"
    async with PluginHost(
        cmd=fake_host_cmd,
        env={"FAKE_HOST_RECORD_FILE": str(record_file)},
    ) as host:
        bootstrap = await host.call("init_plugin", {})
        assert bootstrap == {"bootstrap_rc": 0}
        result = await host.call(
            "change_user",
            {"canonical_login": json.dumps(
                {"command": "user_login",
                 "data": {"token": "...", "user_id": "42"}}
            )},
        )
        assert result == {"rc": 0}

    # Confirm the fake host saw both requests in order.
    seen = [json.loads(line) for line in record_file.read_text().splitlines()]
    assert [r["method"] for r in seen] == ["init_plugin", "change_user"]


async def test_plugin_host_raises_when_subprocess_dies(fake_host_cmd):
    async with PluginHost(cmd=fake_host_cmd) as host:
        await host.call("echo", {})  # warm up
        # Kill the subprocess from underneath; subsequent calls must fail.
        host._proc.kill()
        await host._proc.wait()
        with pytest.raises(PluginHostError):
            await host.call("echo", {})
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
.venv/bin/pytest tests/test_cloud_plugin_host.py -v
```

Expected: FAIL — `PluginHost` not implemented.

- [ ] **Step 3: Implement `PluginHost`**

Create `app/cloud/plugin_host.py`:

```python
"""Async subprocess manager + JSONL RPC client for the C++ host binary."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, Mapping

logger = logging.getLogger("bambu.cloud.host")


class PluginHostError(RuntimeError):
    """Raised when the host subprocess returns an error or dies."""


class PluginHost:
    """Manages a child process speaking JSONL RPC over stdin/stdout.

    Usage::

        async with PluginHost(cmd=["/usr/local/bin/bambu_cloud_host"]) as host:
            await host.call("init_plugin", {})
            await host.call("change_user", {"canonical_login": "..."})

    Concurrent ``call()``s are safe; responses are routed to the right awaiter
    by request id.
    """

    def __init__(
        self,
        *,
        cmd: list[str],
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._cmd = list(cmd)
        self._env = dict(env) if env else None
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._closed = False
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "PluginHost":
        await self.start()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.stop()

    async def start(self) -> None:
        import os

        full_env = os.environ.copy()
        if self._env:
            full_env.update(self._env)
        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(), name="bambu-cloud-host-reader"
        )
        asyncio.create_task(
            self._drain_stderr(), name="bambu-cloud-host-stderr"
        )

    async def stop(self) -> None:
        self._closed = True
        if self._proc and self._proc.returncode is None:
            with suppress(ProcessLookupError):
                if self._proc.stdin and not self._proc.stdin.is_closing():
                    self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("host did not exit; killing")
                self._proc.kill()
                await self._proc.wait()
        if self._reader_task:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        # Fail any still-pending requests.
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(
                    PluginHostError("host shutting down")
                )
        self._pending.clear()

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        if self._closed or self._proc is None:
            raise PluginHostError("host not running")
        if self._proc.returncode is not None:
            raise PluginHostError(
                f"host died (exit code {self._proc.returncode})"
            )

        async with self._lock:
            self._next_id += 1
            rid = self._next_id

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut

        request = {"id": rid, "method": method, "params": params}
        line = (json.dumps(request) + "\n").encode("utf-8")
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            self._pending.pop(rid, None)
            raise PluginHostError(f"host stdin closed: {exc}") from exc

        return await fut

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            async for raw in self._proc.stdout:
                if not raw:
                    break
                try:
                    msg = json.loads(raw.decode("utf-8").rstrip("\n"))
                except json.JSONDecodeError as exc:
                    logger.error("malformed frame from host: %s", exc)
                    continue
                rid = msg.get("id")
                fut = self._pending.pop(rid, None) if rid is not None else None
                if fut is None:
                    logger.warning("unmatched response id=%r", rid)
                    continue
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(
                        PluginHostError(
                            err.get("message", "unknown host error")
                        )
                    )
                else:
                    fut.set_result(msg.get("result"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("host read loop crashed: %s", exc)
        finally:
            # Subprocess closed stdout — fail any remaining waiters.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(
                        PluginHostError("host stdout closed")
                    )
            self._pending.clear()

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        async for raw in self._proc.stderr:
            if not raw:
                break
            logger.info("host: %s", raw.decode("utf-8", errors="replace").rstrip())
```

- [ ] **Step 4: Run the tests, confirm they pass**

```bash
.venv/bin/pytest tests/test_cloud_plugin_host.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Run the full suite, confirm no regressions**

```bash
.venv/bin/pytest -q
```

Expected: previous pass count + 5 (so 388 if previous was 383).

- [ ] **Step 6: Commit**

```bash
git add app/cloud/plugin_host.py tests/test_cloud_plugin_host.py
git commit -m "Cloud host: Python PluginHost + tests against fake host"
```

---

## Phase F — Wire into FastAPI lifespan

The downloader runs at startup; immediately after, spawn the C++ host and call `init_plugin`. Stop the host on shutdown.

### Task F.1: Add `bambu_cloud_host_binary` to settings

**Files:**
- Modify: `app/config.py`
- Modify: `tests/test_cloud_config.py`

- [ ] **Step 1: Add the setting**

In `app/config.py`'s `Settings` class, alongside the existing `bambu_cloud_*` fields, add:

```python
bambu_cloud_host_binary: Path = Path("/usr/local/bin/bambu_cloud_host")
```

- [ ] **Step 2: Add a test**

In `tests/test_cloud_config.py`, append:

```python
def test_cloud_host_binary_default(monkeypatch):
    monkeypatch.delenv("BAMBU_CLOUD_HOST_BINARY", raising=False)
    settings = Settings()
    assert settings.bambu_cloud_host_binary == Path("/usr/local/bin/bambu_cloud_host")


def test_cloud_host_binary_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BAMBU_CLOUD_HOST_BINARY", str(tmp_path / "host"))
    settings = Settings()
    assert settings.bambu_cloud_host_binary == tmp_path / "host"
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest tests/test_cloud_config.py -v
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add app/config.py tests/test_cloud_config.py
git commit -m "Cloud host: add BAMBU_CLOUD_HOST_BINARY setting"
```

### Task F.2: Wire `PluginHost` into the lifespan

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_cloud_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cloud_config.py`:

```python
def test_lifespan_starts_plugin_host_after_downloader(monkeypatch, tmp_path):
    import app.main as main_mod
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_enabled", True)
    monkeypatch.setattr(
        main_mod.settings, "bambu_cloud_plugin_dir", tmp_path
    )
    monkeypatch.setattr(
        main_mod.settings,
        "bambu_cloud_host_binary",
        tmp_path / "fake_host_binary",
    )

    started = []
    stopped = []

    class FakePluginHost:
        def __init__(self, **kwargs):
            self._kwargs = kwargs

        async def __aenter__(self):
            started.append(self._kwargs)
            return self

        async def __aexit__(self, *_exc):
            stopped.append(True)

        async def call(self, method, params):
            return {"bootstrap_rc": 0}

    with (
        patch(
            "app.cloud.plugin_downloader.PluginDownloader.ensure_active",
            new=AsyncMock(return_value=None),
        ),
        patch("app.printer_service.PrinterService.start", new=MagicMock()),
        patch("app.main.PluginHost", FakePluginHost),
    ):
        from fastapi.testclient import TestClient
        with TestClient(main_mod.app):
            pass

    assert len(started) == 1
    assert len(stopped) == 1
```

- [ ] **Step 2: Wire `PluginHost` into the lifespan**

Read `app/main.py` to find the existing cloud branch added in Task 1.9. Extend it:

```python
from app.cloud.plugin_host import PluginHost  # add to imports

# inside lifespan, after the downloader's `ensure_active()` succeeds:

active_dir = settings.bambu_cloud_plugin_dir / "active"
host = PluginHost(
    cmd=[str(settings.bambu_cloud_host_binary)],
    env={
        "PJARCZAK_BAMBU_PLUGIN_DIR": str(active_dir),
        "PJARCZAK_BAMBU_NETWORK_SO": str(active_dir / "libbambu_networking.so"),
        "PJARCZAK_BAMBU_SOURCE_SO": str(active_dir / "libBambuSource.so"),
        "BAMBU_CLOUD_REGION": settings.bambu_cloud_region,
    },
)
async with host:
    boot = await host.call("init_plugin", {})
    if boot.get("bootstrap_rc", -1) != 0:
        raise RuntimeError(
            f"Bambu plugin bootstrap failed: {boot}"
        )
    logger.info("Bambu plugin host ready")
    # ... existing PrinterService startup follows; eventually yield ...
    # The `async with host:` block must cover the whole lifespan body so
    # the host runs alongside the FastAPI app.
    # The existing `yield` from the prior lifespan goes inside this block.
```

Read the existing lifespan carefully and restructure so the host is alive for the entire app lifetime — yielding inside `async with host:` is the cleanest way.

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest tests/test_cloud_config.py -v
```

Expected: the new test plus the previously-added ones all pass.

- [ ] **Step 4: Run the full suite**

```bash
.venv/bin/pytest -q
```

Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_cloud_config.py
git commit -m "Cloud host: start PluginHost in lifespan after downloader"
```

---

## Phase G — End-to-end smoke test (manual, Docker)

The unit tests use a Python fake host. The real proof is: spawn the gateway in Docker with cloud mode on, watch it download the plugin, start the C++ host, dlopen the .so, and call `change_user` against the real plugin without crashing.

### Task G.1: Document the smoke test

**Files:**
- Create: `docs/cloud-host-smoke-test.md`

- [ ] **Step 1: Write the runbook**

```markdown
# Bambu Cloud Host — Manual Smoke Test

Confirms the full Phase 2 + 3 pipeline works against the real Bambu plugin.
Not in CI (requires network + Bambu's CDN).

## Steps

1. Build the image:

   ```bash
   docker build -t bambu-gateway:cloud .
   ```

2. Start with cloud mode on, NO printers configured (we only test the host):

   ```bash
   docker run --rm -it \
     -e BAMBU_CLOUD_ENABLED=true \
     -e BAMBU_CLOUD_REGION=US \
     -v $(mktemp -d):/data/bambu-plugin \
     bambu-gateway:cloud
   ```

3. Watch the logs for:
   - `Bambu plugin 02.05.02.58 installed to /data/bambu-plugin/active`
   - `bambu_cloud_host: started`
   - `Bambu plugin host ready`

4. In another terminal, exec a curl against `/api/health` (or whatever the
   gateway's health endpoint is). Confirm it responds.

5. Stop the container. The host should exit cleanly (look for
   `bambu_cloud_host: stdin closed, exiting`).

## Expected failure modes & diagnosis

- "dlopen failed: libagora_rtc_sdk.so: cannot open shared object file" —
  the plugin's dependencies aren't on LD_LIBRARY_PATH. Fix by setting
  `LD_LIBRARY_PATH=/data/bambu-plugin/active` in the runtime image's
  environment (the host inherits it).
- "host died (exit code 139)" — the plugin segfaulted. Check the discovery
  notes; the bootstrap sequence may be incomplete or out of order.
- "PJARCZAK_BAMBU_NETWORK_SO env var is not set" — the lifespan didn't
  pass it through. Check `app/main.py`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/cloud-host-smoke-test.md
git commit -m "Cloud host: smoke-test runbook"
```

### Task G.2: Run the smoke test

This step is manual — run on a Linux host with Docker. Document the output in the runbook with a "First run: <date>, status: pass/fail" section.

- [ ] **Step 1: Build the image**

```bash
docker build -t bambu-gateway:cloud . 2>&1 | tail -30
```

- [ ] **Step 2: Start with cloud mode on**

```bash
mkdir -p /tmp/bambu-plugin-smoke-prod
docker run --rm -d --name bambu-smoke \
  -e BAMBU_CLOUD_ENABLED=true \
  -e BAMBU_CLOUD_REGION=US \
  -e LD_LIBRARY_PATH=/data/bambu-plugin/active \
  -v /tmp/bambu-plugin-smoke-prod:/data/bambu-plugin \
  bambu-gateway:cloud

# Wait a few seconds then check logs.
sleep 8
docker logs bambu-smoke | tail -50
docker stop bambu-smoke
```

- [ ] **Step 3: Document outcome**

If pass: append `First run: <YYYY-MM-DD>, status: pass` to the runbook.

If fail: capture the relevant log lines and report BLOCKED with the
specific failure. Common failures:
- Missing transitive `.so` deps (LD_LIBRARY_PATH issue)
- Bootstrap sequence wrong (segfault in host)
- Discovery had wrong C signatures (segfault or rc != 0)

- [ ] **Step 4: Commit any runbook updates**

```bash
git add docs/cloud-host-smoke-test.md
git commit -m "Cloud host: record first smoke test result"
```

---

## Wrap-up

Phase 2 + 3 are complete: subprocess host builds, runs, loads the real Bambu plugin, and a Python-side `PluginHost` can call `change_user` against it. The follow-up plan (`docs/superpowers/plans/2026-XX-XX-bambu-cloud-host-methods.md`) will extend the host with the remaining methods we need: `connect_server`, `start_subscribe`, `add_subscribe`, `set_on_message_fn`, `set_on_update_status_fn`, `start_print`, `send_message`, `logout`, `bridge.poll_events`. Phase 4 (OAuth) will then assemble these into a working sign-in flow.
