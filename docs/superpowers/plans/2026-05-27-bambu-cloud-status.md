# Bambu Cloud Status Integration — Implementation Plan (Phase 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** End-to-end cloud-MQTT status pipeline: plugin callbacks → C++ event queue → `bridge.poll_events` RPC → Python `EventPump` → registered handler. The handler updates the existing `PrinterStatus` so the dashboard "just works" for cloud printers without endpoint changes.

**Architecture:**

```
Plugin internal thread (libbambu_networking.so)
  │
  │  invokes callback registered via set_on_message_fn
  ▼
C trampoline (in our host)
  │  pushes {dev_id, payload} onto thread-safe deque
  ▼
EventQueue (C++ side, mutex-guarded)
  │
  │  drained by `bridge.poll_events` RPC
  ▼
─── JSONL RPC ───  stdin/stdout
  ▼
PluginHost.call("bridge.poll_events", {})  ← Python
  │  returns list[Event]
  ▼
EventPump (asyncio.Task)
  │  polls every 80ms (active) / 250ms (idle)
  │  dispatches each event to handlers[event_type]
  ▼
Handler coroutine (e.g. update_printer_status)
  │  parses JSON via existing PrinterStatus updater
  ▼
PrinterStatus (existing model, lock-guarded)
```

**Tech Stack:** C++17 (std::deque + std::mutex), Python asyncio, existing `PrinterStatus`/`MachineObject.parse_json` parsing.

**Spec:** §5.4 (event pump), §8.1 (status MQTT).
**Prior outputs in scope:** Phase 2-4's C++ host + Python PluginHost + auth flow.

---

## File Structure

**New files:**

| File | Purpose |
|---|---|
| `tools/bambu_cloud_host/event_queue.hpp` | Thread-safe `std::deque<json>` + mutex |
| `app/cloud/event_pump.py` | `EventPump` asyncio task |
| `app/cloud/cloud_printer.py` | `CloudPrinterClient`: status state-holder updated from events |
| `tests/test_cloud_event_pump.py` | Python tests against the fake host |
| `tests/test_cloud_printer_client.py` | Status-update tests with canned event sequences |
| `docs/superpowers/notes/2026-05-27-bambu-cloud-status-discovery.md` | C ABI discovery for the new plugin methods |

**Modified files:**

| File | Change |
|---|---|
| `tools/bambu_cloud_host/plugin_loader.hpp` | Add 4 new method signatures |
| `tools/bambu_cloud_host/plugin_loader.cpp` | Resolve + bootstrap-call the new symbols |
| `tools/bambu_cloud_host/methods.cpp` | Add `connect_server`, `start_subscribe`, `add_subscribe`, `bridge.poll_events` RPCs |
| `tools/bambu_cloud_host/main.cpp` | Plumb the event queue + register the OnMessageFn trampoline |
| `tests/cloud_fake_host.py` | Add the new methods + a way for tests to inject events |
| `app/main.py` | Start the `EventPump` after `PluginHost` is up |
| `app/printer_service.py` | (Light touch) accept status updates from `CloudPrinterClient` |

---

## Phase A — Discovery: C ABI for cloud-MQTT methods

### Task A.1: Discover the 4 method signatures

**Files:**
- Create: `docs/superpowers/notes/2026-05-27-bambu-cloud-status-discovery.md`

- [ ] **Step 1: Grep for the relevant typedefs**

```bash
grep -n "func_connect_server\|func_start_subscribe\|func_add_subscribe\|func_set_on_message_fn" \
  /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLNetworkPlugin.hpp
```

For each, capture the function-pointer typedef in the discovery notes.

- [ ] **Step 2: Grep for the dlsym calls**

```bash
grep -n "bambu_network_connect_server\|bambu_network_start_subscribe\|bambu_network_add_subscribe\|bambu_network_set_on_message_fn" \
  /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLNetworkPlugin.cpp
```

Note each dlsym symbol name. Add to discovery notes.

- [ ] **Step 3: Read the OnMessageFn callback signature carefully**

