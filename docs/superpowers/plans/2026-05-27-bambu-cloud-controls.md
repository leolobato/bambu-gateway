# Bambu Cloud Control Commands — Implementation Plan (Phase 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Route printer control commands (pause/resume/cancel/speed-level/AMS-drying) through the plugin's `send_message` for cloud printers, so the existing control endpoints work transparently in cloud mode. The JSON envelopes are identical to what the LAN path already constructs — this is mostly a routing change, not new business logic.

**Architecture:**

```
POST /api/printers/{id}/pause (or resume, cancel, speed, ams-drying)
  │
  ▼
PrinterService.<command>(printer_id, ...)
  │  branches on connection mode:
  │
  ├── LAN: existing path — BambuMQTTClient.publish_<command>()
  │
  └── Cloud: NEW path
       │
       ▼
       CloudPrinterClient.send_command(json_envelope)
        │
        ▼
       PluginHost.call("send_message", {dev_id, payload, qos, flag})
        │  (plugin publishes to cloud MQTT relay; printer responds via OnMessage,
        │   which Phase 5's pipeline already handles)
        ▼
       Response confirmation arrives via OnMessage (status update)
```

**Spec:** §6 (control commands), §8.2 (transport-neutral command JSON).
**Prior outputs in scope:** Phase 5's CloudPrinterClient, Phase 2-3's PluginHost.

---

## File Structure

**New files:**

| File | Purpose |
|---|---|
| `tests/test_cloud_command_dispatch.py` | Tests for CloudPrinterClient.send_command + per-route smoke tests |
| `docs/superpowers/notes/2026-05-27-bambu-cloud-controls-discovery.md` | C ABI for `send_message` |

**Modified files:**

| File | Change |
|---|---|
| `tools/bambu_cloud_host/plugin_loader.{hpp,cpp}` | Add `send_message` resolution + wrapper |
| `tools/bambu_cloud_host/methods.cpp` | Add `send_message` RPC method |
| `tests/cloud_fake_host.py` | Add `send_message` stub (records the call for assertions) |
| `app/cloud/cloud_printer.py` | Add `send_command(json)` method |
| `app/mqtt_client.py` | Extract command-JSON builders (`build_pause_command`, `build_resume_command`, etc.) into transport-neutral free functions |
| `app/printer_service.py` (or wherever route handlers live) | Branch control endpoints to cloud client when in cloud mode |

---

## Phase A — Discovery: `send_message` C ABI

### Task A.1: Discover the signature

**Files:**
- Create: `docs/superpowers/notes/2026-05-27-bambu-cloud-controls-discovery.md`

- [ ] **Step 1: Grep**

```bash
grep -n "func_send_message\|bambu_network_send_message\|send_message" \
  /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLNetworkPlugin.hpp \
  /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLNetworkPlugin.cpp \
  /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLPrinterAgent.cpp
```

The spec (§6) mentions two methods:
- `send_message(dev_id, json, qos, flag)` — cloud relay
- `send_message_to_printer(dev_id, json, qos, flag)` — LAN direct

We only need the cloud one (`send_message`).

- [ ] **Step 2: Document**

Append to discovery notes per the same template used in earlier phases — typedef, signature, prerequisites, return semantics, threading.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/notes/2026-05-27-bambu-cloud-controls-discovery.md
git commit -m "Cloud controls: discover send_message C ABI"
```

---

## Phase B — C++ host: `send_message` RPC

### Task B.1: Add the RPC

**Files:**
- Modify: `tools/bambu_cloud_host/plugin_loader.{hpp,cpp}`
- Modify: `tools/bambu_cloud_host/methods.cpp`

- [ ] **Step 1: Add the typedef** (adapt to discovery)

```cpp
// Most likely:
using send_message_fn = int(*)(void*, std::string dev_id,
                                 std::string payload,
                                 int qos);  // possibly + flag
```

Add to PluginLoader:
```cpp
int send_message(const std::string& dev_id,
                 const std::string& payload,
                 int qos);
```

- [ ] **Step 2: Resolve + implement**

```cpp
p_send_message_ = must_resolve<send_message_fn>(
    dl_handle_, "bambu_network_send_message");

int PluginLoader::send_message(const std::string& dev_id,
                                const std::string& payload,
                                int qos) {
  if (!agent_handle_) throw std::runtime_error("agent not bootstrapped");
  return p_send_message_(agent_handle_, dev_id, payload, qos);
}
```

- [ ] **Step 3: RPC handler**

```cpp
json method_send_message(const json& params) {
  std::string dev_id = params.at("dev_id").get<std::string>();
  std::string payload = params.at("payload").get<std::string>();
  int qos = params.value("qos", 0);
  return {{"rc", loader().send_message(dev_id, payload, qos)}};
}
// dispatch: if (method == "send_message") return method_send_message(params);
```

- [ ] **Step 4: Rebuild + commit**

```bash
docker build -t bambu-gateway:cloud-host-test . 2>&1 | tail -5
git add tools/bambu_cloud_host/plugin_loader.hpp tools/bambu_cloud_host/plugin_loader.cpp \
        tools/bambu_cloud_host/methods.cpp
