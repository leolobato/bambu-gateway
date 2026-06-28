# Print Sessions (Gateway) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a gateway capability for an agent to configure a print from a model (3MF/STL) and get a handoff URL, with a separate explicitly-gated action to start the print.

**Architecture:** A new `POST /api/print-sessions` resolves config from a `printer_id` (machine → process/filament defaults via `resolve-for-machine`, AMS-aware), turns the model into a slicer `input_token` (3MF upload, or STL draft→materialize), creates a slice job (sliced now if `slice:true`, else persisted unsliced), and returns `{job_id, sliced, handoff_url}` reusing the existing `/print?reprint=<id>` rehydration. A separate `POST /api/print-sessions/{id}/print` starts a sliced job on its printer, gated and logged. Per-printer `default_plate_type` becomes the plate default everywhere the printer is targeted. The reprint-config gains `filament_overrides`.

**Tech Stack:** FastAPI + httpx (backend), pytest; React + TypeScript + vitest (web). Backend tests: `.venv/bin/pytest`. Web tests: `cd web && npx vitest run`; build `cd web && npm run build`.

## Global Constraints

- `POST /api/print-sessions` NEVER prints. Printing is exclusively `POST /api/print-sessions/{id}/print`. Auto-print as a side effect of configuration is excluded.
- Plate-type precedence (first match wins): request `plate_type` → printer `default_plate_type` → 3MF authored plate → machine default.
- "Printer only" defaulting: from `printer_id` derive machine; default process + filament via `resolve-for-machine` (AMS-aware); any agent-supplied field/slot wins.
- Slot index space for `filament_profiles` / `filament_overrides` is the dense filament position (consistent with the existing slice-job contract). Forward overrides verbatim.
- `handoff_url = {base}/print?reprint=<job_id>`, `base = PUBLIC_BASE_URL` if set else the request base URL.
- New config: `PUBLIC_BASE_URL` (default `""`), `ALLOW_AGENT_PRINT` (default `True`). `/print` returns 403 when disabled, 409 when the session has no sliced output.
- No new dependencies. Mirror existing patterns. Commit messages: subject < 60 chars; body bullets wrapped at 140; backtick code identifiers; end with the repo's Co-Authored-By / Claude-Session trailers. Work on `main`.

---

### Task 1: Per-printer `default_plate_type` (config, store, CRUD)

**Files:**
- Modify: `app/config.py` (`PrinterConfig`)
- Modify: `app/config_store.py` (`_serialize`, `_deserialize`)
- Modify: `app/models.py` (`PrinterConfigInput`, `PrinterConfigResponse`)
- Modify: `app/main.py` (the printer CRUD: create/update build `PrinterConfig`; the GET/response builders at the `machine_model=cfg.machine_model` sites)
- Test: `tests/test_default_plate_type_config.py` (new)

**Interfaces:**
- Produces: `PrinterConfig.default_plate_type: str = ""`, round-tripped through `printers.json`; `PrinterConfigInput.default_plate_type` / `PrinterConfigResponse.default_plate_type`; CRUD endpoints accept + echo it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_default_plate_type_config.py
from app.config import PrinterConfig
from app.config_store import _serialize, _deserialize


def test_default_plate_type_round_trips():
    cfg = PrinterConfig(ip="1.1.1.1", access_code="x", serial="S1",
                        name="P", machine_model="GM020",
                        default_plate_type="textured_pei_plate")
    out = _deserialize(_serialize([cfg]))[0]
    assert out.default_plate_type == "textured_pei_plate"


def test_default_plate_type_defaults_empty_and_legacy_loads():
    assert PrinterConfig(ip="i", access_code="a", serial="s").default_plate_type == ""
    # legacy printers.json entry without the key still loads
    legacy = [{"serial": "s", "ip": "i", "access_code": "a", "name": "n"}]
    assert _deserialize(legacy)[0].default_plate_type == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_default_plate_type_config.py -v`
Expected: FAIL (`PrinterConfig` has no `default_plate_type`).

- [ ] **Step 3: Implement**

3a. `app/config.py` — add the field after `machine_model`:
```python
    machine_model: str = ""
    default_plate_type: str = ""