The `set_on_message_fn` is the trickiest — it registers a callback that the plugin invokes from its own internal threads. Read both:
- The typedef (likely `using OnMessageFn = std::function<void(std::string dev_id, std::string payload, int chan)>` or similar — be precise)
- How OrcaSlicer's `BBLCloudServiceAgent` (or wherever) wraps the registration: what closure does it pass?

Document:
- The C++ callback type EXACTLY (parameter list, return type, std::function vs raw function pointer)
- Threading guarantees (is the callback ever called concurrently from multiple plugin threads? Spec §5.4 implies yes — internal MQTT thread.)

- [ ] **Step 4: Connect_server semantics**

Read `BBLCloudServiceAgent::connect_server` or similar. Does it block until the cloud broker handshake completes, or return immediately and signal via `OnServerConnectedFn`? This affects how the RPC method handles success/timeout.

- [ ] **Step 5: Document everything**

Append to discovery notes:

```markdown
# Bambu Cloud Status — C ABI Discovery

## connect_server

**dlsym name:** `bambu_network_connect_server`
**C signature:** `<from BBLNetworkPlugin.hpp:LINE>`
**Sync/async:** <…>
**Return semantics:** <0 = ok / negative = …>

## start_subscribe

(same template)

## add_subscribe

(same template — note: takes a vector<string> of dev_ids)

## set_on_message_fn

**dlsym name:** `bambu_network_set_on_message_fn`
**Callback type:**
```cpp
using OnMessageFn = std::function<void(string dev_id, string payload, …)>;
```

**Registration call shape:**
```cpp
plugin.set_on_message_fn([](string dev_id, string payload, …) { … });
```

**Threading:** invoked from <which plugin thread?>. Concurrent calls possible? Must our callback be reentrant?
```

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/notes/2026-05-27-bambu-cloud-status-discovery.md
git commit -m "Cloud status: discover C ABI for subscribe + message callback"
```

---

## Phase B — C++ host: event queue + `bridge.poll_events`

### Task B.1: Implement `event_queue.hpp`

**Files:**
- Create: `tools/bambu_cloud_host/event_queue.hpp`

- [ ] **Step 1: Write the header**

```cpp
// SPDX-License-Identifier: MIT
// Thread-safe queue of plugin-callback events, drained by bridge.poll_events.
#pragma once

#include <deque>
#include <mutex>
#include <vector>

#include "third_party/nlohmann/json.hpp"

namespace bambu_host {

using json = nlohmann::json;

class EventQueue {
 public:
  void push(json event) {
    std::lock_guard<std::mutex> g(m_);
    q_.push_back(std::move(event));
  }

  // Drain everything currently buffered, atomically.
  std::vector<json> drain() {
    std::lock_guard<std::mutex> g(m_);
    std::vector<json> out(q_.begin(), q_.end());
    q_.clear();
    return out;
  }

  std::size_t size() const {
    std::lock_guard<std::mutex> g(m_);
    return q_.size();
  }

 private:
  mutable std::mutex m_;
  std::deque<json> q_;
};

// Process-global instance. The plugin callbacks push into this; the
// bridge.poll_events RPC method drains it.
EventQueue& global_event_queue();

}  // namespace bambu_host
```

- [ ] **Step 2: Add the global-instance accessor**

The global accessor needs to live in a .cpp for ODR safety. Add to `methods.cpp` (or split into a small `event_queue.cpp` — your call):

```cpp
namespace bambu_host {

EventQueue& global_event_queue() {
  static EventQueue g;
  return g;
}

}  // namespace bambu_host
```

- [ ] **Step 3: Commit**

```bash
git add tools/bambu_cloud_host/event_queue.hpp tools/bambu_cloud_host/methods.cpp
git commit -m "Cloud host: thread-safe event queue"
```

### Task B.2: TDD `bridge.poll_events` RPC

This one is C++-side, but we can validate it by spawning the host with synthetic events injected via a hidden `_test_push_event` method (added below for tests only).

**Files:**
- Modify: `tools/bambu_cloud_host/methods.cpp`
- Modify: `tools/bambu_cloud_host/main.cpp` (include event_queue.hpp)

- [ ] **Step 1: Add `bridge.poll_events` + `_test_push_event` methods**

In `methods.cpp` (inside the anonymous namespace):

```cpp
#include "event_queue.hpp"

