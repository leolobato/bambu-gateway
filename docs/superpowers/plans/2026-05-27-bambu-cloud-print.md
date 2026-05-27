# Bambu Cloud Print Submission — Implementation Plan (Phase 6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Wire the existing `/api/print` and `/api/print-stream` routes to dispatch print jobs to **cloud-bound printers** via the plugin's `start_print` method, with `OnUpdateStatusFn` progress events streamed back over the same SSE contract the LAN path uses today. v1 ships **single gcode-3MF only** (no config-3MF — see Phase 0 §Q11.2).

**Architecture:**

```
POST /api/print(-stream) → main.py routes
  │
  ▼
PrinterService.submit_print(printer_id, file, profiles, ...)
  │  branches on connection mode:
  │
  ├── LAN: existing path (BambuMQTTClient + FTPS upload)
  │
  └── Cloud: NEW path
       │
       ▼
       CloudPrinterClient.submit_print(PrintParams)
        │
        ▼
       PluginHost.call("start_print", {dev_id, filename, ...})
        │  (the plugin uploads the 3MF to OSS, posts the job, fires
        │   OnUpdateStatusFn callbacks for progress; those events arrive
        │   via the existing EventPump from Phase 5)
        ▼
       OnUpdateStatusFn events → SSE progress frames (existing contract)
```

**Spec:** §7 (cloud-direct print job flow).
**Prior outputs in scope:** Phase 5's EventPump, CloudPrinterClient, plus PluginHost from Phase 2-3.

---

## File Structure

**New files:**

| File | Purpose |
|---|---|
| `app/cloud/print_params.py` | `PrintParams` dataclass + builder from `/api/print` request shape |
| `app/cloud/error_codes.py` | `BAMBU_NETWORK_ERR_*` → user-facing message map |
| `tests/test_cloud_print_params.py` | Tests for PrintParams construction + AMS mapping serialisation |
| `tests/test_cloud_print_submission.py` | End-to-end: submit_print → fake host → SSE event sequence |
| `docs/superpowers/notes/2026-05-27-bambu-cloud-print-discovery.md` | C ABI discovery for `start_print` + `set_on_update_status_fn` |

**Modified files:**

| File | Change |
|---|---|
| `tools/bambu_cloud_host/plugin_loader.{hpp,cpp}` | Add `start_print` + `set_on_update_status_fn` resolution + trampoline |
| `tools/bambu_cloud_host/methods.cpp` | Add `start_print` RPC method |
| `app/cloud/cloud_printer.py` | Add `submit_print()` method; handle `OnUpdateStatus` events into a `Queue` per active job |
| `app/main.py` | Wire `OnUpdateStatus` event handler in the EventPump |
| `app/printer_service.py` | Route `/api/print` to `CloudPrinterClient.submit_print` when printer is cloud-mode |
| `tests/cloud_fake_host.py` | Add `start_print` method (returns rc=0, optionally emits OnUpdateStatus events) |

---

## Phase A — Discovery

### Task A.1: Discover `start_print` + `set_on_update_status_fn` C ABIs

**Files:**
- Create: `docs/superpowers/notes/2026-05-27-bambu-cloud-print-discovery.md`

- [ ] **Step 1: Grep for typedefs**

```bash
grep -n "func_start_print\|func_set_on_update_status_fn\|OnUpdateStatusFn\|PrintParams" \
  /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/BBLNetworkPlugin.hpp \
  /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/bambu_networking.hpp
```

For each, capture in the discovery notes.

- [ ] **Step 2: Read `PrintParams`**

Read `bambu_networking.hpp` around lines 217-260 (the spec cites this range). Capture the full struct definition with all field names + types. Group them by purpose per the spec's §7.1 table (Identity, Files, AMS, LAN-only, Print options, Routing).

- [ ] **Step 3: Read `start_print` call site**