```

3b. `app/config_store.py` `_serialize` — after the `machine_model` block:
```python
        if c.machine_model:
            d["machine_model"] = c.machine_model
        if c.default_plate_type:
            d["default_plate_type"] = c.default_plate_type
```
and `_deserialize` — add the kwarg:
```python
            machine_model=item.get("machine_model", ""),
            default_plate_type=item.get("default_plate_type", ""),
```

3c. `app/models.py` — add to both models beside `machine_model`:
```python
    machine_model: str = ""
    default_plate_type: str = ""
```

3d. `app/main.py` — at each `machine_model=cfg.machine_model` response-builder site (around lines 314, 2534) add `default_plate_type=cfg.default_plate_type,`; in the create (`create_printer_config`) and update (`update_printer_config`) handlers, where `PrinterConfig(... machine_model=body.machine_model ...)` is built, add `default_plate_type=body.default_plate_type,`. Read each site first to match its exact shape.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_default_plate_type_config.py -v`
Expected: PASS.

- [ ] **Step 5: Run focused regression on printer settings**

Run: `.venv/bin/pytest tests/ -k "printer and (config or settings)" -q`
Expected: PASS (existing printer CRUD tests unaffected).

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/config_store.py app/models.py app/main.py tests/test_default_plate_type_config.py
git commit -m "feat: per-printer default_plate_type config + CRUD"
```

---

### Task 2: `filament_overrides` in reprint-config + web rehydration

**Files:**
- Modify: `app/models.py` (`SliceJobReprintConfig`)
- Modify: `app/main.py` (`get_slice_job_reprint_config`)
- Modify: `web/src/lib/api/types.ts` (the reprint-config type)
- Modify: `web/src/routes/print.tsx` (`rehydrateFromJob`)
- Test: `tests/test_reprint_config_filament_overrides.py` (new)

**Interfaces:**
- Consumes: `SliceJob.filament_overrides` (already persisted).
- Produces: `SliceJobReprintConfig.filament_overrides: dict[str, dict[str, str]] | None = None`; the print page restores it via `setFilamentOverride`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reprint_config_filament_overrides.py
import pytest
from httpx import AsyncClient, ASGITransport
import app.main as app_main


@pytest.mark.asyncio
async def test_reprint_config_includes_filament_overrides(tmp_path, monkeypatch):
    # Build a manager with one job carrying filament_overrides, injected as the
    # module-level `slice_jobs`. Mirror the harness in
    # tests/test_slice_job_manager.py for constructing a SliceJobManager.
    from app.slice_jobs import SliceJob

    class _Stub:
        async def get(self, job_id):
            blob = tmp_path / "in.3mf"; blob.write_bytes(b"x")
            return SliceJob.new(
                filename="a.3mf", machine_profile="GM020", process_profile="GP109",
                filament_profiles=["GFL99"], plate_id=0, plate_type="",
                project_filament_count=1, printer_id=None, auto_print=False,
                input_path=blob,
                filament_overrides={"0": {"nozzle_temperature": "230"}},
            )
    monkeypatch.setattr(app_main, "slice_jobs", _Stub())
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/slice-jobs/anyid/reprint-config")
    assert r.status_code == 200
    assert r.json()["filament_overrides"] == {"0": {"nozzle_temperature": "230"}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_reprint_config_filament_overrides.py -v`
Expected: FAIL (`filament_overrides` absent from the response).

- [ ] **Step 3: Implement backend**

3a. `app/models.py` `SliceJobReprintConfig` — add after `process_overrides`:
```python
    process_overrides: dict[str, str] | None = None
    filament_overrides: dict[str, dict[str, str]] | None = None
```

3b. `app/main.py` `get_slice_job_reprint_config` — add to the `SliceJobReprintConfig(...)` construction:
```python
        process_overrides=job.process_overrides,
        filament_overrides=job.filament_overrides,
```

- [ ] **Step 4: Run backend test**

Run: `.venv/bin/pytest tests/test_reprint_config_filament_overrides.py -v`
Expected: PASS.

- [ ] **Step 5: Implement web rehydration**

5a. `web/src/lib/api/types.ts` — on the reprint-config interface (the one with `process_overrides`), add:
```ts
  filament_overrides?: Record<string, Record<string, string>> | null;
```