namespace bambu_host {
namespace {

json method_bridge_poll_events(const json& /*params*/) {
  auto events = global_event_queue().drain();
  return {{"events", events}};
}

// Test-only: lets a Python test push an event into the queue from outside.
// Should NOT be exposed in production, but the cost of leaving it in is
// negligible and it dramatically simplifies integration testing.
json method_test_push_event(const json& params) {
  global_event_queue().push(params);
  return {{"queued", true}};
}

}  // namespace
}  // namespace bambu_host
```

Wire them in `dispatch_method`:

```cpp
if (method == "bridge.poll_events") return method_bridge_poll_events(params);
if (method == "_test_push_event") return method_test_push_event(params);
```

- [ ] **Step 2: Rebuild Docker**

```bash
docker build -t bambu-gateway:cloud-host-test . 2>&1 | tail -5
```

- [ ] **Step 3: Smoke test via piped JSONL**

```bash
docker run --rm -i bambu-gateway:cloud-host-test \
  /usr/local/bin/bambu_cloud_host <<'EOF'
{"id":1,"method":"_test_push_event","params":{"kind":"OnMessage","dev_id":"DEV1","payload":"{}"}}
{"id":2,"method":"bridge.poll_events","params":{}}
{"id":3,"method":"bridge.poll_events","params":{}}
EOF
```

Expected: response 1 = `{"queued":true}`, response 2 = `{"events":[{"kind":"OnMessage", ...}]}`, response 3 = `{"events":[]}` (queue drained).

- [ ] **Step 4: Commit**

```bash
git add tools/bambu_cloud_host/methods.cpp tools/bambu_cloud_host/main.cpp
git commit -m "Cloud host: bridge.poll_events RPC + test-only push"
```

---

## Phase C — C++ host: register `set_on_message_fn` trampoline

### Task C.1: Resolve the symbol + register a queue-pushing trampoline

**Files:**
- Modify: `tools/bambu_cloud_host/plugin_loader.hpp`
- Modify: `tools/bambu_cloud_host/plugin_loader.cpp`

- [ ] **Step 1: Add the typedef + method to plugin_loader.hpp**

```cpp
// Adjust the std::function signature per Phase A discovery notes.
using on_message_fn = std::function<void(std::string dev_id,
                                          std::string payload,
                                          int /*chan*/)>;
using set_on_message_fn_t = int(*)(void* agent, on_message_fn);
```

And in the `PluginLoader` class, add:

```cpp
void register_message_callback();
```

- [ ] **Step 2: Implement in plugin_loader.cpp**

Resolve the symbol in `load_from_env`:

```cpp
p_set_on_message_fn_ = must_resolve<set_on_message_fn_t>(
    dl_handle_, "bambu_network_set_on_message_fn");