Read `BBLCloudServiceAgent::start_print` (or wherever it's called in the cloud path — possibly `Jobs/PrintJob.cpp` around line 524-620 per the spec). Capture:
- What gets populated in PrintParams for a pure-cloud send (mode A from spec §7.2)
- Threading: is `start_print` synchronous (returns after job is queued) or does it return immediately and signal via OnUpdateStatusFn?
- Return value semantics

- [ ] **Step 4: Read `OnUpdateStatusFn` signature**

The callback receives `(stage, code, msg)` per spec §7.4. Verify in `bambu_networking.hpp` and capture the exact std::function typedef.

- [ ] **Step 5: Document the error code map**

The spec §7.4 lists important error codes (`-2040`, `-2110`, `-2120`, `-2060`, `-4020`, `-16`). Grep for the full set:

```bash
grep -n "BAMBU_NETWORK_ERR_" /Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/src/slic3r/Utils/bambu_networking.hpp
```

Capture each `#define` with its numeric value and a one-line meaning (read surrounding comments).

- [ ] **Step 6: Document the stages**

`PrintingStageCreate=0, Upload=1, Waiting=2, Sending=3, Record=4, WaitPrinter=5, Finished=6, ERROR=7` per spec §7.4 — verify in `bambu_networking.hpp` and capture.

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/notes/2026-05-27-bambu-cloud-print-discovery.md
git commit -m "Cloud print: discover start_print + OnUpdateStatus C ABI"
```

---

## Phase B — C++ host: `start_print` + `OnUpdateStatusFn` trampoline

### Task B.1: Resolve `set_on_update_status_fn` and register a trampoline

Same pattern as `set_on_message_fn` from Phase 5 Task C.1. The trampoline pushes `OnUpdateStatus` events to the global queue.

**Files:**
- Modify: `tools/bambu_cloud_host/plugin_loader.{hpp,cpp}`

- [ ] **Step 1: Add typedefs**

In `plugin_loader.hpp`:

```cpp
// Adjust signature per discovery — likely:
using on_update_status_fn = std::function<void(int stage, int code,
                                                 std::string msg)>;
using set_on_update_status_fn_t = int(*)(void* agent, on_update_status_fn);
```

Add to PluginLoader:
```cpp
void register_update_status_callback();
```

- [ ] **Step 2: Implement in plugin_loader.cpp**

Resolve in `load_from_env`:

```cpp
p_set_on_update_status_fn_ = must_resolve<set_on_update_status_fn_t>(
    dl_handle_, "bambu_network_set_on_update_status_fn");
```

Register from `bootstrap()` (try/catch + log, non-fatal):

```cpp
void PluginLoader::register_update_status_callback() {
  on_update_status_fn cb = [](int stage, int code, std::string msg) {
    global_event_queue().push({
      {"kind", "OnUpdateStatus"},
      {"stage", stage},
      {"code", code},
      {"msg", std::move(msg)},
    });
  };
  int rc = p_set_on_update_status_fn_(agent_handle_, std::move(cb));
  if (rc != 0) {
    throw std::runtime_error(
      "set_on_update_status_fn rc=" + std::to_string(rc));
  }
}
```

- [ ] **Step 3: Rebuild + commit**

```bash
docker build -t bambu-gateway:cloud-host-test . 2>&1 | tail -5
git add tools/bambu_cloud_host/plugin_loader.hpp tools/bambu_cloud_host/plugin_loader.cpp
git commit -m "Cloud print: register OnUpdateStatus trampoline"
```

### Task B.2: Add `start_print` RPC method

**Files:**
- Modify: `tools/bambu_cloud_host/plugin_loader.{hpp,cpp}`
- Modify: `tools/bambu_cloud_host/methods.cpp`

- [ ] **Step 1: Add the typedef**

In `plugin_loader.hpp` (signature per discovery — most likely a struct or a JSON-flatted argument):

```cpp
// PrintParams is a closed-source struct; we cannot construct it directly.
// Approach: pass a JSON-serialised version + the plugin's wrapper unpacks it.
// IF that's not how the C ABI works, document the alternative in the
// discovery notes and update this typedef. Most likely the actual signature
// is a function that takes the struct by value or by reference — in which
// case the host must declare a matching struct.
using start_print_fn = int(*)(void*, /* params */);
```

> **Critical:** Phase A.1 discovery must clarify what `start_print` actually takes. If it's a struct-by-value, the host needs a matching definition. If it's a JSON string, simpler. Adapt the typedef to reality.

Add to PluginLoader:
```cpp
int start_print(const nlohmann::json& params);
```

- [ ] **Step 2: Implement the wrapper**

Adapt to the real signature. Two likely cases:

**Case A: takes a struct.** Define the matching struct in `plugin_loader.cpp` (matching `PrintParams` field layout from `bambu_networking.hpp`), populate from JSON, call. The struct must match bit-for-bit — get this wrong and you have a memory-layout bug.

**Case B: takes a serialised payload.** Just `dump()` the JSON and call.

```cpp
int PluginLoader::start_print(const nlohmann::json& params) {
  if (!agent_handle_) throw std::runtime_error("agent not bootstrapped");
  // Case B example:
  // return p_start_print_(agent_handle_, params.dump());
  // Case A: populate a struct, then call.
  ...
}
```

- [ ] **Step 3: Add RPC handler**

In `methods.cpp`:

```cpp
json method_start_print(const json& params) {
  return {{"rc", loader().start_print(params)}};
}
// dispatch_method: if (method == "start_print") return method_start_print(params);
```

- [ ] **Step 4: Rebuild + commit**

```bash
docker build -t bambu-gateway:cloud-host-test . 2>&1 | tail -5
git add tools/bambu_cloud_host/plugin_loader.hpp tools/bambu_cloud_host/plugin_loader.cpp \
        tools/bambu_cloud_host/methods.cpp
git commit -m "Cloud print: start_print RPC method"
```

---

## Phase C — Python: `PrintParams` + error mapping

### Task C.1: TDD `PrintParams` builder

**Files:**
- Create: `app/cloud/print_params.py`
- Create: `tests/test_cloud_print_params.py`

- [ ] **Step 1: Failing tests**

The print_params builder takes the existing `/api/print` request shape and produces a dict ready to send as `start_print` RPC params.

```python
"""Tests for PrintParams construction."""
from __future__ import annotations

import pytest

from app.cloud.print_params import build_print_params


def test_build_print_params_minimal():
    p = build_print_params(
        dev_id="DEV1",
        project_name="benchy",
        plate_index=0,
        gcode_3mf_path="/tmp/job.3mf",
        ams_mapping=None,
        use_ams=False,
    )
    assert p["dev_id"] == "DEV1"
    assert p["task_name"] == "benchy_plate_0"
    assert p["project_name"] == "benchy"
    assert p["plate_index"] == 0
    assert p["filename"] == "/tmp/job.3mf"
    # v1 ships gcode-3MF only — config_filename empty per Phase 0 §Q11.2.
    assert p.get("config_filename", "") == ""
    assert p["task_use_ams"] is False
    # Cloud routing — connection_type empty (not "lan"):
    assert p.get("connection_type", "") == ""


def test_build_print_params_with_ams_mapping():
    p = build_print_params(
        dev_id="DEV1",
        project_name="multi-color",
        plate_index=2,
        gcode_3mf_path="/tmp/job.3mf",
        ams_mapping={"0": "PLA_BLUE", "1": "PLA_RED"},
        use_ams=True,
    )
    assert p["task_use_ams"] is True
    # AMS mapping is JSON-string per Phase 0 spec §7.3.
    import json
    assert json.loads(p["ams_mapping"]) == {"0": "PLA_BLUE", "1": "PLA_RED"}


def test_build_print_params_rejects_missing_dev_id():
    with pytest.raises(ValueError):
        build_print_params(
            dev_id="", project_name="x", plate_index=0,
            gcode_3mf_path="/tmp/x.3mf", ams_mapping=None, use_ams=False,
        )
```

- [ ] **Step 2: Implement**

```python
"""Builder for the `start_print` RPC params (cloud print submission)."""
from __future__ import annotations

import json
from typing import Mapping


def build_print_params(
    *,
    dev_id: str,
    project_name: str,
    plate_index: int,
    gcode_3mf_path: str,
    ams_mapping: Mapping[str, str] | None,
    use_ams: bool,
    preset_name: str = "",
) -> dict:
    """Construct the JSON payload for the plugin's start_print RPC.

    Mirrors `PrintParams` from bambu_networking.hpp:217-260, populated for the
    pure-cloud send path (mode A from spec §7.2). LAN-only fields are left
    empty/default; v1 ships gcode-3MF only (no config-3MF).
    """
    if not dev_id:
        raise ValueError("dev_id is required")

    return {
        # Identity
        "dev_id": dev_id,
        "task_name": f"{project_name}_plate_{plate_index}",
        "project_name": project_name,
        "preset_name": preset_name,
        # Files
        "filename": gcode_3mf_path,
        "config_filename": "",  # v1: gcode-3MF only
        "plate_index": plate_index,
        # AMS
        "ams_mapping": json.dumps(ams_mapping) if ams_mapping else "",
        "ams_mapping2": "",
        "ams_mapping_info": "",
        "nozzles_info": "",
        "task_use_ams": use_ams,
        # Routing — cloud (empty connection_type)
        "connection_type": "",
        # Print options — defaults
        "task_bed_leveling": True,
        "task_flow_cali": True,
        "task_vibration_cali": False,
        "task_layer_inspect": False,
        "task_record_timelapse": False,
        # LAN-only fields left empty
        "dev_ip": "",
        "password": "",
        "username": "",
    }
```

- [ ] **Step 3: Run + commit**

```bash
.venv/bin/pytest tests/test_cloud_print_params.py -v
git add app/cloud/print_params.py tests/test_cloud_print_params.py
git commit -m "Cloud print: PrintParams builder"
```

### Task C.2: Error code map

**Files:**
- Create: `app/cloud/error_codes.py`
- Modify: `tests/test_cloud_print_params.py` (add tests at the bottom or new file)

- [ ] **Step 1: Implement**

```python
"""Map plugin BAMBU_NETWORK_ERR_* numeric codes to user-facing messages.

Codes per Phase 0 §7.4. Discovery (Phase A.5) may extend this list.
"""

_ERROR_MESSAGES: dict[int, str] = {
    -2040: "File too large for Bambu cloud upload",
    -2110: "Bambu cloud OSS upload failed",
    -2120: "Bambu cloud rejected the print job",
    -2060: "Timed out waiting for printer to acknowledge cloud job",
    -4020: "LAN FTP upload failed (cloud fallback should engage)",
    -16: "Uploaded file checksum mismatch",
}


def error_message(code: int) -> str:
    """Return a human-readable message for a plugin error code."""
    return _ERROR_MESSAGES.get(code, f"Bambu plugin error {code}")
```

- [ ] **Step 2: Add tests** (small file or append):

```python
# tests/test_cloud_error_codes.py
from app.cloud.error_codes import error_message


def test_error_message_known_code():
    assert "OSS upload" in error_message(-2110)


def test_error_message_unknown_code_falls_back():
    assert "1234" in error_message(1234)
```

- [ ] **Step 3: Run + commit**

```bash
.venv/bin/pytest tests/test_cloud_error_codes.py -v
git add app/cloud/error_codes.py tests/test_cloud_error_codes.py
git commit -m "Cloud print: error code → message map"
```

---

## Phase D — Cloud print submission orchestrator

### Task D.1: Extend `CloudPrinterClient` with `submit_print` + `OnUpdateStatus` routing

**Files:**
- Modify: `app/cloud/cloud_printer.py`
- Modify: `tests/cloud_fake_host.py`
- Create/modify: `tests/test_cloud_print_submission.py`

- [ ] **Step 1: Extend the fake host**

Add `start_print` to the fake host (returns rc=0 immediately, optionally pushes a canned OnUpdateStatus sequence so tests can verify progress events):

```python
if method == "start_print":
    # By default, push a happy-path event sequence so the orchestrator's
    # `await` for terminal events completes promptly.
    import os
    if os.environ.get("FAKE_HOST_PRINT_SCRIPT") == "happy":
        for stage in (0, 1, 2, 3, 6):  # Create, Upload, Waiting, Sending, Finished
            _PENDING_EVENTS.append({
                "kind": "OnUpdateStatus", "stage": stage, "code": 0, "msg": ""
            })
    return {"rc": 0}
```

- [ ] **Step 2: Add `submit_print` to `CloudPrinterClient`**

The method:
1. Calls `host.call("start_print", print_params)` and confirms rc=0.
2. Registers an asyncio Queue keyed by dev_id; the OnUpdateStatus handler pushes events into it.
3. Async-iterates the queue, yielding progress frames matching the existing SSE contract (per spec §7.4 table).
4. Stops on terminal stage (Finished=6 or ERROR=7).

Failing tests:

```python
"""End-to-end cloud print submission test (uses fake host)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.cloud.cloud_printer import CloudPrinterClient
from app.cloud.plugin_host import PluginHost


FAKE_HOST = Path(__file__).parent / "cloud_fake_host.py"


async def test_submit_print_emits_progress_then_finished(tmp_path):
    client = CloudPrinterClient(dev_id="DEV1")

    async with PluginHost(
        cmd=[sys.executable, str(FAKE_HOST)],
        env={"FAKE_HOST_PRINT_SCRIPT": "happy"},
    ) as host:
        events_seen: list[dict] = []

        async def feed_events():
            # Drain the fake host's pending events into the client.
            while not events_seen or events_seen[-1].get("stage") != 6:
                result = await host.call("bridge.poll_events", {})
                for ev in result.get("events", []):
                    if ev.get("kind") == "OnUpdateStatus":
                        events_seen.append(ev)
                        await client.handle_update_status(ev)
                await asyncio.sleep(0.02)

        # Kick off the print + the feeder concurrently.
        feeder = asyncio.create_task(feed_events())
        frames: list[dict] = []
        async for frame in client.submit_print(
            host=host,
            print_params={"dev_id": "DEV1", "task_name": "x"},
        ):
            frames.append(frame)
            if frame.get("event") in ("done", "error"):
                break
        feeder.cancel()
        try:
            await feeder
        except asyncio.CancelledError:
            pass

    # Frames should cover at least progress + done.
    kinds = [f.get("event") for f in frames]
    assert "progress" in kinds
    assert "done" in kinds
```

> Adapt this test to the existing SSE event-name conventions in `app/main.py` or `app/notification_hub.py` — look for whatever the current LAN-mode print-stream returns and match it.

Implementation in `cloud_printer.py`:

```python
import asyncio
import logging

logger = logging.getLogger("bambu.cloud.printer")


class CloudPrinterClient:
    # ... existing init + handle_event from Phase 5 ...

    def __init__(self, *, dev_id: str) -> None:
        self._dev_id = dev_id
        self._status = PrinterStatus()
        self._lock = threading.Lock()
        # Single in-flight print job's progress channel. None = no active job.
        self._progress: asyncio.Queue | None = None

    async def handle_update_status(self, event: dict) -> None:
        """OnUpdateStatus event handler — dispatched from the EventPump."""
        if self._progress is None:
            return
        await self._progress.put(event)

    async def submit_print(
        self, *, host, print_params: dict
    ) -> "AsyncIterator[dict]":
        """Submit a print job and yield SSE-shape frames as progress arrives.

        Frames:
            {"event": "progress", "stage": N, "code": M, "msg": "..."}
            {"event": "done"} on Finished (stage 6)
            {"event": "error", "code": N, "msg": "..."} on ERROR (stage 7)
        """
        from app.cloud.error_codes import error_message

        if self._progress is not None:
            raise RuntimeError("a print job is already in flight for this printer")
        self._progress = asyncio.Queue()
        try:
            result = await host.call("start_print", print_params)
            rc = result.get("rc", -1)
            if rc != 0:
                yield {"event": "error", "code": rc, "msg": error_message(rc)}
                return
            # Now wait for OnUpdateStatus events.
            while True:
                ev = await self._progress.get()
                stage = ev.get("stage")
                code = ev.get("code", 0)
                if stage == 6:  # PrintingStageFinished
                    yield {"event": "done"}
                    return
                if stage == 7:  # PrintingStageERROR
                    yield {"event": "error", "code": code,
                           "msg": error_message(code)}
                    return
                yield {"event": "progress", "stage": stage,
                       "code": code, "msg": ev.get("msg", "")}
        finally:
            self._progress = None
```

- [ ] **Step 3: Run + commit**

```bash
.venv/bin/pytest tests/test_cloud_print_submission.py -v
git add app/cloud/cloud_printer.py tests/cloud_fake_host.py tests/test_cloud_print_submission.py
git commit -m "Cloud print: submit_print + OnUpdateStatus routing"
```

### Task D.2: Wire `OnUpdateStatus` into the lifespan handlers

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Extend the EventPump handler map**

The Phase 5 lifespan registered `{"OnMessage": _on_message}`. Add `"OnUpdateStatus"` routing the event to the right `CloudPrinterClient`:

```python
async def _on_update_status(event: dict) -> None:
    # OnUpdateStatus is per-printer if events carry dev_id; otherwise it
    # belongs to whichever printer has an active submit_print. For v1 we
    # rely on the per-printer single-in-flight invariant: we route to
    # WHICHEVER client currently has self._progress != None.
    for client in cloud_clients.values():
        if client._progress is not None:
            await client.handle_update_status(event)
            return

pump = EventPump(host=host, handlers={
    "OnMessage": _on_message,
    "OnUpdateStatus": _on_update_status,
})
```

> If discovery reveals `OnUpdateStatus` events DO carry a `dev_id` field, route by that instead — cleaner. The fallback above assumes the event doesn't include device identity (which is how OrcaSlicer's PrintJob.cpp treats it).

- [ ] **Step 2: Run full suite**

```bash
.venv/bin/pytest -q
```

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "Cloud print: route OnUpdateStatus to active job"
```

### Task D.3: Wire `/api/print` and `/api/print-stream` to the cloud client

**Files:**
- Modify: `app/printer_service.py` (and possibly `app/main.py` if routes live there)
- Modify: existing test files that exercise the print routes (don't break LAN behavior)

- [ ] **Step 1: Read the existing print routes**

Find where `/api/print` is defined (likely in `app/main.py`) and how it currently invokes `PrinterService.submit_print` or similar. Trace the call chain into `app/printer_service.py`.

- [ ] **Step 2: Add cloud-mode branching**

When `settings.bambu_cloud_enabled` is True AND the requested printer matches a `CloudPrinterClient` in `app.state.cloud_printers`:

1. Slice the input (existing logic — orcaslicer-headless integration; UNCHANGED). Returns a gcode-3MF file path.
2. Build PrintParams via `build_print_params(...)` — passing the file path, dev_id, project_name, etc.
3. Call `cloud_client.submit_print(host=app.state.cloud_host, print_params=...)`.
4. For `/api/print-stream`: forward the yielded frames as SSE events using the existing SSE writer.
5. For `/api/print` (non-streaming): consume the iterator until done/error; return appropriate HTTP response.

> **Important:** the existing print routes do a lot — file validation, profile resolution, AMS matching, etc. Don't reimplement any of that. Just BRANCH on cloud vs LAN at the point where the LAN path calls into MQTT/FTPS. Keep the surface area minimal.

- [ ] **Step 3: Don't break LAN tests**

Run the full suite and confirm no regression:

```bash
.venv/bin/pytest -q
```

- [ ] **Step 4: Commit**

```bash
git add app/printer_service.py app/main.py
git commit -m "Cloud print: route /api/print to cloud client when applicable"
```

---

## Wrap-up

Phase 6 is complete: `/api/print` and `/api/print-stream` work for cloud-bound printers. The plugin uploads the 3MF to Bambu's OSS, dispatches the job, and progress flows back through the same SSE contract the LAN path already provides.

**Still deferred:** real-plugin smoke test (requires native x86_64 Linux per Phase 2 limitation).

**Follow-up:** Phase 7 adds cloud-routed control commands (pause/resume/cancel/speed/AMS-drying) via the plugin's `send_message`.
