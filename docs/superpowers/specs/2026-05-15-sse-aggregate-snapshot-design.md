# SSE aggregate snapshot + dedicated project_file cache

**Date:** 2026-05-15
**Status:** Approved

## Problem

`bambu-spool-helper` subscribes to `GET /api/printers/{id}/events` to drive
filament-usage tracking. Its `PrintTracker` is a state machine that requires
`command == "project_file"` (print start), `gcode_state` transitions
(start/end), and `layer_num` deltas (per-layer accounting). When any of those
fail to arrive, no Spoolman `used_weight` is decremented.

In production, no spool's `used_weight` advances after a print. A 30 s sample
of the live SSE stream returns only thin deltas:

```
event: snapshot
data: {"wifi_signal": "-63dBm", "command": "push_status", "msg": 1, "sequence_id": "58631"}

event: report
data: {"nozzle_temper": 25.1875, ...}
```

No `gcode_state`, no `task_id`, no `layer_num`, no `ams_mapping`.

### Root cause

`BambuMQTTClient._on_message` at `app/mqtt_client.py:514-515`:

```python
with self._lock:
    self._latest_print_payload = print_info
```

Every incoming MQTT `print` payload **overwrites** the cache instead of
merging. The pushall response that the gateway already requests on connect
(via `request_pushall()` at `app/mqtt_client.py:483`) lands in the cache for
exactly one MQTT round-trip and is then clobbered by the next thin delta
(typically `wifi_signal`).

Two downstream consequences:

1. **SSE snapshot is stale.** `printer_events` at `app/main.py:398-399`
   emits `client.latest_print_payload`. A late subscriber (any reconnect,
   any cold start after the printer's first pushall) sees only the last
   single delta, never the aggregate state.

2. **`/api/printers/{id}/current-job/file` 404s.** `current_job_file` at
   `app/main.py:438-440` requires
   `latest_print_payload.command == "project_file"`. That holds only for
   the few milliseconds between the printer's `project_file` MQTT message
   and the next delta. After that, the endpoint always 404s — observed in
   production logs:

   ```
   GET /api/printers/.../current-job/file?task_id=0 "HTTP/1.1 404 Not Found"
   ERROR: Failed to resume print; tracking disabled until next start
   ```

Additionally, that same log line reveals a secondary jam in the spool-helper:
when SSE delivered a frame with `gcode_state: "RUNNING"` and `task_id: 0`
(the Bambu idle sentinel), `_attempt_print_resume` fetched `/current-job/file?task_id=0`,
404'd, set `_active_model = None`, but left `_gcode_state = "RUNNING"`. Every
subsequent `FINISH` transition is then a no-op for tracking (because
`_active_model is None`), and tracking is silently disabled for the rest of
that print's lifetime.

## Goals

- SSE snapshot to a fresh subscriber contains the full aggregate printer
  state (`gcode_state`, `task_id`, `subtask_id`, `layer_num`, `ams_mapping`,
  `use_ams`, AMS tray contents, etc.) as last reported by the printer.
- `/api/printers/{id}/current-job/file` remains valid for the lifetime of an
  active print, not just the moments between MQTT messages.
- The spool-helper's resume path no longer jams the tracker when handed an
  idle/placeholder `task_id`.
- Backwards-compatible: existing live `event: report` semantics unchanged.
  Internal `BambuMQTTClient.latest_print_payload` callers continue to work.

## Non-goals

- No `pushall`-on-subscribe — the persistent MQTT connection plus the
  on-connect `pushall` already keeps the aggregate warm; per-subscribe
  pushall adds timing races and MQTT traffic without addressing a real
  failure mode.
- No redesign of the spool-helper resume path to be checkpoint-only. The
  reference implementation (`bambu-spoolman`) does that, but it's a larger
  architectural shift that belongs in a separate spec.

## Design

### Gateway-side: aggregate state + dedicated project_file cache

Mirrors the proven approach in `bambu-spoolman/bambu_mqtt.py`'s
`StatefulPrinterInfo`, adapted for the gateway's threading model.

#### `app/mqtt_client.py`

Add a module-level recursive merge helper:

```python
def _recursive_merge(dst: dict, src: dict) -> None:
    """In-place merge of `src` into `dst`. Nested dicts merge; scalars and
    lists replace. Matches the semantics of bambu-spoolman's
    `recursive_merge` so the aggregate behaves like a long-lived printer
    state mirror."""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _recursive_merge(dst[key], value)
        else:
            dst[key] = value
```

Lists are replaced (not merged element-wise), which is correct for Bambu's
AMS reporting: the printer sends the full AMS tray array when any tray
changes.

In `BambuMQTTClient.__init__`:

- Remove `self._latest_print_payload: dict | None = None`.
- Add `self._aggregate_print_state: dict = {}`.
- Add `self._latest_project_file_payload: dict | None = None`.

In `_on_message`, after the existing `print_info = payload.get("print", {})`
guard:

```python
command = print_info.get("command")
with self._lock:
    if command == "project_file":
        self._latest_project_file_payload = dict(print_info)
    elif command in (None, "push_status"):
        _recursive_merge(self._aggregate_print_state, print_info)
    # Other commands (gcode_line, etc.) flow through to live subscribers
    # via the broker below, but do not pollute either cache.
```

The existing `self._event_broker.publish(dict(print_info), ...)` call stays
unchanged — live deltas keep flowing to subscribers verbatim.

In `_update_status`, after the `gcode_state` assignment, detect FINISH/FAILURE
transitions and clear the project_file cache:

```python
if gcode_state in ("FINISH", "FAILURE") and self._latest_project_file_payload is not None:
    self._latest_project_file_payload = None
```

The aggregate is **not** cleared on FINISH. The printer continues to report
state via push_status and the aggregate self-refreshes. A stale `layer_num`
immediately after FINISH is semantically correct ("the last layer of the
just-finished print").

Properties:

```python
@property
def latest_print_payload(self) -> dict:
    """Aggregate of all `push_status` deltas received since connect.

    Replaces the previous semantics (latest single delta). Returns a
    shallow copy under lock so callers don't race with concurrent merges.
    """
    with self._lock:
        return dict(self._aggregate_print_state)

@property
def latest_project_file_payload(self) -> dict | None:
    """Cached `project_file` command payload for the currently active
    print, or None if no print has started since the last FINISH/FAILURE."""
    with self._lock:
        return dict(self._latest_project_file_payload) if self._latest_project_file_payload else None
```

The `latest_print_payload` name is kept for backwards compatibility with
any external code reading it (audit: only `current_job_file` reads it
externally, and we're updating that caller below).

#### `app/main.py`

`printer_events` snapshot — no code change to the event emit itself;
`client.latest_print_payload` now returns the aggregate:

```python
async def _gen():
    snapshot = client.latest_print_payload or {}
    yield _sse_event("snapshot", snapshot)
    ...
```

`current_job_file` — switch to the dedicated cache:

```python
payload = client.latest_project_file_payload
if not payload or not payload.get("url"):
    raise HTTPException(status_code=404, detail="No active print")
# `command` check removed: the dedicated cache only ever holds project_file
# payloads by construction.

cached_task = payload.get("task_id")
if task_id is not None and cached_task and task_id != cached_task:
    raise HTTPException(status_code=409, detail="Task ID mismatch")
```

### Spool-helper-side: defensive resume guard

#### `app/services/print_tracking/tracker.py`

In `_attempt_print_resume` (currently at `tracker.py:171`), at the top:

```python
async def _attempt_print_resume(self, payload: dict) -> None:
    task_id = payload.get("task_id")
    if task_id in (None, 0, "0", ""):
        # Idle/placeholder task_id — not a real print to resume.
        # Reset `_gcode_state` so a later legitimate RUNNING transition
        # is re-evaluated rather than swallowed as "no change".
        logger.info("Ignoring resume for placeholder task_id=%r", task_id)
        self._gcode_state = None
        return
    subtask_id = payload.get("subtask_id")
    ...
```

In the `except Exception:` branch at `tracker.py:209-212`, reset
`_gcode_state` so the state machine doesn't silently jam after a failed
resume:

```python
except Exception:
    logger.exception("Failed to resume print; tracking disabled until next start")
    self._active_model = None
    self._gcode_state = None  # ← new: allow re-evaluation on next event
    return
```

## Tests

### `bambu-gateway`

**`tests/test_mqtt_client.py`** (extend existing or add a new
`tests/test_aggregate_state.py`):

1. **Aggregate carries forward fields across disjoint deltas.** Feed
   `_on_message` a sequence of `{"print": {"gcode_state": "RUNNING", ...}}`,
   then `{"print": {"layer_num": 10}}`, then `{"print": {"wifi_signal": "-65dBm"}}`.
   Assert `client.latest_print_payload` contains all three.

2. **Nested merge preserves sibling keys.** Feed a payload with both
   `ams: {...}` and `vt_tray: {...}` blocks. Then feed a payload that
   contains only an updated `vt_tray`. Assert the `ams` block is
   unchanged in the aggregate (nested-dict merge does not clobber
   siblings). Then feed a payload with a fresh `ams.ams` list and assert
   the list value is replaced wholesale (lists replace, not merge).

3. **`project_file` cached separately, survives subsequent deltas.** Feed a
   `command: "project_file"` payload with `url` + `task_id`. Feed several
   `push_status` deltas. Assert `latest_project_file_payload` still holds
   the original project_file, and the aggregate does NOT contain its
   one-shot fields (`url`, `param`).

4. **FINISH transition clears project_file cache.** Feed project_file →
   push_status with `gcode_state: "RUNNING"` → push_status with
   `gcode_state: "FINISH"`. Assert `latest_project_file_payload is None`
   and the aggregate still contains `gcode_state: "FINISH"`.

5. **FAILURE transition clears project_file cache** (same as #4 with
   `FAILURE`).

**`tests/test_main_events.py`** (or wherever endpoint tests live):

6. **`/current-job/file` returns 200 after non-`project_file` deltas.** Set
   up an MQTT client with a project_file payload cached and the aggregate
   populated with subsequent push_status. Call `/api/printers/.../current-job/file`
   and assert non-404 (the actual file-fetch path can be mocked).

7. **`/current-job/file` returns 404 after FINISH.** Same setup, then feed
   a FINISH push_status, then call the endpoint, assert 404.

8. **SSE snapshot contains aggregate fields.** Connect to
   `/api/printers/{id}/events`, parse the first `event: snapshot` frame,
   assert it contains every field fed into the aggregate (not just the
   last delta).

### `bambu-spool-helper`

**`tests/print_tracking/test_tracker.py`** (extend existing):

9. **Placeholder `task_id` doesn't trigger fetch.** Feed the tracker an
   event with `gcode_state: "RUNNING"`, `task_id: "0"`. Assert
   `_fetch_3mf` was not called and `_gcode_state is None` after handling.

10. **Resume failure resets `_gcode_state`.** Mock `_fetch_3mf` to raise.
    Feed an event with `gcode_state: "RUNNING"`, `task_id: "ABC"`. Assert
    `_active_model is None`, `_gcode_state is None`, and a subsequent
    `gcode_state: "RUNNING"` transition is re-evaluated (not swallowed).

## Migration / rollout

- Gateway change is deploy-and-restart. No data migration. The first
  pushall after restart populates the aggregate.
- Spool-helper change is deploy-and-restart. No state migration.
- Both changes are independent (the spool-helper guard is purely defensive
  and harmless when the gateway also ships correctly). Order of deploy
  doesn't matter.

## Risks

- **Other readers of `latest_print_payload`.** Audit: only `current_job_file`
  reads it externally; internally `_update_status` consumes the raw
  `print_info` passed from `_on_message`. The semantic shift (latest delta
  → aggregate) is strictly more informative; no realistic consumer is
  broken by getting *more* fields than before.
- **Aggregate-not-cleared could surface stale fields after a print.** Acceptable:
  `gcode_state: "FINISH"` is in the aggregate too, so consumers using
  `gcode_state` to decide what's relevant won't be confused. The dedicated
  `project_file` cache is the only thing that needs explicit clearing.
- **Recursive merge cost.** Bambu payloads are small (kilobytes). Merge is
  O(field-count) per message; well below MQTT message rate (≲10/s).