```

Then implement the registration. The trampoline must be reentrant (the plugin's MQTT thread may invoke it concurrently):

```cpp
void PluginLoader::register_message_callback() {
  if (!agent_handle_ || !p_set_on_message_fn_) {
    throw std::runtime_error("agent not bootstrapped");
  }
  // Capture nothing — push into the process-global queue.
  on_message_fn cb = [](std::string dev_id, std::string payload, int chan) {
    json event = {
      {"kind", "OnMessage"},
      {"dev_id", std::move(dev_id)},
      {"payload", std::move(payload)},
      {"chan", chan},
    };
    global_event_queue().push(std::move(event));
  };
  int rc = p_set_on_message_fn_(agent_handle_, std::move(cb));
  if (rc != 0) {
    throw std::runtime_error("set_on_message_fn rc=" + std::to_string(rc));
  }
}
```

- [ ] **Step 3: Call `register_message_callback` from `bootstrap()`**

In `PluginLoader::bootstrap()`, after `bambu_network_start(agent_)`, call:

```cpp
register_message_callback();
```

If `set_on_message_fn` rc != 0 is non-fatal for `change_user` use cases (login works without subscription), wrap the call in a try/catch and log instead of throwing. Otherwise propagate.

- [ ] **Step 4: Rebuild**

```bash
docker build -t bambu-gateway:cloud-host-test . 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add tools/bambu_cloud_host/plugin_loader.hpp tools/bambu_cloud_host/plugin_loader.cpp
git commit -m "Cloud host: register OnMessage trampoline into event queue"
```

---

## Phase D — C++ host: `connect_server`, `start_subscribe`, `add_subscribe`

### Task D.1: Add three RPC methods

**Files:**
- Modify: `tools/bambu_cloud_host/plugin_loader.hpp`
- Modify: `tools/bambu_cloud_host/plugin_loader.cpp`
- Modify: `tools/bambu_cloud_host/methods.cpp`

- [ ] **Step 1: Add typedefs + method signatures**

In `plugin_loader.hpp`:

```cpp
// Signatures must match Phase A discovery exactly.
// Example shapes (verify against discovery):
using connect_server_fn = int(*)(void*);
using start_subscribe_fn = int(*)(void*, std::string module);
using add_subscribe_fn = int(*)(void*, std::vector<std::string>);

// Add to PluginLoader class:
int connect_server();
int start_subscribe(const std::string& module);
int add_subscribe(const std::vector<std::string>& dev_ids);
```

- [ ] **Step 2: Resolve + implement**

In `plugin_loader.cpp`'s `load_from_env`:

```cpp
p_connect_server_ = must_resolve<connect_server_fn>(
    dl_handle_, "bambu_network_connect_server");
p_start_subscribe_ = must_resolve<start_subscribe_fn>(
    dl_handle_, "bambu_network_start_subscribe");
p_add_subscribe_ = must_resolve<add_subscribe_fn>(
    dl_handle_, "bambu_network_add_subscribe");
```

Implement the wrappers:

```cpp
int PluginLoader::connect_server() {
  if (!agent_handle_) throw std::runtime_error("agent not bootstrapped");
  return p_connect_server_(agent_handle_);
}

int PluginLoader::start_subscribe(const std::string& module) {
  if (!agent_handle_) throw std::runtime_error("agent not bootstrapped");
  return p_start_subscribe_(agent_handle_, module);
}

int PluginLoader::add_subscribe(const std::vector<std::string>& dev_ids) {
  if (!agent_handle_) throw std::runtime_error("agent not bootstrapped");
  return p_add_subscribe_(agent_handle_, dev_ids);
}
```

- [ ] **Step 3: Add RPC handlers to methods.cpp**

```cpp
json method_connect_server(const json& /*params*/) {
  return {{"rc", loader().connect_server()}};
}

json method_start_subscribe(const json& params) {
  std::string module = params.at("module").get<std::string>();
  return {{"rc", loader().start_subscribe(module)}};
}

json method_add_subscribe(const json& params) {
  auto vec = params.at("dev_ids").get<std::vector<std::string>>();
  return {{"rc", loader().add_subscribe(vec)}};
}
```

And in dispatch_method:

```cpp
if (method == "connect_server") return method_connect_server(params);
if (method == "start_subscribe") return method_start_subscribe(params);
if (method == "add_subscribe") return method_add_subscribe(params);
```

- [ ] **Step 4: Rebuild Docker**

- [ ] **Step 5: Commit**

```bash
git add tools/bambu_cloud_host/plugin_loader.hpp tools/bambu_cloud_host/plugin_loader.cpp \
        tools/bambu_cloud_host/methods.cpp
git commit -m "Cloud host: connect_server + start_subscribe + add_subscribe"
```

---

## Phase E — Python: `EventPump`

### Task E.1: Update the fake host with the new methods + event injection

**Files:**
- Modify: `tests/cloud_fake_host.py`

- [ ] **Step 1: Add new methods**

In `_dispatch`:

```python
# These three just return rc=0 for tests; they don't do anything.
if method == "connect_server":
    return {"rc": 0}