git commit -m "Cloud controls: send_message RPC method"
```

---

## Phase C — Python: extract command builders + cloud send_command

### Task C.1: Extract LAN command-JSON builders into transport-neutral functions

**Files:**
- Modify: `app/mqtt_client.py`

The existing `BambuMQTTClient` has methods like `publish_pause()`, `publish_resume()`, `publish_cancel()`, etc., each of which constructs a JSON envelope and publishes it. We want the JSON-construction logic separate from the publish (so the cloud path can build the same JSON and call `send_message` instead of `publish`).

- [ ] **Step 1: Read the existing client**

Find each `publish_<command>` method on `BambuMQTTClient`. Note the exact JSON envelope shape each one constructs (typically `{"print": {"command": "pause", "sequence_id": ...}}` or similar).

- [ ] **Step 2: Extract free functions**

Add free functions at module level in `app/mqtt_client.py`:

```python
def build_pause_command(sequence_id: str | None = None) -> dict: ...
def build_resume_command(sequence_id: str | None = None) -> dict: ...
def build_cancel_command(sequence_id: str | None = None) -> dict: ...
def build_speed_command(level: int, sequence_id: str | None = None) -> dict: ...
def build_ams_start_drying_command(
    ams_id: int, temperature: int, duration_minutes: int,
    sequence_id: str | None = None,
) -> dict: ...
def build_ams_stop_drying_command(
    ams_id: int, sequence_id: str | None = None,
) -> dict: ...
```

Each returns the same dict the existing `publish_<command>` builds. The existing `publish_<command>` methods can now call the free function and pass its result to MQTT (small refactor, shouldn't break anything).

- [ ] **Step 3: Run the existing tests**

```bash
.venv/bin/pytest tests/test_camera_and_light.py tests/test_ams_filament_setting_payload.py tests/test_mqtt_callback.py -v
.venv/bin/pytest -q
```

Expected: no regressions. The LAN command tests still pass — they exercise the same envelopes.

- [ ] **Step 4: Commit**

```bash
git add app/mqtt_client.py
git commit -m "Cloud controls: extract command-JSON builders to free functions"
```

### Task C.2: Add `send_command` to `CloudPrinterClient`

**Files:**
- Modify: `app/cloud/cloud_printer.py`
- Modify: `tests/cloud_fake_host.py`
- Create: `tests/test_cloud_command_dispatch.py`

- [ ] **Step 1: Extend the fake host**

```python
# tests/cloud_fake_host.py
if method == "send_message":
    if not all(k in params for k in ("dev_id", "payload")):
        raise ValueError("send_message requires dev_id + payload")
    # FAKE_HOST_RECORD_FILE is already wired; the request is recorded there
    # so tests can assert on payload contents.
    return {"rc": 0}