5b. `web/src/routes/print.tsx` `rehydrateFromJob` — after the existing `process_overrides` restore loop and a `resetAllFilamentOverrides()` call (destructure `resetAllFilamentOverrides` and `setFilamentOverride` from `usePrintContext()` if not already), add:
```tsx
        resetAllFilamentOverrides();
        for (const [slotStr, keys] of Object.entries(config.filament_overrides ?? {})) {
          const slot = Number(slotStr);
          for (const [key, value] of Object.entries(keys)) {
            setFilamentOverride(slot, key, value);
          }
        }
```
Add `resetAllFilamentOverrides` and `setFilamentOverride` to the `useCallback` dependency array of `rehydrateFromJob`.

- [ ] **Step 6: Verify web compiles + test**

Run: `cd web && npx tsc --noEmit` → clean.
Run: `cd web && npx vitest run src/routes/print.test.tsx` → PASS (update the test only if it asserts the restore set).

- [ ] **Step 7: Commit**

```bash
git add app/models.py app/main.py web/src/lib/api/types.ts web/src/routes/print.tsx tests/test_reprint_config_filament_overrides.py
git commit -m "feat: carry filament_overrides through reprint config"
```

---

### Task 3: Configured-but-unsliced job (`submit(enqueue=False)`)

**Files:**
- Modify: `app/slice_jobs.py` (`SliceJobManager.submit`)
- Test: `tests/test_slice_job_enqueue_flag.py` (new)

**Interfaces:**
- Produces: `SliceJobManager.submit(..., enqueue: bool = True)`. When `False`, the job is persisted (input blob + record) and returned WITHOUT being placed on the slice queue (`has_output` stays false).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slice_job_enqueue_flag.py
import pytest
from app.slice_jobs import SliceJobManager, SliceJobStore


def _manager(tmp_path):
    store = SliceJobStore(tmp_path / "jobs.json")
    return SliceJobManager(store=store, slicer=object(), printer_service=None)


@pytest.mark.asyncio
async def test_submit_enqueue_false_does_not_queue(tmp_path):
    mgr = _manager(tmp_path)
    job = await mgr.submit(
        file_data=b"x", filename="a.3mf", machine_profile="GM020",
        process_profile="GP109", filament_profiles=["GFL99"], plate_id=0,
        plate_type="", project_filament_count=1, printer_id=None,
        auto_print=False, enqueue=False,
    )
    assert mgr._queue.empty()
    assert (await mgr.get(job.id)) is not None
    assert job.output_path is None


@pytest.mark.asyncio
async def test_submit_default_enqueues(tmp_path):
    mgr = _manager(tmp_path)
    await mgr.submit(
        file_data=b"x", filename="a.3mf", machine_profile="GM020",
        process_profile="GP109", filament_profiles=["GFL99"], plate_id=0,
        plate_type="", project_filament_count=1, printer_id=None,
        auto_print=False,
    )
    assert not mgr._queue.empty()
```

Read `tests/test_slice_job_manager.py` first and match the exact `SliceJobManager`/`SliceJobStore` constructor signatures used there (adjust the `_manager` helper if they differ).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_slice_job_enqueue_flag.py -v`
Expected: FAIL (`submit() got an unexpected keyword argument 'enqueue'`).

- [ ] **Step 3: Implement**

In `app/slice_jobs.py` `submit`, add `enqueue: bool = True` to the signature (after `auto_center`), and guard the queue put at the end:
```python
        await self._store.upsert(job)
        self._cancel_events[job.id] = asyncio.Event()
        if enqueue:
            await self._queue.put(job.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_slice_job_enqueue_flag.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/slice_jobs.py tests/test_slice_job_enqueue_flag.py
git commit -m "feat: allow persisting a slice job without enqueuing"
```

---

### Task 4: Config + session config resolver (printer → defaults)

**Files:**
- Modify: `app/config.py` (`Settings`: `public_base_url`, `allow_agent_print`)
- Create: `app/print_sessions.py` (pure resolver helpers)
- Test: `tests/test_print_session_resolve.py` (new)

