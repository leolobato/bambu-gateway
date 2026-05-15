# SSE Aggregate Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the gateway's SSE snapshot carry the full aggregate printer state and keep `/current-job/file` valid for the lifetime of an active print, and harden the spool-helper's resume path against placeholder `task_id` values.

**Architecture:** `BambuMQTTClient` maintains a long-lived `_aggregate_print_state` dict that absorbs every push_status delta via recursive merge (mirroring `bambu-spoolman`'s proven approach), plus a separate `_latest_project_file_payload` cache for the one-shot `project_file` command. SSE snapshots emit the aggregate; `/current-job/file` reads from the dedicated cache. The spool-helper gains a defensive guard that ignores placeholder `task_id` values and resets `_gcode_state` on resume failure so the tracker can recover.

**Tech Stack:** Python 3.12+, FastAPI, paho-mqtt v2, pytest (gateway); unittest (spool-helper). Two repos:
- Gateway: `/Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway`
- Spool-helper: `/Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-spool-helper`

**Spec:** `bambu-gateway/docs/superpowers/specs/2026-05-15-sse-aggregate-snapshot-design.md`

---

## File Structure

**Gateway changes** (`bambu-gateway/`):
- `app/mqtt_client.py` — module-level `_recursive_merge` helper; replace `_latest_print_payload` field with `_aggregate_print_state` (dict) and `_latest_project_file_payload` (dict | None); update `_on_message` to dispatch by `command`; update `_update_status` to clear project_file cache on FINISH/FAILURE; update `latest_print_payload` property; add `latest_project_file_payload` property.
- `app/main.py` — `current_job_file` endpoint reads from `latest_project_file_payload`.
- `tests/test_mqtt_client.py` — update assertions for merge semantics; add aggregate/project_file/clear tests.
- `tests/test_current_job_file_endpoint.py` — update stub to expose `latest_project_file_payload`.

**Spool-helper changes** (`bambu-spool-helper/`):
- `app/services/print_tracking/tracker.py` — guard `_attempt_print_resume` against placeholder `task_id`; reset `_gcode_state` on resume failure.
- `tests/print_tracking/test_tracker.py` — add tests for the guard and reset behavior.

---

## Task 1: Add `_recursive_merge` helper to `BambuMQTTClient`

**Files:**
- Modify: `bambu-gateway/app/mqtt_client.py`
- Test: `bambu-gateway/tests/test_mqtt_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `bambu-gateway/tests/test_mqtt_client.py`:

```python
def test_recursive_merge_flat_keys():
    from app.mqtt_client import _recursive_merge

    dst = {"a": 1, "b": 2}
    _recursive_merge(dst, {"b": 20, "c": 30})
    assert dst == {"a": 1, "b": 20, "c": 30}


def test_recursive_merge_nested_dicts():
    from app.mqtt_client import _recursive_merge

    dst = {"ams": {"version": "1.0", "trays": "untouched"}, "outer": "kept"}
    _recursive_merge(dst, {"ams": {"version": "1.1"}})
    assert dst == {
        "ams": {"version": "1.1", "trays": "untouched"},
        "outer": "kept",
    }


def test_recursive_merge_replaces_lists_wholesale():
    from app.mqtt_client import _recursive_merge

    dst = {"trays": [{"id": 0}, {"id": 1}]}
    _recursive_merge(dst, {"trays": [{"id": 0, "color": "red"}]})
    assert dst == {"trays": [{"id": 0, "color": "red"}]}


def test_recursive_merge_dict_replaces_scalar():
    """If dst has a scalar and src brings a dict for the same key, src wins."""
    from app.mqtt_client import _recursive_merge

    dst = {"vt_tray": None}
    _recursive_merge(dst, {"vt_tray": {"id": 254, "color": "blue"}})
    assert dst == {"vt_tray": {"id": 254, "color": "blue"}}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
.venv/bin/pytest tests/test_mqtt_client.py::test_recursive_merge_flat_keys tests/test_mqtt_client.py::test_recursive_merge_nested_dicts tests/test_mqtt_client.py::test_recursive_merge_replaces_lists_wholesale tests/test_mqtt_client.py::test_recursive_merge_dict_replaces_scalar -v
```

Expected: 4 failures with `ImportError: cannot import name '_recursive_merge'`.

- [ ] **Step 3: Implement `_recursive_merge`**

Add to `bambu-gateway/app/mqtt_client.py`, near the top (after the constants near line 37, before `class BambuMQTTClient`):

```python
def _recursive_merge(dst: dict, src: dict) -> None:
    """In-place merge of `src` into `dst`.

    Nested dicts merge recursively; everything else (scalars, lists, None)
    replaces. Mirrors `bambu-spoolman`'s `recursive_merge` so the aggregate
    printer state behaves like a long-lived mirror of MQTT push_status
    deltas: every key the printer ever reports stays in the dict until the
    printer reports a new value for it.

    Lists replace wholesale rather than merging element-wise because Bambu's
    AMS payload reports the entire tray array on any tray change — an
    element-wise merge would conflate stale entries with fresh ones.
    """
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _recursive_merge(dst[key], value)
        else:
            dst[key] = value
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
.venv/bin/pytest tests/test_mqtt_client.py -v -k "recursive_merge"
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
git add app/mqtt_client.py tests/test_mqtt_client.py
git commit -m "$(cat <<'EOF'
feat(mqtt): add `_recursive_merge` helper for printer state aggregation

- Add a module-level helper that merges nested dicts recursively while replacing scalars and lists. Lays groundwork for replacing the
  single-delta `_latest_print_payload` cache with an aggregate state mirror.
- Mirrors `bambu-spoolman`'s proven `recursive_merge` semantics: lists replace wholesale so a fresh AMS tray report supersedes the prior one
  instead of conflating stale entries.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Switch `BambuMQTTClient` to aggregate + dedicated `project_file` cache

**Files:**
- Modify: `bambu-gateway/app/mqtt_client.py`
- Test: `bambu-gateway/tests/test_mqtt_client.py`

This task replaces the field, updates `_on_message`, updates `_update_status` to clear the project_file cache on FINISH/FAILURE, and adds the new property. The existing `test_on_message_*` tests need their assertions updated to match the new merge semantics. New tests cover project_file separation and FINISH-clears-cache.

- [ ] **Step 1: Update existing tests to expect aggregate semantics**

In `bambu-gateway/tests/test_mqtt_client.py`, replace `test_on_message_caches_latest_print_payload`, `test_on_message_ignores_non_print_payload`, and `test_on_message_overwrites_previous_payload` with these versions (rename the third to reflect new behavior):

```python
def test_on_message_caches_first_push_status_into_aggregate():
    client = _make_client()
    msg = MagicMock()
    msg.payload = json.dumps({
        "print": {"command": "push_status", "layer_num": 42, "gcode_state": "RUNNING"}
    }).encode()
    msg.topic = "device/S01/report"
    client._on_message(None, None, msg)
    assert client.latest_print_payload == {
        "command": "push_status",
        "layer_num": 42,
        "gcode_state": "RUNNING",
    }


def test_on_message_ignores_non_print_payload():
    client = _make_client()
    msg = MagicMock()
    msg.payload = json.dumps({"info": {"command": "get_version"}}).encode()
    msg.topic = "device/S01/report"
    client._on_message(None, None, msg)
    assert client.latest_print_payload == {}


def test_on_message_merges_subsequent_push_status_into_aggregate():
    """The aggregate accumulates fields across deltas instead of overwriting."""
    client = _make_client()
    first = MagicMock()
    first.payload = json.dumps({
        "print": {"command": "push_status", "layer_num": 1, "wifi_signal": "-60dBm"}
    }).encode()
    first.topic = "device/S01/report"
    second = MagicMock()
    second.payload = json.dumps({
        "print": {"command": "push_status", "layer_num": 2, "gcode_state": "RUNNING"}
    }).encode()
    second.topic = "device/S01/report"
    client._on_message(None, None, first)
    client._on_message(None, None, second)
    # `wifi_signal` survives from the first delta; `layer_num` overwrites;
    # `gcode_state` is added.
    assert client.latest_print_payload == {
        "command": "push_status",
        "layer_num": 2,
        "wifi_signal": "-60dBm",
        "gcode_state": "RUNNING",
    }
```

Then add these new tests at the end of the file:

```python
def test_on_message_caches_project_file_separately():
    """`project_file` payloads go into their own cache, not the aggregate."""
    client = _make_client()
    pf = MagicMock()
    pf.payload = json.dumps({
        "print": {
            "command": "project_file",
            "url": "file:///cache/m.3mf",
            "param": "Metadata/plate_1.gcode",
            "task_id": "T1",
            "subtask_id": "ST1",
            "ams_mapping": [0, 1],
            "use_ams": True,
            "gcode_state": "RUNNING",
        }
    }).encode()
    pf.topic = "device/S01/report"
    client._on_message(None, None, pf)

    assert client.latest_project_file_payload == {
        "command": "project_file",
        "url": "file:///cache/m.3mf",
        "param": "Metadata/plate_1.gcode",
        "task_id": "T1",
        "subtask_id": "ST1",
        "ams_mapping": [0, 1],
        "use_ams": True,
        "gcode_state": "RUNNING",
    }
    # Aggregate is NOT polluted with the project_file one-shot fields.
    assert "url" not in client.latest_print_payload
    assert "param" not in client.latest_print_payload


def test_project_file_cache_survives_subsequent_push_status_deltas():
    client = _make_client()
    pf = MagicMock()
    pf.payload = json.dumps({
        "print": {
            "command": "project_file",
            "url": "file:///cache/m.3mf",
            "task_id": "T1",
        }
    }).encode()
    pf.topic = "device/S01/report"
    delta = MagicMock()
    delta.payload = json.dumps({
        "print": {"command": "push_status", "wifi_signal": "-65dBm"}
    }).encode()
    delta.topic = "device/S01/report"

    client._on_message(None, None, pf)
    client._on_message(None, None, delta)

    assert client.latest_project_file_payload is not None
    assert client.latest_project_file_payload["task_id"] == "T1"


def test_finish_clears_project_file_cache():
    client = _make_client()
    pf = MagicMock()
    pf.payload = json.dumps({
        "print": {
            "command": "project_file",
            "url": "file:///cache/m.3mf",
            "task_id": "T1",
            "gcode_state": "RUNNING",
        }
    }).encode()
    pf.topic = "device/S01/report"
    finish = MagicMock()
    finish.payload = json.dumps({
        "print": {"command": "push_status", "gcode_state": "FINISH"}
    }).encode()
    finish.topic = "device/S01/report"

    client._on_message(None, None, pf)
    assert client.latest_project_file_payload is not None
    client._on_message(None, None, finish)
    assert client.latest_project_file_payload is None


def test_failure_clears_project_file_cache():
    client = _make_client()
    pf = MagicMock()
    pf.payload = json.dumps({
        "print": {
            "command": "project_file",
            "url": "file:///cache/m.3mf",
            "task_id": "T1",
            "gcode_state": "RUNNING",
        }
    }).encode()
    pf.topic = "device/S01/report"
    failure = MagicMock()
    failure.payload = json.dumps({
        "print": {"command": "push_status", "gcode_state": "FAILURE"}
    }).encode()
    failure.topic = "device/S01/report"

    client._on_message(None, None, pf)
    client._on_message(None, None, failure)
    assert client.latest_project_file_payload is None


def test_aggregate_preserves_nested_ams_siblings():
    """An update to one nested block doesn't clobber a sibling block."""
    client = _make_client()
    first = MagicMock()
    first.payload = json.dumps({
        "print": {
            "command": "push_status",
            "ams": {"version": "1.0", "tray_now": "0"},
            "vt_tray": {"id": 254, "color": "red"},
        }
    }).encode()
    first.topic = "device/S01/report"
    second = MagicMock()
    second.payload = json.dumps({
        "print": {
            "command": "push_status",
            "vt_tray": {"color": "blue"},
        }
    }).encode()
    second.topic = "device/S01/report"

    client._on_message(None, None, first)
    client._on_message(None, None, second)

    agg = client.latest_print_payload
    # `ams` block untouched by the vt_tray-only update.
    assert agg["ams"] == {"version": "1.0", "tray_now": "0"}
    # `vt_tray.color` updated; `vt_tray.id` preserved.
    assert agg["vt_tray"] == {"id": 254, "color": "blue"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
.venv/bin/pytest tests/test_mqtt_client.py -v
```

Expected: failures on `latest_project_file_payload` (attribute doesn't exist), on `latest_print_payload == {}` (currently `None`), and on the merge-semantics assertions.

- [ ] **Step 3: Update `__init__` to use the new fields**

In `bambu-gateway/app/mqtt_client.py`, around line 57, replace:

```python
        self._latest_print_payload: dict | None = None
```

with:

```python
        # Long-lived mirror of every push_status delta the printer reports.
        # Late SSE subscribers read this so they see full state, not just
        # whatever single field the printer happened to update last.
        self._aggregate_print_state: dict = {}
        # Holds the active print's `project_file` payload (url, param,
        # task_id, ams_mapping, etc.). None when no print is active.
        # Cleared on `gcode_state` reaching FINISH or FAILURE.
        self._latest_project_file_payload: dict | None = None
```

- [ ] **Step 4: Replace the `latest_print_payload` property and add `latest_project_file_payload`**

In `bambu-gateway/app/mqtt_client.py`, around line 68-72, replace:

```python
    @property
    def latest_print_payload(self) -> dict | None:
        """Most recently received `print` payload (raw), or None if none seen yet."""
        with self._lock:
            return self._latest_print_payload
```

with:

```python
    @property
    def latest_print_payload(self) -> dict:
        """Aggregate of every push_status `print` payload received since connect.

        Replaces the previous "last single delta" semantics. Returns a shallow
        copy so callers don't race with concurrent merges on the MQTT thread.
        An empty dict means no push_status has arrived yet (cold start before
        the printer's first pushall response lands).
        """
        with self._lock:
            return dict(self._aggregate_print_state)

    @property
    def latest_project_file_payload(self) -> dict | None:
        """The active print's cached `project_file` payload, or None.

        Set whenever the printer reports `command == "project_file"`; cleared
        when `gcode_state` reaches FINISH or FAILURE. Survives intervening
        push_status deltas so `/api/printers/{id}/current-job/file` resolves
        for the full lifetime of an active print.
        """
        with self._lock:
            if self._latest_project_file_payload is None:
                return None
            return dict(self._latest_project_file_payload)
```

- [ ] **Step 5: Update `_on_message` to dispatch by command**

In `bambu-gateway/app/mqtt_client.py`, around line 510-516, replace:

```python
        print_info = payload.get("print", {})
        if not print_info:
            return

        with self._lock:
            self._latest_print_payload = print_info
```

with:

```python
        print_info = payload.get("print", {})
        if not print_info:
            return

        command = print_info.get("command")
        with self._lock:
            if command == "project_file":
                self._latest_project_file_payload = dict(print_info)
            elif command in (None, "push_status"):
                _recursive_merge(self._aggregate_print_state, print_info)
            # Other commands (gcode_line, etc.) still flow through to live
            # subscribers via the broker below, but do not pollute either
            # cache — they aren't durable state.
```

- [ ] **Step 6: Clear project_file cache on FINISH/FAILURE in `_update_status`**

In `bambu-gateway/app/mqtt_client.py`, around line 588-595, find:

```python
    def _update_status(self, print_info: dict) -> None:
        """Apply fields from an MQTT print report to the in-memory status."""
        with self._lock:
            prev_snapshot = self._status.model_copy(deep=True)
            # Track raw fields for state derivation
            gcode_state = print_info.get("gcode_state")
            if gcode_state is not None:
                self._gcode_state = gcode_state
```

and add the cache-clear immediately after the `self._gcode_state = gcode_state` line:

```python
    def _update_status(self, print_info: dict) -> None:
        """Apply fields from an MQTT print report to the in-memory status."""
        with self._lock:
            prev_snapshot = self._status.model_copy(deep=True)
            # Track raw fields for state derivation
            gcode_state = print_info.get("gcode_state")
            if gcode_state is not None:
                self._gcode_state = gcode_state
                if gcode_state in ("FINISH", "FAILURE"):
                    # The active print is over; drop the cached project_file
                    # so a late SSE subscriber doesn't see stale resume context.
                    self._latest_project_file_payload = None
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
.venv/bin/pytest tests/test_mqtt_client.py -v
```

Expected: all tests in `tests/test_mqtt_client.py` pass (existing + new).

- [ ] **Step 8: Run the full gateway suite to catch downstream breakage**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
.venv/bin/pytest -x
```

Expected: only `tests/test_current_job_file_endpoint.py` may fail because it stubs `latest_print_payload` instead of `latest_project_file_payload`. Task 3 fixes those. `tests/test_events_endpoint.py` may also need an adjustment for the "no cached payload" test asserting `{}` vs `None` — note any failures and address in Task 3.

- [ ] **Step 9: Commit**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
git add app/mqtt_client.py tests/test_mqtt_client.py
git commit -m "$(cat <<'EOF'
feat(mqtt): aggregate push_status deltas; dedicated project_file cache

- Replace the single-delta `_latest_print_payload` with a long-lived `_aggregate_print_state` mirror so SSE snapshots carry the full
  printer state (gcode_state, layer_num, task_id, ams_mapping) to late subscribers instead of whatever single field the printer happened
  to report last.
- Cache `command: "project_file"` payloads separately so `/current-job/file` keeps resolving across subsequent push_status deltas.
- Clear the `project_file` cache when `gcode_state` reaches FINISH or FAILURE so a late subscriber doesn't see stale resume context after
  the print is over.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Update `/current-job/file` to use `latest_project_file_payload`

**Files:**
- Modify: `bambu-gateway/app/main.py` (the `current_job_file` handler around lines 422-445)
- Test: `bambu-gateway/tests/test_current_job_file_endpoint.py`

- [ ] **Step 1: Update existing endpoint tests to expose `latest_project_file_payload` on the stub**

In `bambu-gateway/tests/test_current_job_file_endpoint.py`, replace the `_make_stub_service` helper (lines 11-25) with:

```python
def _make_stub_service(*, project_file_payload=None):
    """Build a minimal printer_service stub for these tests."""
    class _Client:
        def __init__(self, host="10.0.0.5", access_code="x"):
            self.host = host
            self.access_code = access_code
            self.latest_project_file_payload = project_file_payload

    class _Service:
        def get_client(self, pid):
            return _Client() if pid == "S1" else None
        def default_printer_id(self):
            return "S1"

    return _Service()
```

Then update each test's call:

- `test_404_when_no_cached_project_file`: change `payload=None` to `project_file_payload=None` (already None; just rename kwarg).
- `test_ftp_url_triggers_ftps_download`: change `payload=payload` to `project_file_payload=payload`.
- `test_http_url_passes_through`: change `payload=payload` to `project_file_payload=payload`.
- `test_task_id_mismatch_returns_409`: change `payload=payload` to `project_file_payload=payload`.

Also add this new test at the bottom of the file:

```python
@pytest.mark.asyncio
async def test_404_after_finish_clears_cache(monkeypatch):
    """Once the project_file cache is None (e.g. after FINISH), endpoint 404s."""
    monkeypatch.setattr(
        main_mod, "printer_service",
        _make_stub_service(project_file_payload=None),
    )
    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/printers/S1/current-job/file")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run endpoint tests to verify they fail**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
.venv/bin/pytest tests/test_current_job_file_endpoint.py -v
```

Expected: failures because the handler still reads `client.latest_print_payload`, which the stub no longer exposes.

- [ ] **Step 3: Update the `current_job_file` handler**

In `bambu-gateway/app/main.py`, around lines 438-445, replace:

```python
    payload = client.latest_print_payload
    if not payload or payload.get("command") != "project_file" or not payload.get("url"):
        raise HTTPException(status_code=404, detail="No active print")

    cached_task = payload.get("task_id")
    if task_id is not None and cached_task and task_id != cached_task:
        raise HTTPException(status_code=409, detail="Task ID mismatch")
```

with:

```python
    payload = client.latest_project_file_payload
    if not payload or not payload.get("url"):
        raise HTTPException(status_code=404, detail="No active print")

    cached_task = payload.get("task_id")
    if task_id is not None and cached_task and task_id != cached_task:
        raise HTTPException(status_code=409, detail="Task ID mismatch")
```

(The `command != "project_file"` check is removed: the dedicated cache only ever holds project_file payloads by construction.)

- [ ] **Step 4: Run endpoint tests to verify they pass**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
.venv/bin/pytest tests/test_current_job_file_endpoint.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Check the events endpoint test still passes**

`tests/test_events_endpoint.py` has a test (`test_events_emits_empty_snapshot_when_no_cached_payload`) that sets `latest_print_payload = None` on its stub. With the new property semantics returning `{}` not `None`, the snapshot frame's `_gen()` uses `client.latest_print_payload or {}` (existing code in `main.py` line 398), so both `None` and `{}` yield an empty snapshot. The test should still pass — verify:

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
.venv/bin/pytest tests/test_events_endpoint.py -v
```

Expected: all events tests pass. If `test_events_emits_empty_snapshot_when_no_cached_payload` fails, change its stub's `latest_print_payload = None` to `latest_print_payload = {}` — both represent "no state yet" under the new semantics.

- [ ] **Step 6: Run the full gateway suite**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
.venv/bin/pytest -x
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
git add app/main.py tests/test_current_job_file_endpoint.py tests/test_events_endpoint.py
git commit -m "$(cat <<'EOF'
feat(api): /current-job/file reads from dedicated project_file cache

- Switch the handler to read `latest_project_file_payload` so the endpoint resolves for the full lifetime of an active print, not just the
  brief window between the printer's `project_file` MQTT message and the next push_status delta.
- Drop the now-redundant `command != "project_file"` guard since the dedicated cache only ever holds project_file payloads by construction.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Spool-helper — guard `_attempt_print_resume` against placeholder `task_id`

**Files:**
- Modify: `bambu-spool-helper/app/services/print_tracking/tracker.py`
- Test: `bambu-spool-helper/tests/print_tracking/test_tracker.py`

- [ ] **Step 1: Write the failing test**

Append to `bambu-spool-helper/tests/print_tracking/test_tracker.py` (inside `class TestPrintTrackerResume` at the end, before the closing of the class):

```python
    async def test_placeholder_task_id_skips_resume(self):
        """A RUNNING snapshot with task_id=0/"0"/None/"" must not 404-fetch.

        The Bambu MQTT idle sentinel is `task_id: 0`. Without this guard, the
        tracker would call `_fetch_3mf(0)`, the gateway would 404, and the
        resume except branch would leave `_gcode_state = "RUNNING"` set —
        silently disabling FINISH detection for the rest of the session.
        """
        fetch_calls: list = []

        async def _failing_fetch(task_id):
            fetch_calls.append(task_id)
            raise AssertionError("fetch must not be called for placeholder task_id")

        tracker = PrintTracker(self.reporter, self.checkpoint, _failing_fetch)

        for placeholder in (0, "0", None, ""):
            await tracker.handle_event({
                "command": "push_status",
                "gcode_state": "RUNNING",
                "task_id": placeholder,
                "layer_num": 5,
            })
            # Reset the state machine so the next placeholder also produces a
            # RUNNING transition — without the guard, _gcode_state would be
            # "RUNNING" after the first call and subsequent calls would be
            # no-ops, hiding the real bug.
            tracker._gcode_state = None

        assert fetch_calls == [], f"fetch was called with {fetch_calls!r}"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-spool-helper
python -m unittest tests.print_tracking.test_tracker.TestPrintTrackerResume.test_placeholder_task_id_skips_resume -v
```

Expected: failure — the AssertionError from `_failing_fetch` propagates (the resume `except Exception` catches it but the test's `fetch_calls` list is non-empty).

- [ ] **Step 3: Add the guard in `_attempt_print_resume`**

In `bambu-spool-helper/app/services/print_tracking/tracker.py`, around line 171, replace:

```python
    async def _attempt_print_resume(self, payload: dict) -> None:
        task_id = payload.get("task_id")
        subtask_id = payload.get("subtask_id")
        ams_mapping = payload.get("ams_mapping") or []
```

with:

```python
    async def _attempt_print_resume(self, payload: dict) -> None:
        task_id = payload.get("task_id")
        if task_id in (None, 0, "0", ""):
            # Bambu reports `task_id: 0` as the idle sentinel. Treating it as
            # a real print to resume would 404 on /current-job/file and jam
            # `_gcode_state = "RUNNING"`. Reset state and bail.
            logger.info("Ignoring resume for placeholder task_id=%r", task_id)
            self._gcode_state = None
            return
        subtask_id = payload.get("subtask_id")
        ams_mapping = payload.get("ams_mapping") or []
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-spool-helper
python -m unittest tests.print_tracking.test_tracker.TestPrintTrackerResume.test_placeholder_task_id_skips_resume -v
```

Expected: pass.

- [ ] **Step 5: Run the full spool-helper print-tracking suite to ensure nothing else broke**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-spool-helper
python -m unittest discover tests/print_tracking -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-spool-helper
git add app/services/print_tracking/tracker.py tests/print_tracking/test_tracker.py
git commit -m "$(cat <<'EOF'
fix(print-tracking): skip resume for placeholder task_id values

- Treat `task_id` of 0/"0"/None/"" as the Bambu idle sentinel and bail out of `_attempt_print_resume` before the gateway fetch. Without this
  guard a stale "RUNNING" snapshot with no real job 404'd on `/current-job/file` and jammed `_gcode_state = "RUNNING"`, silently
  suppressing FINISH detection for the rest of the session.
- Reset `_gcode_state` to None before bailing so a later legitimate RUNNING transition is re-evaluated rather than swallowed as "no change".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Spool-helper — reset `_gcode_state` on resume failure

**Files:**
- Modify: `bambu-spool-helper/app/services/print_tracking/tracker.py` (the `except` branch around line 209)
- Test: `bambu-spool-helper/tests/print_tracking/test_tracker.py`

- [ ] **Step 1: Write the failing test**

Append to `bambu-spool-helper/tests/print_tracking/test_tracker.py` (inside `class TestPrintTrackerResume`):

```python
    async def test_resume_failure_resets_gcode_state(self):
        """A failed fetch must reset _gcode_state so later transitions fire.

        Before this fix, a 3MF fetch failure left `_gcode_state = "RUNNING"`,
        so the subsequent FINISH transition was a no-op for tracking (the
        transition check `gcode_state != prev_state` saw equal values once
        the printer re-reported RUNNING, and FINISH then failed the
        `_active_model is not None` guard). Both project_file recovery on
        the next print AND in-flight tracking were dead until restart.
        """
        async def _broken_fetch(_task_id):
            raise RuntimeError("simulated 404")

        tracker = PrintTracker(self.reporter, self.checkpoint, _broken_fetch)

        await tracker.handle_event({
            "command": "push_status",
            "gcode_state": "RUNNING",
            "task_id": "REAL_TASK",
            "layer_num": 5,
        })

        assert tracker._active_model is None
        assert tracker._gcode_state is None, (
            "Expected _gcode_state to be reset after fetch failure, "
            f"got {tracker._gcode_state!r}"
        )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-spool-helper
python -m unittest tests.print_tracking.test_tracker.TestPrintTrackerResume.test_resume_failure_resets_gcode_state -v
```

Expected: failure on `assert tracker._gcode_state is None` — actual is `"RUNNING"`.

- [ ] **Step 3: Reset `_gcode_state` in the resume failure branch**

In `bambu-spool-helper/app/services/print_tracking/tracker.py`, around line 209-212, find:

```python
        except Exception:
            logger.exception("Failed to resume print; tracking disabled until next start")
            self._active_model = None
            return
```

and replace with:

```python
        except Exception:
            logger.exception("Failed to resume print; tracking disabled until next start")
            self._active_model = None
            # Reset so the next legitimate RUNNING/FINISH transition is
            # re-evaluated rather than swallowed as "no change". Without
            # this, a single failed resume permanently jams the state
            # machine until the helper restarts.
            self._gcode_state = None
            return
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-spool-helper
python -m unittest tests.print_tracking.test_tracker.TestPrintTrackerResume.test_resume_failure_resets_gcode_state -v
```

Expected: pass.

- [ ] **Step 5: Run the full spool-helper test suite**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-spool-helper
python -m unittest discover tests -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-spool-helper
git add app/services/print_tracking/tracker.py tests/print_tracking/test_tracker.py
git commit -m "$(cat <<'EOF'
fix(print-tracking): reset gcode_state on resume failure

- Clear `_gcode_state` in the `_attempt_print_resume` exception branch so a 3MF fetch failure no longer permanently jams the state machine.
- Previously, a single failed resume left the tracker convinced the printer was RUNNING; subsequent legitimate transitions were swallowed as
  "no change" and FINISH detection was a no-op (failed the `_active_model is not None` guard), silently disabling tracking for the rest of
  the session until restart.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: End-to-end smoke verification (no code change)

Once Tasks 1–5 are committed and built, verify against the live deployment.

- [ ] **Step 1: Build and deploy the gateway**

The user deploys with their own script. They run:

```bash
# user-driven; replace with your usual build/deploy
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-gateway
docker build -t <registry>/bambu-gateway:latest .
deploy-docker.sh bambu-gateway
```

Expected: gateway restarts cleanly at `http://10.0.1.9:4844`.

- [ ] **Step 2: Build and deploy the spool-helper**

```bash
cd /Users/leolobato/Documents/Projetos/Personal/3d/bambu_workspace/bambu-spool-helper
docker build -t <registry>/bambu-spool-helper:latest .
deploy-docker.sh bambu-spool-helper
```

Expected: spool-helper restarts cleanly at `http://10.0.1.9:9817`.

- [ ] **Step 3: Sample the SSE stream and verify aggregate snapshot**

```bash
curl -sN -m 5 -H 'Accept: text/event-stream' \
  'http://10.0.1.9:4844/api/printers/0309DA561103403/events' | head -10
```

Expected: the very first `event: snapshot` frame's data dict contains `gcode_state`, AMS info, temperatures — not just `wifi_signal`. (If the printer is mid-print, also expect `task_id`, `layer_num`, `ams_mapping`.)

- [ ] **Step 4: Verify `/current-job/file` no longer 404s mid-print**

When a print is active, run:

```bash
curl -s -o /dev/null -w 'HTTP %{http_code}\n' \
  'http://10.0.1.9:4844/api/printers/0309DA561103403/current-job/file'
```

Expected during an active print: HTTP 200 (file streams) or 502 (FTP/HTTP fetch error, but NOT 404). When no print is active or after FINISH: HTTP 404.

- [ ] **Step 5: Run a short test print and verify Spoolman decrements**

Snapshot a bound spool's `used_weight` before, run a short print to completion, then re-snapshot:

```bash
# Before:
curl -s 'http://10.0.1.9:7912/api/v1/spool' | \
  python3 -c "import json,sys;[print(s['id'], s['used_weight']) for s in json.load(sys.stdin) if not s.get('archived')]"

# ... start print, wait for FINISH ...

# After:
curl -s 'http://10.0.1.9:7912/api/v1/spool' | \
  python3 -c "import json,sys;[print(s['id'], s['used_weight']) for s in json.load(sys.stdin) if not s.get('archived')]"
```

Expected: the spool bound to the AMS tray used by the print has a non-zero `used_weight` delta, roughly matching the print's filament usage in grams.

- [ ] **Step 6: Check the spool-helper logs for the resume guard**

```bash
docker logs --since=10m bambu-spool-helper 2>&1 | grep -Ei 'placeholder|resume|print tracking|use_length' | tail -30
```

Expected: no `Failed to resume print` errors. If `Ignoring resume for placeholder task_id=0` appears, that's the new guard logging — confirms it's defending correctly.

---

## Self-Review

**Spec coverage:**

| Spec requirement | Plan task |
|---|---|
| `_recursive_merge` helper | Task 1 |
| Replace `_latest_print_payload` field with aggregate + project_file cache | Task 2 |
| Update `_on_message` to dispatch by command | Task 2 step 5 |
| Clear project_file cache on FINISH/FAILURE in `_update_status` | Task 2 step 6 |
| `latest_print_payload` returns aggregate (backwards-compat name) | Task 2 step 4 |
| Add `latest_project_file_payload` property | Task 2 step 4 |
| `printer_events` snapshot emits aggregate | Task 2 + Task 3 step 5 (no code change; new property semantics flow through) |
| `current_job_file` reads from new cache | Task 3 step 3 |
| Gateway tests for aggregate carry-forward, nested merge, project_file separation, FINISH clear | Task 1 + Task 2 step 1 |
| Gateway test: `/current-job/file` 404 after FINISH | Task 3 step 1 |
| Gateway test: SSE snapshot contains aggregate | Implicit: existing `test_events_emits_snapshot_then_reports` exercises that the snapshot equals whatever `latest_print_payload` returns; combined with Task 2's unit tests of the aggregate, full coverage |
| Spool-helper: skip resume for placeholder task_id | Task 4 |
| Spool-helper: reset `_gcode_state` on resume failure | Task 5 |
| Spool-helper tests for both | Task 4 + Task 5 |
| End-to-end verification | Task 6 |

No gaps.

**Placeholder scan:** No TBDs, no "implement later", no "similar to Task N". All code blocks are complete.

**Type / name consistency:** `_aggregate_print_state`, `_latest_project_file_payload`, `latest_print_payload`, `latest_project_file_payload` used consistently across all tasks. `_recursive_merge` signature matches between Task 1 definition and Task 2 call. `_attempt_print_resume` and `_gcode_state` match between Task 4 and Task 5.

---

Plan complete and saved to `bambu-gateway/docs/superpowers/plans/2026-05-15-sse-aggregate-snapshot.md`.