if method == "start_subscribe":
    return {"rc": 0}
if method == "add_subscribe":
    return {"rc": 0}

# bridge.poll_events drains a list stashed in a module-level deque (so
# tests can inject events between RPCs).
if method == "bridge.poll_events":
    events = list(_PENDING_EVENTS)
    _PENDING_EVENTS.clear()
    return {"events": events}

# _test_push_event matches the C++ host's test-only method.
if method == "_test_push_event":
    _PENDING_EVENTS.append(params)
    return {"queued": True}
```

At module level, near the imports, add:

```python
from collections import deque

_PENDING_EVENTS: deque = deque()
```

- [ ] **Step 2: Commit**

```bash
git add tests/cloud_fake_host.py
git commit -m "Cloud status: fake host supports subscribe + event injection"
```

### Task E.2: TDD the `EventPump`

**Files:**
- Create: `app/cloud/event_pump.py`
- Create: `tests/test_cloud_event_pump.py`

- [ ] **Step 1: Failing tests**

Create `tests/test_cloud_event_pump.py`:

```python
"""Tests for EventPump using the Python fake host."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.cloud.event_pump import EventPump
from app.cloud.plugin_host import PluginHost


FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


async def test_event_pump_dispatches_events_to_handler(tmp_path):
    received: list[dict] = []

    async def handler(event: dict) -> None:
        received.append(event)

    async with PluginHost(cmd=[sys.executable, str(FAKE_HOST)]) as host:
        # Inject one event before starting the pump.
        await host.call("_test_push_event", {
            "kind": "OnMessage", "dev_id": "DEV1", "payload": "{\"a\":1}"
        })
        pump = EventPump(
            host=host,
            handlers={"OnMessage": handler},
            idle_interval=0.05,
            active_interval=0.01,
        )
        await pump.start()
        # Wait briefly for the pump to drain.
        await asyncio.sleep(0.2)
        # Inject another event mid-flight.
        await host.call("_test_push_event", {
            "kind": "OnMessage", "dev_id": "DEV2", "payload": "{\"a\":2}"
        })
        await asyncio.sleep(0.2)
        await pump.stop()

    assert len(received) == 2
    assert {e["dev_id"] for e in received} == {"DEV1", "DEV2"}


async def test_event_pump_unknown_event_kind_is_logged_not_raised(caplog):
    async def never_called(event):
        raise AssertionError("should not be called")

    async with PluginHost(cmd=[sys.executable, str(FAKE_HOST)]) as host:
        await host.call("_test_push_event", {"kind": "SomethingNew"})
        pump = EventPump(
            host=host, handlers={"OnMessage": never_called},
            idle_interval=0.05, active_interval=0.01,
        )
        await pump.start()
        await asyncio.sleep(0.2)
        await pump.stop()

    # No crash; some log entry about the unknown kind would be nice but not
    # required for this test.


async def test_event_pump_recovers_when_handler_raises(tmp_path):
    received: list[dict] = []

    async def flaky(event):
        if event["dev_id"] == "BAD":
            raise RuntimeError("boom")
        received.append(event)

    async with PluginHost(cmd=[sys.executable, str(FAKE_HOST)]) as host:
        await host.call("_test_push_event", {"kind": "OnMessage", "dev_id": "BAD"})
        await host.call("_test_push_event", {"kind": "OnMessage", "dev_id": "OK"})
        pump = EventPump(
            host=host, handlers={"OnMessage": flaky},
            idle_interval=0.05, active_interval=0.01,
        )
        await pump.start()
        await asyncio.sleep(0.3)
        await pump.stop()

    # OK still went through despite BAD raising.
    assert any(e["dev_id"] == "OK" for e in received)
```

- [ ] **Step 2: Implement `EventPump`**

Create `app/cloud/event_pump.py`:

```python
"""Polls bridge.poll_events and dispatches events to registered handlers."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Mapping