**Interfaces:**
- Produces:
  - `Settings.public_base_url: str = ""`, `Settings.allow_agent_print: bool = True`.
  - `resolve_plate_type(request_plate: str, printer_default: str, authored_plate: str, machine_default: str) -> str` — first non-empty in that order.
  - `build_handoff_url(base: str, job_id: str) -> str` → `f"{base.rstrip('/')}/print?reprint={job_id}"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_print_session_resolve.py
from app.print_sessions import resolve_plate_type, build_handoff_url


def test_plate_precedence_request_wins():
    assert resolve_plate_type("cool_plate", "textured_pei_plate", "smooth_plate", "x") == "cool_plate"

def test_plate_precedence_printer_default_over_authored():
    assert resolve_plate_type("", "textured_pei_plate", "smooth_plate", "x") == "textured_pei_plate"

def test_plate_precedence_authored_over_machine():
    assert resolve_plate_type("", "", "smooth_plate", "machine_def") == "smooth_plate"

def test_plate_precedence_machine_default_last():
    assert resolve_plate_type("", "", "", "machine_def") == "machine_def"

def test_build_handoff_url():
    assert build_handoff_url("https://g.example/", "ab12") == "https://g.example/print?reprint=ab12"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_print_session_resolve.py -v`
Expected: FAIL (module/functions absent).

- [ ] **Step 3: Implement**

3a. `app/config.py` `Settings` — add fields (match the existing pydantic-settings style, e.g. beside `orcaslicer_api_url`):
```python
    public_base_url: str = ""
    allow_agent_print: bool = True
```

3b. Create `app/print_sessions.py`:
```python
"""Pure helpers for the print-session (agent handoff) endpoints."""
from __future__ import annotations


def resolve_plate_type(
    request_plate: str, printer_default: str, authored_plate: str, machine_default: str,
) -> str:
    """First non-empty of request → printer default → authored → machine default."""
    for candidate in (request_plate, printer_default, authored_plate, machine_default):
        if candidate:
            return candidate
    return ""


def build_handoff_url(base: str, job_id: str) -> str:
    return f"{base.rstrip('/')}/print?reprint={job_id}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_print_session_resolve.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/print_sessions.py tests/test_print_session_resolve.py
git commit -m "feat: print-session config + plate/url resolvers"
```

---

### Task 5: `POST /api/print-sessions` — create (3MF + STL)

**Files:**
- Modify: `app/main.py` (new endpoint + response model import)
- Modify: `app/models.py` (`PrintSessionResponse`)
- Test: `tests/test_print_sessions_create.py` (new)

**Interfaces:**
- Consumes: `resolve_plate_type`/`build_handoff_url` (Task 4), `SliceJobManager.submit(..., enqueue=...)` (Task 3), `PrinterConfig.default_plate_type` (Task 1), `slicer_client.resolve_for_machine`, `slicer_client.import_stl_draft`/`materialize_stl_draft`, `slicer_client.upload_3mf`, `parse_3mf_via_slicer`, `validate_selected_trays`, `build_slicer_filament_payload`, the printer config lookup by id.
- Produces: `POST /api/print-sessions` → `PrintSessionResponse {job_id, sliced, handoff_url}`. `PrintSessionResponse` Pydantic model.

**This is the centerpiece. Read these before coding** (do not invent parallel logic):
- `create_slice_job` in `app/main.py` — how it parses `filament_profiles`/overrides, uploads/parses a 3MF, and calls `slice_jobs.submit(...)`. Mirror its model-handling and submit call.
- `materialize_stl_draft` endpoint (`POST /api/stl-drafts/{token}/3mf`) and `slicer_client.import_stl_draft` — the STL → `input_token` path.
- `web/src/routes/print.tsx` `resolveForMachine` usage and the print_file machine-defaulting — how `resolve_for_machine` output maps to a process `setting_id` and per-slot filament `setting_id`s. The endpoint replicates this defaulting server-side.
- The printer lookup: map `printer_id` → its `PrinterConfig` (and `machine_model`, `default_plate_type`). Use the same lookup the printer endpoints use.

- [ ] **Step 1: Add the response model**