```

- [ ] **Step 2: Failing tests**

Create `tests/test_cloud_command_dispatch.py`:

```python
"""Tests for CloudPrinterClient.send_command."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.cloud.cloud_printer import CloudPrinterClient
from app.cloud.plugin_host import PluginHost
from app.mqtt_client import build_pause_command


FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


async def test_send_command_forwards_to_send_message(tmp_path):
    record_file = tmp_path / "requests.jsonl"
    client = CloudPrinterClient(dev_id="DEV1")
    pause_envelope = build_pause_command()

    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_RECORD_FILE": str(record_file)},
    ) as host:
        rc = await client.send_command(host=host, envelope=pause_envelope)

    assert rc == 0
    seen = [json.loads(line) for line in record_file.read_text().splitlines()]
    sm_calls = [r for r in seen if r["method"] == "send_message"]
    assert len(sm_calls) == 1
    p = sm_calls[0]["params"]
    assert p["dev_id"] == "DEV1"
    # payload is JSON-serialised — the plugin expects a string
    assert json.loads(p["payload"]) == pause_envelope


async def test_send_command_returns_nonzero_on_plugin_error(tmp_path):
    # Use the FAKE_HOST_SEND_MESSAGE_RC env knob (added below) to force a
    # non-zero rc.
    client = CloudPrinterClient(dev_id="DEV1")
    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_SEND_MESSAGE_RC": "-7"},
    ) as host:
        rc = await client.send_command(
            host=host, envelope={"print": {"command": "x"}}
        )
    assert rc == -7
```

(Add the `FAKE_HOST_SEND_MESSAGE_RC` knob to the fake host: `return {"rc": int(os.environ.get("FAKE_HOST_SEND_MESSAGE_RC", "0"))}`.)

- [ ] **Step 3: Implement `send_command`**

```python
# app/cloud/cloud_printer.py
import json

class CloudPrinterClient:
    # ... existing ...

    async def send_command(self, *, host, envelope: dict, qos: int = 0) -> int:
        """Publish a command envelope to this printer via the cloud relay.

        Returns the plugin's rc (0 = success, negative = error code).
        The envelope is whatever ``build_<command>`` from app.mqtt_client
        produces — same JSON shape as the LAN path uses today.
        """
        result = await host.call("send_message", {
            "dev_id": self._dev_id,
            "payload": json.dumps(envelope),
            "qos": qos,
        })
        return result.get("rc", -1)
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/bin/pytest tests/test_cloud_command_dispatch.py -v
git add app/cloud/cloud_printer.py tests/cloud_fake_host.py tests/test_cloud_command_dispatch.py
git commit -m "Cloud controls: CloudPrinterClient.send_command"
```

---

## Phase D — Route the existing control endpoints to cloud

### Task D.1: Branch each control endpoint

**Files:**
- Modify: `app/main.py` (or wherever the control routes live — find via `grep -n "pause\|resume\|cancel\|speed\|drying" app/main.py`)
- Modify: `app/printer_service.py` (if route handlers delegate there)

- [ ] **Step 1: Find the existing route definitions**

Grep the route paths:

```bash
grep -n "POST.*pause\|/pause\|/resume\|/cancel\|/speed\|/start-drying\|/stop-drying" app/main.py app/printer_service.py
```

For each route, locate the line where it ultimately calls `BambuMQTTClient.publish_<command>()`. That's the branch point.

- [ ] **Step 2: Add a cloud branch helper**

In `app/printer_service.py` (or wherever the routes converge), add a helper:

```python
async def _dispatch_command(self, printer_id: str, envelope: dict) -> int:
    """Send `envelope` to the printer over whichever transport applies.

    Returns 0 on success, negative rc on plugin error (cloud path), or
    raises if the LAN path's publish fails.
    """
    cloud_client = self._cloud_clients.get(printer_id)
    if cloud_client is not None:
        return await cloud_client.send_command(
            host=self._app.state.cloud_host, envelope=envelope
        )
    # LAN path
    lan_client = self._lan_clients[printer_id]
    lan_client.publish(envelope)
    return 0
```

> Adapt the structure to whatever the existing service shape is. The principle: introduce ONE branch point that routes by transport, and each per-command route reuses it. Don't add a separate cloud branch in every route handler.

- [ ] **Step 3: Switch each route to use the helper**

For each of the 6 routes:
- `POST /api/printers/{id}/pause` → `_dispatch_command(id, build_pause_command())`
- `POST /api/printers/{id}/resume` → `_dispatch_command(id, build_resume_command())`
- `POST /api/printers/{id}/cancel` → `_dispatch_command(id, build_cancel_command())`
- `POST /api/printers/{id}/speed` → `_dispatch_command(id, build_speed_command(level=req.level))`
- `POST /api/printers/{id}/ams/{ams_id}/start-drying` → `_dispatch_command(id, build_ams_start_drying_command(...))`
- `POST /api/printers/{id}/ams/{ams_id}/stop-drying` → `_dispatch_command(id, build_ams_stop_drying_command(...))`

The route bodies should now be 3-4 lines each: extract params, build envelope, dispatch, return response.

- [ ] **Step 4: Run the existing tests + add a cloud-mode smoke test**

The existing tests for `/pause`, `/resume`, etc. exercise the LAN path. Make sure they still pass. Then add ONE end-to-end smoke test in `tests/test_cloud_command_dispatch.py` that exercises the route via TestClient with cloud mode on (similar pattern to Phase 4's `cloud_app` fixture):

```python
def test_pause_route_dispatches_to_cloud_when_cloud_mode_on(cloud_app):
    # The fixture has cloud mode enabled with the fake host. The pause
    # route should reach the fake host's send_message stub and return ok.
    resp = cloud_app.post("/api/printers/DEV1/pause")
    assert resp.status_code in (200, 204), resp.text
```

Adapt the test to the actual route URL pattern and HTTP method.

- [ ] **Step 5: Run full suite**

```bash
.venv/bin/pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/printer_service.py tests/test_cloud_command_dispatch.py
git commit -m "Cloud controls: route control endpoints to cloud client"
```

---

## Wrap-up

Phase 7 complete. The gateway now fully supports cloud-direct printer operation:

- Login: paste-fallback OAuth (Phase 4)
- Status: cloud MQTT into the existing dashboard (Phase 5)
- Print submission: cloud start_print with SSE progress (Phase 6)
- Control: pause/resume/cancel/speed/AMS-drying over cloud relay (this phase)

**Still pending across all phases:** end-to-end smoke test against the real plugin, which requires deploying to native Linux x86_64 (per Phase 2's documented limitation on Apple Silicon + OrbStack).

**Suggested first deploy step:** push to `10.0.1.9` via `deploy-docker.sh`, then walk through the smoke-test runbooks in order: plugin downloader → host startup → auth → status → print → controls. Each runbook documents its known failure modes so any breakage points at a specific phase.