from app.cloud.plugin_host import PluginHost, PluginHostError

logger = logging.getLogger("bambu.cloud.event_pump")

Handler = Callable[[dict], Awaitable[None]]


class EventPump:
    """Background task that polls the host for events and dispatches them.

    Cadence: ``active_interval`` (default 80ms) while events are flowing,
    ``idle_interval`` (default 250ms) when the queue is empty. The default
    cadence matches OrcaSlicer-bambulab's PJarczak bridge.
    """

    def __init__(
        self,
        *,
        host: PluginHost,
        handlers: Mapping[str, Handler],
        active_interval: float = 0.08,
        idle_interval: float = 0.25,
    ) -> None:
        self._host = host
        self._handlers = dict(handlers)
        self._active = active_interval
        self._idle = idle_interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name="bambu-cloud-event-pump"
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                result = await self._host.call("bridge.poll_events", {})
                events: list[dict] = result.get("events", []) or []
            except PluginHostError as exc:
                logger.warning("poll_events failed: %s; will retry", exc)
                await asyncio.sleep(self._idle)
                continue
            for ev in events:
                kind = ev.get("kind")
                handler = self._handlers.get(kind)
                if handler is None:
                    logger.debug("no handler for event kind=%r", kind)
                    continue
                try:
                    await handler(ev)
                except Exception:
                    logger.exception("handler for %r raised", kind)
            # Cadence: active when we drained at least one event.
            await asyncio.sleep(self._active if events else self._idle)
```

- [ ] **Step 3: Run + commit**

```bash
.venv/bin/pytest tests/test_cloud_event_pump.py -v
git add app/cloud/event_pump.py tests/test_cloud_event_pump.py
git commit -m "Cloud status: EventPump background task"
```

---

## Phase F — Wire the EventPump into the FastAPI lifespan

### Task F.1: Start the EventPump after PluginHost is ready

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_cloud_config.py`

- [ ] **Step 1: Add a test**

Append to `tests/test_cloud_config.py`:

```python
def test_lifespan_starts_event_pump_when_cloud_enabled(monkeypatch, tmp_path):
    """The EventPump must be started after PluginHost.init_plugin succeeds."""
    import app.main as main_mod
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_enabled", True)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_plugin_dir", tmp_path)
    monkeypatch.setattr(main_mod.settings, "bambu_cloud_host_binary", tmp_path)

    started = []
    stopped = []

    class FakePump:
        def __init__(self, **kwargs):
            self._kwargs = kwargs
        async def start(self):
            started.append(self._kwargs)
        async def stop(self):
            stopped.append(True)

    class FakeHost:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def call(self, *_a, **_k): return {"bootstrap_rc": 0}

    with (
        patch(
            "app.cloud.plugin_downloader.PluginDownloader.ensure_active",
            new=AsyncMock(return_value=None),
        ),
        patch("app.printer_service.PrinterService.start", new=MagicMock()),
        patch("app.main.PluginHost", FakeHost),
        patch("app.main.EventPump", FakePump),
    ):
        from fastapi.testclient import TestClient
        with TestClient(main_mod.app):
            pass

    assert len(started) == 1
    assert len(stopped) == 1
```

- [ ] **Step 2: Wire `EventPump` into the lifespan**

In `app/main.py`, add to imports:

```python
from app.cloud.event_pump import EventPump
```

Inside the lifespan, after `init_plugin` succeeds and `app.state.cloud_host = host` is set, register a handler dict (initially empty — Phase G will fill it) and start the pump:

```python
pump = EventPump(host=host, handlers={})  # handlers wired in Phase G
await pump.start()
app.state.cloud_event_pump = pump
stack.push_async_callback(pump.stop)
```

(Using `stack.push_async_callback(pump.stop)` ensures the pump is stopped when the AsyncExitStack unwinds.)

- [ ] **Step 3: Run tests + full suite + commit**