`app/models.py`:
```python
class PrintSessionResponse(BaseModel):
    job_id: str
    sliced: bool
    handoff_url: str
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_print_sessions_create.py
import pytest
from httpx import AsyncClient, ASGITransport
import app.main as app_main


@pytest.mark.asyncio
async def test_print_session_unknown_printer_400(monkeypatch):
    # slice_jobs + slicer_client present, but printer_id not found
    monkeypatch.setattr(app_main, "slice_jobs", object())
    monkeypatch.setattr(app_main, "slicer_client", object())
    monkeypatch.setattr(app_main, "_printer_config_by_id", lambda pid: None, raising=False)
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/print-sessions", data={"printer_id": "nope"},
                         files={"file": ("m.3mf", b"PK\x03\x04", "model/3mf")})
    assert r.status_code == 400
```

Extend this file during Step 4 with a happy-path 3MF test that injects fakes for `slicer_client` (stubbing `upload_3mf`, `resolve_for_machine`) and a fake `slice_jobs` capturing the `submit(...)` kwargs, asserting: the resolved `plate_type` follows precedence (printer `default_plate_type` over authored), `enqueue=False` when `slice` omitted, and the response `handoff_url` ends with `/print?reprint=<job_id>`. Mirror the fake-injection style used in `tests/test_slice_jobs_api.py`.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_print_sessions_create.py -v`
Expected: FAIL (404/route missing or helper absent).

- [ ] **Step 4: Implement the endpoint**

Add to `app/main.py` (place near `create_slice_job`). Implement in this order inside the handler:
1. Guard `slice_jobs`/`slicer_client` configured (mirror `create_slice_job`'s guards).
2. Look up the printer by `printer_id`; 400 if missing. Derive `machine = machine_profile or printer.machine_model`; 400 if still empty.
3. Parse `filament_profiles`, `process_overrides` (`_parse_process_overrides_form`), `filament_overrides` (`_parse_filament_overrides_form`).
4. Resolve defaults via `slicer_client.resolve_for_machine(machine_id=machine, ...)`: pick `process_profile or resolved.process.setting_id`; for unspecified filament slots use the resolved per-slot filament `setting_id`s. Agent-supplied process/filament/slots win. (Follow the web's `resolveForMachine` mapping.)
5. Model → `input_token` + authored plate:
   - `.3mf`: `parse_3mf_via_slicer` (read authored `plate_type`), `upload_3mf` → token.
   - `.stl`: `import_stl_draft(machine, process)` → `materialize_stl_draft` → token; authored plate = "".
6. `plate_type = resolve_plate_type(request_plate, printer.default_plate_type, authored_plate, resolved_machine_default_plate)`.
7. Validate AMS trays (`validate_selected_trays`) → 400 on failure.
8. `enqueue = bool(slice_flag)`; `job = await slice_jobs.submit(... input via file_data/token ..., plate_type=plate_type, process_overrides=..., filament_overrides=..., enqueue=enqueue)`. (Match `create_slice_job`'s submit call shape; reuse its token/file handling.)
9. `base = settings.public_base_url or str(request.base_url)`; `return PrintSessionResponse(job_id=job.id, sliced=enqueue, handoff_url=build_handoff_url(base, job.id))`.

Add a small `_printer_config_by_id(printer_id)` helper if one doesn't already exist (reuse the existing printer-lookup the printer endpoints use). Errors: STL import failure → 422; slicer unreachable → 502 (let `SlicingError` map as elsewhere).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_print_sessions_create.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/models.py tests/test_print_sessions_create.py
git commit -m "feat: POST /api/print-sessions configures a print and returns a handoff URL"
```

---

### Task 6: `GET /api/print-sessions/{id}` + `POST /api/print-sessions/{id}/print`

**Files:**
- Modify: `app/main.py` (two endpoints; extract `_print_finished_job` helper from `print_file`'s job branch)
- Modify: `app/models.py` (`PrintSessionStatus`)
- Test: `tests/test_print_sessions_status_print.py` (new)

**Interfaces:**
- Consumes: `slice_jobs.get`, `settings.allow_agent_print`, `settings.public_base_url`, the existing print-from-job logic in `print_file`.
- Produces:
  - `GET /api/print-sessions/{id}` → `PrintSessionStatus {job_id, status, sliced, printer_id, handoff_url}`.
  - `POST /api/print-sessions/{id}/print` → starts the job; 403 if `allow_agent_print` is false; 409 if not sliced; 404 if unknown.

- [ ] **Step 1: Add the status model**

`app/models.py`:
```python
class PrintSessionStatus(BaseModel):
    job_id: str
    status: str
    sliced: bool
    printer_id: str | None = None
    handoff_url: str
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_print_sessions_status_print.py
import pytest
from httpx import AsyncClient, ASGITransport
import app.main as app_main


@pytest.mark.asyncio
async def test_print_action_403_when_disabled(monkeypatch):
    monkeypatch.setattr(app_main.settings, "allow_agent_print", False, raising=False)
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/print-sessions/anyid/print")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_print_action_409_when_not_sliced(tmp_path, monkeypatch):
    from app.slice_jobs import SliceJob
    monkeypatch.setattr(app_main.settings, "allow_agent_print", True, raising=False)
    class _Stub:
        async def get(self, jid):
            blob = tmp_path / "in.3mf"; blob.write_bytes(b"x")
            return SliceJob.new(filename="a.3mf", machine_profile="GM020",
                process_profile="GP109", filament_profiles=["GFL99"], plate_id=0,
                plate_type="", project_filament_count=1, printer_id="p1",
                auto_print=False, input_path=blob)  # no output_path → unsliced
    monkeypatch.setattr(app_main, "slice_jobs", _Stub())
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/print-sessions/anyid/print")
    assert r.status_code == 409
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_print_sessions_status_print.py -v`
Expected: FAIL (routes missing).

- [ ] **Step 4: Extract the print helper**

Read `print_file`'s job branch (`app/main.py` ~1929-1995): the part that, given a sliced `job`, builds the AMS mapping and calls `printer_service.submit_print(...)` with `job.output_path`. Extract it into a module-level async helper `_print_finished_job(job, printer_id)` that both `print_file` and the new action call. Replace the inline code in `print_file` with a call to the helper (keep `print_file`'s behavior identical — verify by running its existing tests). If extraction looks risky against `print_file`'s surrounding flow, STOP and report DONE_WITH_CONCERNS rather than altering `print_file`'s behavior.

- [ ] **Step 5: Implement the endpoints**

```python
@app.get("/api/print-sessions/{session_id}", response_model=PrintSessionStatus)
async def get_print_session(session_id: str, request: Request):
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(session_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Session not found")
    sliced = bool(job.output_path and Path(job.output_path).exists())
    base = settings.public_base_url or str(request.base_url)
    return PrintSessionStatus(
        job_id=job.id, status=job.status.value, sliced=sliced,
        printer_id=job.printer_id, handoff_url=build_handoff_url(base, job.id),
    )


@app.post("/api/print-sessions/{session_id}/print")
async def start_print_session(session_id: str):
    if not settings.allow_agent_print:
        raise HTTPException(status_code=403, detail="Agent printing is disabled")
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(session_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not (job.output_path and Path(job.output_path).exists()):
        raise HTTPException(status_code=409, detail="Session is not sliced yet")
    logger.info("agent print start: session=%s printer=%s", job.id, job.printer_id)
    await _print_finished_job(job, job.printer_id)
    return {"job_id": job.id, "status": "printing"}
```

- [ ] **Step 6: Run tests + print_file regression**

Run: `.venv/bin/pytest tests/test_print_sessions_status_print.py tests/ -k "print" -q`
Expected: PASS (new tests green; existing print/print_file tests unaffected by the extraction).

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/models.py tests/test_print_sessions_status_print.py
git commit -m "feat: print-session status + gated start-print action"
```

---

### Task 7: Web — printer default plate in import flow + Settings UI

**Files:**
- Modify: `web/src/routes/print.tsx` (plate default from active printer)
- Modify: the Settings printer form component (add a plate-type select) — locate via `grep -rn "machine_model" web/src/components/settings`
- Modify: `web/src/lib/api/types.ts` / printers API types (add `default_plate_type`)
- Test: `web/src/routes/print.test.tsx` (extend) or a focused new test

**Interfaces:**
- Consumes: `default_plate_type` on the printer objects (Task 1 backend); the plate-type precedence from the spec.
- Produces: the web import flow seeds `plate_type` from the active printer's `default_plate_type` (above the 3MF authored plate); Settings UI can edit it.

- [ ] **Step 1: Write the failing test**

Add a test asserting that when the active printer has `default_plate_type: "textured_pei_plate"`, the print settings default `plateType` to it rather than the imported 3MF's authored plate. Mirror the provider/mock harness in `print.test.tsx`. (Read `print.test.tsx` for how it seeds printers + imports a file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/routes/print.test.tsx`
Expected: FAIL (plate defaults to authored, not printer default).