```bash
.venv/bin/pytest tests/test_cloud_config.py -v
.venv/bin/pytest -q
git add app/main.py tests/test_cloud_config.py
git commit -m "Cloud status: start EventPump in lifespan"
```

---

## Phase G — `CloudPrinterClient` + handler wiring

The minimum useful slice for v1: cloud `OnMessage` events update an in-memory `PrinterStatus` keyed by `dev_id`. The existing `/api/printers` endpoint can then surface them.

The existing LAN path uses `BambuMQTTClient` which holds per-printer state behind a `threading.Lock`. The cloud path doesn't need a thread (events are dispatched on the asyncio loop), but we keep the same `PrinterStatus` shape so the existing `parse_json`/`MachineObject` logic can be reused without changes.

### Task G.1: Implement `CloudPrinterClient`

**Files:**
- Create: `app/cloud/cloud_printer.py`
- Create: `tests/test_cloud_printer_client.py`

- [ ] **Step 1: Read the existing LAN client**

Before designing the cloud variant, read `app/mqtt_client.py` (specifically the `BambuMQTTClient` class and how it owns/updates `PrinterStatus`) and `app/models.py` (the `PrinterStatus` dataclass and any parse helpers). The goal is for the cloud variant to expose the SAME read API (e.g. `get_status() -> PrinterStatus`) so callers don't care which path the printer is on.

- [ ] **Step 2: Failing tests**

Create `tests/test_cloud_printer_client.py`:

```python
"""Tests for CloudPrinterClient — receives OnMessage events, updates PrinterStatus."""
from __future__ import annotations

import json

from app.cloud.cloud_printer import CloudPrinterClient


def test_cloud_printer_client_starts_with_unknown_status():
    client = CloudPrinterClient(dev_id="DEV1")
    status = client.get_status()
    assert status is not None
    # Whatever the "empty" PrinterStatus looks like — adjust assertion to
    # match the existing model's defaults.
    # e.g. assert status.online is False


async def test_cloud_printer_client_updates_from_on_message_event():
    client = CloudPrinterClient(dev_id="DEV1")
    # Mimic the payload shape Bambu's MQTT broadcasts (per app/models.py
    # parse_json conventions).
    payload = {
        "print": {
            "gcode_state": "RUNNING",
            "mc_percent": 42,
            # ... other fields ...
        }
    }
    await client.handle_event({
        "kind": "OnMessage",
        "dev_id": "DEV1",
        "payload": json.dumps(payload),
    })
    status = client.get_status()
    assert status.gcode_state == "RUNNING"
    assert status.mc_percent == 42


async def test_cloud_printer_client_ignores_events_for_other_devices():
    client = CloudPrinterClient(dev_id="DEV1")
    await client.handle_event({
        "kind": "OnMessage",
        "dev_id": "DEV2",
        "payload": json.dumps({"print": {"gcode_state": "FAILED"}}),
    })
    # No update applied.
    assert client.get_status().gcode_state != "FAILED"
```

> **Adapt the assertions** above to match the real `PrinterStatus` fields in `app/models.py`. The point is: the client receives an `OnMessage` event whose `payload` is a JSON string matching what Bambu's MQTT broker sends today; it routes that through the SAME parser the LAN client uses.

- [ ] **Step 3: Implement**

Create `app/cloud/cloud_printer.py`:

```python
"""Per-printer state holder updated from cloud MQTT events."""
from __future__ import annotations

import json
import logging
import threading

from app.models import PrinterStatus  # adapt import if the type lives elsewhere

logger = logging.getLogger("bambu.cloud.printer")


class CloudPrinterClient:
    """Mirrors the read surface of ``BambuMQTTClient`` for cloud printers.

    Holds a ``PrinterStatus`` updated by the EventPump's OnMessage handler.
    Thread-safe in the same way as the LAN client.
    """

    def __init__(self, *, dev_id: str) -> None:
        self._dev_id = dev_id
        self._status = PrinterStatus()  # adapt constructor if needed
        self._lock = threading.Lock()

    def get_status(self) -> PrinterStatus:
        with self._lock:
            # If PrinterStatus is mutable, return a copy. If immutable
            # (frozen dataclass), return as-is.
            return self._status

    async def handle_event(self, event: dict) -> None:
        """Dispatched from the EventPump for events whose dev_id matches."""
        if event.get("dev_id") != self._dev_id:
            return
        kind = event.get("kind")
        if kind != "OnMessage":
            return
        raw = event.get("payload", "")
        try:
            msg = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            logger.warning("dev=%s: cloud payload is not JSON", self._dev_id)
            return

        # Reuse the existing LAN parser logic. The LAN client likely has a
        # private `_apply_payload(msg)` method or equivalent that mutates
        # PrinterStatus from a parsed dict. Use the same logic here so the
        # cloud and LAN paths produce identical state updates.
        with self._lock:
            _apply_payload_to_status(self._status, msg)
```

> **Critical:** the `_apply_payload_to_status` helper is whatever the LAN client uses to update `PrinterStatus` from a parsed JSON dict. If that logic isn't already extracted as a module-level function, extract it (small refactor in `app/mqtt_client.py`) so both clients share it. DRY across LAN and cloud paths is essential — the whole point of this design is that the printer-state model is identical between them.

- [ ] **Step 4: Run + commit**

```bash
.venv/bin/pytest tests/test_cloud_printer_client.py -v
git add app/cloud/cloud_printer.py app/mqtt_client.py tests/test_cloud_printer_client.py
git commit -m "Cloud status: CloudPrinterClient applies OnMessage events"
```

### Task G.2: Wire the handler into the EventPump

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Construct cloud clients per configured printer**

In the lifespan, after the PluginHost is up but BEFORE the EventPump is started, instantiate one `CloudPrinterClient` per printer in `printers.json`:

```python
from app.cloud.cloud_printer import CloudPrinterClient

cloud_clients = {
    p.serial: CloudPrinterClient(dev_id=p.serial)
    for p in settings.get_printers()  # or whichever existing accessor lists them
}
app.state.cloud_printers = cloud_clients

async def _on_message(event: dict) -> None:
    dev_id = event.get("dev_id")
    client = cloud_clients.get(dev_id)
    if client is not None:
        await client.handle_event(event)

pump = EventPump(host=host, handlers={"OnMessage": _on_message})
```

- [ ] **Step 2: Update `PrinterService` to surface cloud printers via `/api/printers`**

The existing `/api/printers` route uses `printer_service.list_printers()` or similar. Make it draw from `app.state.cloud_printers` when cloud mode is on, so the dashboard sees the cloud-bound printers without code changes.

This may require a small refactor in `app/printer_service.py`. Read it first to understand the existing accessor pattern; pick the minimal change.

- [ ] **Step 3: Sanity-test against the existing `/api/printers` route**

The existing `tests/test_device_endpoints.py` (or similar) exercises `/api/printers`. Run it to confirm cloud-mode wiring doesn't break the LAN behavior:

```bash
.venv/bin/pytest tests/test_device_endpoints.py -v
.venv/bin/pytest -q
```

- [ ] **Step 4: Commit**

```bash
git add app/main.py app/printer_service.py
git commit -m "Cloud status: wire OnMessage handler + expose cloud printers"
```

---

## Wrap-up

Phase 5 is complete: events flow from the closed plugin → C++ trampoline → event queue → `bridge.poll_events` RPC → `EventPump` → per-printer `CloudPrinterClient` → `PrinterStatus`. The dashboard's existing `/api/printers` endpoint now shows live cloud printer status without any frontend changes.

**Smoke-test status:** The C++ host changes still cannot be smoke-tested locally on Apple Silicon (per Phase 2's known limitation). All Python-side changes are tested against the Python fake host. Real-plugin verification happens at the next deploy to 10.0.1.9 (deferred runbook).

**Follow-up plans:**
- Phase 6 (cloud print submission) needs the auth + status pipeline established here, but is independent code-wise.
- Phase 7 (cloud control commands) layers on top with `send_message` RPCs.