- [ ] **Step 3: Implement**

3a. Add `default_plate_type?: string` to the printer type in `web/src/lib/api/types.ts` (and the printers API adapter if it maps fields explicitly).

3b. In `print.tsx`, where `plateType` is defaulted after import / `resolveForMachine`, insert the printer default as a higher-precedence default than the authored plate: when `activePrinter?.default_plate_type` is set and the user hasn't overridden it, seed `settings.plateType` from it. Match the existing defaulting effect's structure (don't fight the `resolveForMachine` apply — the printer default should win over the authored/resolved plate, mirroring the backend precedence).

3c. In the Settings printer form, add a plate-type `<Select>` (options from the slicer plate-types catalogue — reuse whatever query the print page uses for plate types; empty option = "Use file's plate"), bound to `default_plate_type` in the create/update payload.

- [ ] **Step 4: Run test + build**

Run: `cd web && npx vitest run src/routes/print.test.tsx` → PASS.
Run: `cd web && npm run build` → clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/routes/print.tsx web/src/lib/api/types.ts web/src/components/settings
git commit -m "feat: default plate to the active printer's plate in print UI"
```

---

### Task 8: End-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Backend suite** — `.venv/bin/pytest -q` → PASS.
- [ ] **Step 2: Web suite + build** — `cd web && npm test && npm run build` → PASS + clean.
- [ ] **Step 3: Manual smoke (if a slicer + printer are reachable):** `python -m app`; `POST /api/print-sessions` with a small STL + a `printer_id`, `slice=true`; confirm the response `handoff_url` opens the print page in previewReady with the printer's plate applied; confirm `GET /api/print-sessions/{id}` reports `sliced:true`; with `ALLOW_AGENT_PRINT=false`, confirm `/print` returns 403.
- [ ] **Step 4: Final commit (if verification fixes were needed)** — `git add -A && git commit -m "test: verify print-sessions end-to-end"`.

---

## Self-Review

**Spec coverage:**
- `POST /api/print-sessions` create (3MF+STL, defaulting, plate precedence, AMS validation, handoff URL) → Task 5 (with Tasks 1/3/4 as deps). ✓
- `GET /api/print-sessions/{id}` + gated `POST .../print` → Task 6. ✓
- Per-printer `default_plate_type` config/CRUD → Task 1; applied in web import flow + Settings UI → Task 7; applied in create endpoint → Task 5 precedence. ✓
- Configured-but-unsliced job → Task 3. ✓
- `filament_overrides` in reprint config + web rehydration → Task 2. ✓
- `PUBLIC_BASE_URL` + `ALLOW_AGENT_PRINT` → Task 4 (config) + used in Tasks 5/6. ✓
- "Never prints on create" / separate gated print → Tasks 5 (no print) + 6 (gate). ✓
- Testing → each task + Task 8. ✓
- Trust model is documentation-level (enforced by the MCP sub-project); the gateway's part (separate authenticated/logged/disable-able action) → Task 6. ✓

**Placeholder scan:** No TBD/TODO. Tasks 5 and 6 contain prose "read X then mirror it" steps for the genuinely integration-heavy parts (server-side defaulting that mirrors the web's `resolveForMachine`; extracting the print helper from `print_file`). These cite exact files/regions and give explicit acceptance criteria + a STOP/escalate instruction rather than hand-waving — appropriate for code that must match an existing non-trivial path rather than be invented.

**Type consistency:** `default_plate_type` (Py/TS), `enqueue` flag, `resolve_plate_type`/`build_handoff_url` signatures, `PrintSessionResponse {job_id, sliced, handoff_url}` and `PrintSessionStatus {job_id, status, sliced, printer_id, handoff_url}` are consistent across the tasks that produce and consume them.

**Note for the implementer (Tasks 5/6):** the server-side `resolve-for-machine` defaulting and the `_print_finished_job` extraction are the two judgment-heavy spots — follow the cited existing code exactly, and escalate rather than invent if the existing path doesn't map cleanly.
