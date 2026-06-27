# Per-slot Filament Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users override individual filament-domain settings (e.g. nozzle temperature, max volumetric speed) per filament slot at slice time, through the gateway API and a dedicated "Filament settings" web card.

**Architecture:** Mirror the existing `process_overrides` feature end-to-end, adding a per-slot dimension. The slicer (orcaslicer-headless) already accepts `filament_overrides` on `/slice/v2`, `/slice-stream/v2`, and `/3mf/{token}/prepare`, reports `settings_transfer.filament_overrides_applied`, and exposes `/options/filament` + `/options/filament/layout`. We thread a `filament_overrides` dict through `slicer_client → slice_jobs → main.py` endpoints, add catalogue/baseline proxy endpoints, and build a slot-aware editor card on top of the existing process-editor components.

**Tech Stack:** FastAPI + httpx (backend), pytest; React + TypeScript + TanStack Query + Vite + vitest (web). Project venv at `.venv/`; run tests with `.venv/bin/pytest`. Web tests: `cd web && npm test`.

## Global Constraints

- Slot index space == `filament_settings_ids` positions (position 0 = first project filament). Outer keys of `filament_overrides` are int-like strings; inner objects are `{string_key: string_value}`. Values are OrcaSlicer config-strings.
- `filament_overrides` is forwarded verbatim — the slicer drops unknown/non-filament/out-of-range entries. The gateway validates shape only (object-of-objects-of-strings), never project-filament context. Empty `{}` == no-op (do not send the field).
- Mirror `process_overrides` naming and structure exactly; new names use the `filament_overrides` / `filamentOverrides` analog.
- No new dependencies. Follow existing file patterns.
- Commit messages: subject < 60 chars; body bullets wrapped at 140 chars; wrap class/function names in backticks. End with the Co-Authored-By / Claude-Session trailers used in this repo.
- Work on `main` (no feature branch).

---

### Task 1: `SlicerClient` slice plumbing for `filament_overrides`

**Files:**
- Modify: `app/slicer_client.py` (`SliceResult`, `_slice_result_from_v2`, `slice`, `slice_stream`, `_slice_stream_real`, `_slice_stream_fallback`, `_build_v2_slice_body`, `prepare_3mf_token`)
- Test: `tests/test_slicer_client_filament_overrides.py` (new)

**Interfaces:**
- Consumes: existing `_build_v2_slice_body` keyword-only signature.
- Produces:
  - `SliceResult.filament_overrides_applied: list[dict]` (default `[]`).
  - `slice(..., filament_overrides: dict[str, dict[str, str]] | None = None)` and the same new kwarg on `slice_stream`, `_build_v2_slice_body`, `prepare_3mf_token`.
  - Slice body gains `"filament_overrides"` key only when the dict is non-empty.

- [ ] **Step 1: Write the failing test**

Create `tests/test_slicer_client_filament_overrides.py`:

```python
import pytest

from app.slicer_client import SlicerClient, _slice_result_from_v2


def test_slice_result_surfaces_filament_overrides_applied():
    payload = {
        "settings_transfer": {
            "status": "applied",
            "filament_overrides_applied": [
                {"slot": 0, "key": "nozzle_temperature", "value": "230", "previous": "220"},
            ],
        },
    }
    result = _slice_result_from_v2(payload, b"sliced")
    assert result.filament_overrides_applied == [
        {"slot": 0, "key": "nozzle_temperature", "value": "230", "previous": "220"},
    ]


def test_slice_result_defaults_filament_overrides_applied_empty():
    result = _slice_result_from_v2({"settings_transfer": {"status": "applied"}}, b"x")
    assert result.filament_overrides_applied == []


@pytest.mark.asyncio
async def test_build_v2_body_includes_filament_overrides_when_present(monkeypatch):
    client = SlicerClient("http://slicer")

    async def fake_norm(token, fp, *, machine_profile=None):
        return ["GFL99"], None

    async def fake_center(token, machine):
        return False

    monkeypatch.setattr(client, "_normalize_filament_selection", fake_norm)
    monkeypatch.setattr(client, "should_auto_center_for_machine", fake_center)

    body = await client._build_v2_slice_body(
        input_token="t", machine_profile="GM020", process_profile="GP109",
        filament_profiles=["GFL99"], plate=1,
        filament_overrides={"0": {"nozzle_temperature": "230"}},
    )
    assert body["filament_overrides"] == {"0": {"nozzle_temperature": "230"}}


@pytest.mark.asyncio
async def test_build_v2_body_omits_empty_filament_overrides(monkeypatch):
    client = SlicerClient("http://slicer")

    async def fake_norm(token, fp, *, machine_profile=None):
        return ["GFL99"], None

    async def fake_center(token, machine):
        return False

    monkeypatch.setattr(client, "_normalize_filament_selection", fake_norm)
    monkeypatch.setattr(client, "should_auto_center_for_machine", fake_center)

    for empty in (None, {}):
        body = await client._build_v2_slice_body(
            input_token="t", machine_profile="GM020", process_profile="GP109",
            filament_profiles=["GFL99"], plate=1, filament_overrides=empty,
        )
        assert "filament_overrides" not in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_slicer_client_filament_overrides.py -v`
Expected: FAIL (`SliceResult` has no `filament_overrides_applied`; `_build_v2_slice_body` has no `filament_overrides` kwarg).

- [ ] **Step 3: Implement**

In `app/slicer_client.py`:

3a. Add the field to `SliceResult`:

```python
@dataclass
class SliceResult:
    content: bytes
    settings_transfer_status: str = ""
    settings_transferred: list[dict] = field(default_factory=list)
    filament_transfers: list[dict] = field(default_factory=list)
    estimate: PrintEstimate | None = None
    process_overrides_applied: list[dict] = field(default_factory=list)
    filament_overrides_applied: list[dict] = field(default_factory=list)
```

3b. In `_slice_result_from_v2`, read the new list and pass it:

```python
    overrides_applied = transfer.get("process_overrides_applied") or []
    filament_overrides_applied = transfer.get("filament_overrides_applied") or []
    return SliceResult(
        content=sliced_bytes,
        settings_transfer_status=status,
        settings_transferred=[],
        filament_transfers=filament_slots if isinstance(filament_slots, list) else [],
        estimate=_decode_print_estimate_dict(payload.get("estimate")),
        process_overrides_applied=(
            list(overrides_applied) if isinstance(overrides_applied, list) else []
        ),
        filament_overrides_applied=(
            list(filament_overrides_applied)
            if isinstance(filament_overrides_applied, list) else []
        ),
    )
```

3c. Add `filament_overrides: dict[str, dict[str, str]] | None = None` as a new keyword arg to `slice`, `slice_stream`, `_slice_stream_real`, `_slice_stream_fallback`, `_build_v2_slice_body`, and `prepare_3mf_token`. In each, forward it to the next call. Mirror exactly how `process_overrides` is forwarded. In `slice`, pass it into `_build_v2_slice_body(...)`. In `slice_stream`, forward to both `_slice_stream_real` and `_slice_stream_fallback`. In `_slice_stream_fallback`, forward to `self.slice(...)`.

3d. In `_build_v2_slice_body`, after the `process_overrides` block:

```python
        if process_overrides:
            body["process_overrides"] = dict(process_overrides)
        if filament_overrides:
            body["filament_overrides"] = {
                slot: dict(keys) for slot, keys in filament_overrides.items()
            }
        return body
```

3e. In `prepare_3mf_token`, after the `process_overrides` block:

```python
        if process_overrides:
            body["process_overrides"] = dict(process_overrides)
        if filament_overrides:
            body["filament_overrides"] = {
                slot: dict(keys) for slot, keys in filament_overrides.items()
            }
```

3f. In `_slice_stream_fallback`, include the new field in the synthetic `result` event's `transfer_info`:

```python
        if result.process_overrides_applied:
            transfer_info["process_overrides_applied"] = list(
                result.process_overrides_applied
            )
        if result.filament_overrides_applied:
            transfer_info["filament_overrides_applied"] = list(
                result.filament_overrides_applied
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_slicer_client_filament_overrides.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/slicer_client.py tests/test_slicer_client_filament_overrides.py
git commit -m "feat: thread filament_overrides through slicer client"
```

---

### Task 2: `SlicerClient` filament catalogue + layout fetchers

**Files:**
- Modify: `app/slicer_client.py` (add two methods near `get_process_options`/`get_process_layout`)
- Test: `tests/test_slicer_client_filament_options.py` (new — mirror `tests/test_slicer_client_process_options.py`)

**Interfaces:**
- Produces: `async get_filament_options() -> dict` (GET `/options/filament`) and `async get_filament_layout() -> dict` (GET `/options/filament/layout`), each raising `SlicingError` on connection failure or non-200, returning the slicer's JSON unchanged.

- [ ] **Step 1: Write the failing test**

Look at `tests/test_slicer_client_process_options.py` for the transport-mock pattern, then create `tests/test_slicer_client_filament_options.py`:

```python
import httpx
import pytest

from app.slicer_client import SlicerClient, SlicingError


def _client(handler):
    return SlicerClient("http://slicer", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_filament_options_returns_payload():
    def handler(request):
        assert request.url.path == "/options/filament"
        return httpx.Response(200, json={"version": "2.3.2-41", "options": {}})

    result = await _client(handler).get_filament_options()
    assert result["version"] == "2.3.2-41"


@pytest.mark.asyncio
async def test_get_filament_layout_returns_payload():
    def handler(request):
        assert request.url.path == "/options/filament/layout"
        return httpx.Response(200, json={"version": "v", "pages": [{"label": "Filament", "optgroups": []}]})

    result = await _client(handler).get_filament_layout()
    assert result["pages"][0]["label"] == "Filament"


@pytest.mark.asyncio
async def test_get_filament_options_raises_on_500():
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(SlicingError):
        await _client(handler).get_filament_options()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_slicer_client_filament_options.py -v`
Expected: FAIL (`AttributeError: 'SlicerClient' object has no attribute 'get_filament_options'`).

- [ ] **Step 3: Implement**

In `app/slicer_client.py`, immediately after `get_process_layout`, add (mirroring it):

```python
    async def get_filament_options(self) -> dict:
        """GET /options/filament — filament-option metadata catalogue.

        Same shape as ``/options/process`` but for filament-domain keys.
        Raises ``SlicingError`` on non-200 (the 503 ``options_not_loaded``
        case is meaningful so we surface it).
        """
        url = f"{self._base_url}/options/filament"
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.get(url)
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")
        if resp.status_code != 200:
            raise SlicingError(
                f"Slicer returned {resp.status_code}: {resp.text[:500]}",
            )
        return resp.json()

    async def get_filament_layout(self) -> dict:
        """GET /options/filament/layout — filament editor page layout."""
        url = f"{self._base_url}/options/filament/layout"
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.get(url)
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")
        if resp.status_code != 200:
            raise SlicingError(
                f"Slicer returned {resp.status_code}: {resp.text[:500]}",
            )
        return resp.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_slicer_client_filament_options.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/slicer_client.py tests/test_slicer_client_filament_options.py
git commit -m "feat: add filament options/layout fetchers to slicer client"
```

---

### Task 3: Persist `filament_overrides` through slice jobs

**Files:**
- Modify: `app/slice_jobs.py` (`SliceJob` dataclass, `SliceJob.new`, `SliceJobManager.submit`, `SliceJobManager.slice_stream` call in `_run_job`)
- Test: `tests/test_slice_job_filament_overrides.py` (new — mirror `tests/test_slice_job_process_overrides.py`)

**Interfaces:**
- Consumes: `SlicerClient.slice_stream(..., filament_overrides=...)` from Task 1.
- Produces:
  - `SliceJob.filament_overrides: dict | None = None` (round-trips via `asdict`/`cls(**data)`).
  - `SliceJob.new(..., filament_overrides=None)` and `SliceJobManager.submit(..., filament_overrides=None)` keyword args.
  - `_run_job` forwards `job.filament_overrides` into `self._slicer.slice_stream(...)`.

- [ ] **Step 1: Write the failing test**

Read `tests/test_slice_job_process_overrides.py` first to copy its construction helpers, then create `tests/test_slice_job_filament_overrides.py`:

```python
from app.slice_jobs import SliceJob


def test_slice_job_new_stores_filament_overrides(tmp_path):
    blob = tmp_path / "in.3mf"
    blob.write_bytes(b"x")
    job = SliceJob.new(
        filename="a.3mf",
        machine_profile="GM020",
        process_profile="GP109",
        filament_profiles=["GFL99"],
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id=None,
        auto_print=False,
        input_path=blob,
        filament_overrides={"0": {"nozzle_temperature": "230"}},
    )
    assert job.filament_overrides == {"0": {"nozzle_temperature": "230"}}


def test_slice_job_filament_overrides_round_trip(tmp_path):
    blob = tmp_path / "in.3mf"
    blob.write_bytes(b"x")
    job = SliceJob.new(
        filename="a.3mf", machine_profile="GM020", process_profile="GP109",
        filament_profiles=["GFL99"], plate_id=1, plate_type="",
        project_filament_count=1, printer_id=None, auto_print=False,
        input_path=blob, filament_overrides={"1": {"filament_flow_ratio": "0.95"}},
    )
    restored = SliceJob.from_dict(job.to_dict())
    assert restored.filament_overrides == {"1": {"filament_flow_ratio": "0.95"}}


def test_slice_job_defaults_filament_overrides_none(tmp_path):
    blob = tmp_path / "in.3mf"
    blob.write_bytes(b"x")
    job = SliceJob.new(
        filename="a.3mf", machine_profile="GM020", process_profile="GP109",
        filament_profiles=["GFL99"], plate_id=1, plate_type="",
        project_filament_count=1, printer_id=None, auto_print=False,
        input_path=blob,
    )
    assert job.filament_overrides is None
    # Legacy jobs persisted before this field must still load.
    legacy = job.to_dict()
    legacy.pop("filament_overrides")
    assert SliceJob.from_dict(legacy).filament_overrides is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_slice_job_filament_overrides.py -v`
Expected: FAIL (`SliceJob.new() got an unexpected keyword argument 'filament_overrides'`).

- [ ] **Step 3: Implement**

In `app/slice_jobs.py`:

3a. Add the dataclass field right after `process_overrides` (line ~70):

```python
    process_overrides: dict[str, str] | None = None
    filament_overrides: dict[str, dict[str, str]] | None = None
```

3b. In `SliceJob.new`, add the param after `process_overrides` and pass it into the constructor:

```python
        process_overrides: dict[str, str] | None = None,
        filament_overrides: dict[str, dict[str, str]] | None = None,
```
and in the returned `cls(...)`:
```python
            process_overrides=process_overrides,
            filament_overrides=filament_overrides,
```

3c. The `from_dict` legacy guard: `cls(**data)` already tolerates a missing key via the field default, but a *present-but-unknown* key would crash. Since we add the field, `to_dict` always emits it; only legacy dicts lack it (handled by default). Add nothing else — the test's `legacy.pop` confirms this works.

3d. In `SliceJobManager.submit`, add the param after `process_overrides` (line ~543) and forward to `SliceJob.new`:

```python
        process_overrides: dict[str, str] | None = None,
        filament_overrides: dict[str, dict[str, str]] | None = None,
```
and in the `SliceJob.new(...)` call:
```python
            process_overrides=process_overrides,
            filament_overrides=filament_overrides,
```

3e. In `_run_job`, the `self._slicer.slice_stream(...)` call (line ~635), add:

```python
                process_overrides=job.process_overrides,
                filament_overrides=job.filament_overrides,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_slice_job_filament_overrides.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/slice_jobs.py tests/test_slice_job_filament_overrides.py
git commit -m "feat: persist filament_overrides through slice jobs"
```

---

### Task 4: Gateway endpoints — parse, thread, and proxy filament overrides

**Files:**
- Modify: `app/main.py` (add `_parse_filament_overrides_form`; add `filament_overrides` Form param + threading to `create_slice_job` and `print_file`; surface `filament_overrides_applied` in the print response; add catalogue/layout/baseline proxy endpoints)
- Test: `tests/test_filament_overrides_routes.py` (new — mirror `tests/test_print_routes_process_overrides.py`)

**Interfaces:**
- Consumes: `SliceJobManager.submit(..., filament_overrides=...)` (Task 3); `SlicerClient.get_filament_options/get_filament_layout/get_filament_detail` (Task 2 + existing); `SliceResult.filament_overrides_applied` (Task 1).
- Produces:
  - `_parse_filament_overrides_form(raw: str) -> dict[str, dict[str, str]] | None` — `None` for empty; `HTTPException(400)` on bad JSON / non-object / inner-not-object / non-string inner value.
  - `GET /api/slicer/options/filament`, `GET /api/slicer/options/filament/layout`, `GET /api/slicer/filaments/{setting_id}`.
  - `filament_overrides` accepted as `Form("")` on `POST /api/slice-jobs` and `POST /api/print`.

- [ ] **Step 1: Write the failing test (parser + endpoints)**

Read `tests/test_print_routes_process_overrides.py` and `tests/test_slice_jobs_api.py` for the app/client fixtures, then create `tests/test_filament_overrides_routes.py`:

```python
import pytest
from fastapi import HTTPException

from app.main import _parse_filament_overrides_form


def test_parse_filament_overrides_valid():
    raw = '{"0": {"nozzle_temperature": "230"}, "1": {"filament_flow_ratio": "0.95"}}'
    assert _parse_filament_overrides_form(raw) == {
        "0": {"nozzle_temperature": "230"},
        "1": {"filament_flow_ratio": "0.95"},
    }


def test_parse_filament_overrides_empty_is_none():
    assert _parse_filament_overrides_form("") is None


def test_parse_filament_overrides_bad_json():
    with pytest.raises(HTTPException) as exc:
        _parse_filament_overrides_form("{not json")
    assert exc.value.status_code == 400


def test_parse_filament_overrides_outer_not_object():
    with pytest.raises(HTTPException):
        _parse_filament_overrides_form('["a"]')


def test_parse_filament_overrides_inner_not_object():
    with pytest.raises(HTTPException):
        _parse_filament_overrides_form('{"0": "230"}')


def test_parse_filament_overrides_inner_value_not_string():
    with pytest.raises(HTTPException):
        _parse_filament_overrides_form('{"0": {"nozzle_temperature": 230}}')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_filament_overrides_routes.py -v`
Expected: FAIL (`ImportError: cannot import name '_parse_filament_overrides_form'`).

- [ ] **Step 3: Implement the parser**

In `app/main.py`, directly below `_parse_process_overrides_form`, add:

```python
def _parse_filament_overrides_form(
    raw: str,
) -> dict[str, dict[str, str]] | None:
    """Validate and decode the filament_overrides form field.

    Shape: ``{ "<slot>": { "<key>": "<string value>" } }``. Returns
    ``None`` for empty input. Raises ``HTTPException(400)`` on malformed
    input — slot indices and key validity are left to the permissive
    slicer, but client-side shape mistakes are surfaced early.

    The ``isinstance(raw, str)`` guard mirrors
    ``_parse_process_overrides_form`` for direct in-process route calls.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid filament_overrides JSON")
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=400,
            detail="filament_overrides must be a JSON object",
        )
    for slot, keys in parsed.items():
        if not isinstance(keys, dict):
            raise HTTPException(
                status_code=400,
                detail=f"filament_overrides[{slot}] must be a JSON object",
            )
        if any(not isinstance(v, str) for v in keys.values()):
            raise HTTPException(
                status_code=400,
                detail=f"filament_overrides[{slot}] values must be strings",
            )
    return parsed
```

- [ ] **Step 4: Run parser test to verify it passes**

Run: `.venv/bin/pytest tests/test_filament_overrides_routes.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Thread the field through `create_slice_job`**

In `create_slice_job` (the `POST /api/slice-jobs` handler):

5a. Add the Form param after `process_overrides: str = Form("")`:

```python
    process_overrides: str = Form(""),
    filament_overrides: str = Form(""),
    copies: int = Form(1),
```

5b. After `process_overrides_dict = _parse_process_overrides_form(process_overrides)` (find the line in this handler), add:

```python
    filament_overrides_dict = _parse_filament_overrides_form(filament_overrides)
```

5c. In the `await slice_jobs.submit(...)` call inside `create_slice_job`, add after `process_overrides=process_overrides_dict,`:

```python
        process_overrides=process_overrides_dict,
        filament_overrides=filament_overrides_dict,
```

- [ ] **Step 6: Thread the field through `print_file` (direct-slice path)**

In `print_file` (`POST /api/print`):

6a. Add the Form param after `process_overrides: str = Form(""),`:

```python
    process_overrides: str = Form(""),
    filament_overrides: str = Form(""),
```

6b. After `process_overrides_dict = _parse_process_overrides_form(process_overrides)`:

```python
    filament_overrides_dict = _parse_filament_overrides_form(filament_overrides)
```

6c. In the direct `slicer_client.slice(...)` (or `slice_stream`) call within `print_file` that passes `process_overrides=process_overrides_dict` (around line 2030), add:

```python
                process_overrides=process_overrides_dict,
                filament_overrides=filament_overrides_dict,
```

6d. In the `PrintResponse(...)` construction that sets `process_overrides_applied=[...]` (around line 2043-2053), add a sibling `filament_overrides_applied`. Read those lines first; mirror the exact shape used for process. Example:

```python
        process_overrides_applied=[
            ProcessOverrideApplied(**o) for o in slice_result.process_overrides_applied
        ],
        filament_overrides_applied=[
            FilamentOverrideApplied(**o) for o in slice_result.filament_overrides_applied
        ],
```

If `process_overrides_applied` is surfaced as raw dicts rather than a typed model, mirror that instead (raw `list(slice_result.filament_overrides_applied)`). Match whatever the process path does. (The response model change is Step 8.)

- [ ] **Step 7: Add the three proxy endpoints**

In `app/main.py`, right after `slicer_options_process_layout`, add (mirroring it and `/api/slicer/processes/{setting_id}`):

```python
@app.get("/api/slicer/options/filament")
async def slicer_options_filament():
    """Filament-option metadata catalogue. Pass-through to the slicer."""
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicer not configured: ORCASLICER_API_URL not set",
        )
    try:
        return await slicer_client.get_filament_options()
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/slicer/options/filament/layout")
async def slicer_options_filament_layout():
    """Filament editor layout. Pass-through to the slicer."""
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicer not configured: ORCASLICER_API_URL not set",
        )
    try:
        return await slicer_client.get_filament_layout()
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/slicer/filaments/{setting_id}")
async def slicer_filament_profile(setting_id: str):
    """Resolved filament values for one profile — the filamentBaseline rung.

    Returns the slicer's ``resolved`` flat dict for the named filament
    profile. 404 when the profile is unknown.
    """
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicer not configured: ORCASLICER_API_URL not set",
        )
    detail = await slicer_client.get_filament_detail(setting_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Filament profile {setting_id!r} not found")
    return detail.get("resolved", {})
```

- [ ] **Step 8: Add the response-model field (if process uses a typed model)**

Check `app/models.py` for `ProcessOverrideApplied` and the print response model (likely `PrintResponse`). If `process_overrides_applied` is a typed field there, add the filament analog:

```python
class FilamentOverrideApplied(BaseModel):
    slot: int
    key: str
    value: str
    previous: str | None = None
```

and on the print response model, beside `process_overrides_applied`:

```python
    filament_overrides_applied: list[FilamentOverrideApplied] = Field(default_factory=list)
```

Import `FilamentOverrideApplied` in `app/main.py` next to `ProcessOverrideApplied`. If the process path uses raw dicts instead, skip this step and keep Step 6d's raw-list form.

- [ ] **Step 9: Add endpoint + threading tests**

Append to `tests/test_filament_overrides_routes.py` (use the existing app/client fixtures from `tests/conftest.py` — read `tests/test_slice_jobs_api.py` to copy the slicer-mock fixture that injects a fake `slicer_client` and `slice_jobs`):

```python
def test_options_filament_proxy(client, fake_slicer):
    fake_slicer.get_filament_options_return = {"version": "v", "options": {}}
    resp = client.get("/api/slicer/options/filament")
    assert resp.status_code == 200
    assert resp.json()["version"] == "v"


def test_slice_jobs_rejects_bad_filament_overrides(client):
    resp = client.post(
        "/api/slice-jobs",
        data={
            "machine_profile": "GM020", "process_profile": "GP109",
            "filament_profiles": "[\"GFL99\"]",
            "filament_overrides": "{\"0\": \"230\"}",
        },
        files={"file": ("a.3mf", b"PK\x03\x04", "model/3mf")},
    )
    assert resp.status_code == 400
```

Adapt fixture names to whatever `tests/test_slice_jobs_api.py` actually exposes (e.g. a `monkeypatch`-injected fake). If a `fake_slicer` fixture doesn't exist, assert only the 400-validation path (which needs no slicer) and the proxy 400-when-unconfigured path instead:

```python
def test_options_filament_proxy_requires_slicer(client_without_slicer):
    resp = client_without_slicer.get("/api/slicer/options/filament")
    assert resp.status_code == 400
```

- [ ] **Step 10: Run the full route test file**

Run: `.venv/bin/pytest tests/test_filament_overrides_routes.py -v`
Expected: PASS.

- [ ] **Step 11: Run the backend suite to catch regressions**

Run: `.venv/bin/pytest -q`
Expected: PASS (no regressions in existing slice/print tests).

- [ ] **Step 12: Commit**

```bash
git add app/main.py app/models.py tests/test_filament_overrides_routes.py
git commit -m "feat: accept and proxy filament overrides in gateway API"
```

---

### Task 5: Web — filament options/layout/baseline API hooks

**Files:**
- Create: `web/src/lib/api/filament-options.ts`
- Test: `web/src/lib/api/filament-options.test.ts` (new)

**Interfaces:**
- Consumes: `/api/slicer/options/filament`, `/api/slicer/options/filament/layout`, `/api/slicer/filaments/{id}`; the existing `ProcessOption`/`ProcessLayout`/`ProcessOptionsCatalogue` types and `adaptOption`/`adaptCatalogue`/`adaptLayout` adapters in `web/src/lib/api/process-options.ts`.
- Produces: `useFilamentOptions()`, `useFilamentLayout()`, `useFilamentBaseline(settingId?)` hooks returning the same shapes as their process counterparts (catalogue/layout reuse `ProcessOptionsCatalogue`/`ProcessLayout`; baseline returns `Record<string,string>`).

- [ ] **Step 1: Export the adapters for reuse**

In `web/src/lib/api/process-options.ts`, add `export` to `adaptCatalogue` and `adaptLayout` (and `RawCatalogue`/`RawLayout` if not already importable) so the filament module can reuse them. If they are not easily exportable without churn, instead extract `adaptCatalogue`/`adaptLayout`/`adaptOption` + raw types into a new `web/src/lib/process/option-adapters.ts` and import from both. Prefer the smaller change (just add `export`).

- [ ] **Step 2: Write the failing test**

Create `web/src/lib/api/filament-options.test.ts` (mirror any existing api test that mocks `fetch`; check `web/src/lib/api/printer-commands.test.ts` for the pattern):

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchFilamentOptions, fetchFilamentLayout, fetchFilamentBaseline } from './filament-options';

describe('filament-options api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('adapts the filament catalogue', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        version: 'v',
        options: {
          nozzle_temperature: {
            key: 'nozzle_temperature', label: 'Nozzle temp', category: 'Filament',
            tooltip: '', type: 'coInts', sidetext: '°C', default: '220',
            min: 0, max: 300, enum_values: null, enum_labels: null,
            mode: 'simple', gui_type: '', nullable: false, readonly: false,
          },
        },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    const cat = await fetchFilamentOptions();
    expect(cat.options.nozzle_temperature.label).toBe('Nozzle temp');
  });

  it('fetches a resolved baseline', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ nozzle_temperature: '220' }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }),
    );
    const base = await fetchFilamentBaseline('GFL99');
    expect(base.nozzle_temperature).toBe('220');
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd web && npx vitest run src/lib/api/filament-options.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 4: Implement**

Create `web/src/lib/api/filament-options.ts` (mirror `process-options.ts`, reusing the exported adapters):

```ts
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { fetchJson, ApiError } from './client';
import {
  adaptCatalogue, adaptLayout,
  type RawCatalogue, type RawLayout,
} from './process-options';
import type { ProcessLayout, ProcessOptionsCatalogue } from '@/lib/process/types';

export async function fetchFilamentOptions(): Promise<ProcessOptionsCatalogue> {
  const raw = await fetchJson<RawCatalogue>('/api/slicer/options/filament');
  return adaptCatalogue(raw);
}

export async function fetchFilamentLayout(): Promise<ProcessLayout> {
  const raw = await fetchJson<RawLayout>('/api/slicer/options/filament/layout');
  return adaptLayout(raw);
}

export async function fetchFilamentBaseline(settingId: string): Promise<Record<string, string>> {
  return fetchJson<Record<string, string>>(
    `/api/slicer/filaments/${encodeURIComponent(settingId)}`,
  );
}

const RETRYABLE_503_CODES = new Set(['options_not_loaded', 'options_layout_not_loaded']);

function shouldRetry(failureCount: number, error: Error): boolean {
  if (failureCount >= 1) return false;
  if (!(error instanceof ApiError)) return false;
  if (error.status !== 503) return false;
  return !!error.code && RETRYABLE_503_CODES.has(error.code);
}

export function useFilamentOptions(): UseQueryResult<ProcessOptionsCatalogue, Error> {
  return useQuery({
    queryKey: ['filament-options', 'catalogue'],
    queryFn: fetchFilamentOptions,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    retry: shouldRetry,
    retryDelay: 1500,
  });
}

export function useFilamentLayout(): UseQueryResult<ProcessLayout, Error> {
  return useQuery({
    queryKey: ['filament-options', 'layout'],
    queryFn: fetchFilamentLayout,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    retry: shouldRetry,
    retryDelay: 1500,
  });
}

export function useFilamentBaseline(
  settingId: string | undefined,
): UseQueryResult<Record<string, string>, Error> {
  return useQuery({
    queryKey: ['filament-options', 'baseline', settingId ?? ''],
    queryFn: () => fetchFilamentBaseline(settingId!),
    enabled: !!settingId,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
  });
}
```

If `RawCatalogue`/`RawLayout` were not exportable from `process-options.ts`, import them from the extracted `option-adapters.ts` instead.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd web && npx vitest run src/lib/api/filament-options.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/api/filament-options.ts web/src/lib/api/filament-options.test.ts web/src/lib/api/process-options.ts
git commit -m "feat: add web filament options/layout/baseline hooks"
```

---

### Task 6: Web — `filamentOverrides` state in print context

**Files:**
- Modify: `web/src/lib/print-context.tsx`
- Test: `web/src/lib/print-context.test.tsx` (new; if a context test already exists, extend it)

**Interfaces:**
- Produces on the context value:
  - `filamentOverrides: Record<number, Record<string, string>>`
  - `setFilamentOverride(slot: number, key: string, value: string): void`
  - `revertFilamentOverride(slot: number, key: string): void`
  - `resetAllFilamentOverrides(): void`

- [ ] **Step 1: Write the failing test**

Create `web/src/lib/print-context.test.tsx` using `@testing-library/react`'s `renderHook` with the provider (read `print-context.tsx` to get the provider/hook names — likely `PrintProvider` and `usePrintContext`):

```tsx
import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { PrintProvider, usePrintContext } from './print-context';

function wrapper({ children }: { children: React.ReactNode }) {
  return <PrintProvider>{children}</PrintProvider>;
}

describe('filamentOverrides', () => {
  it('sets, reverts, and resets per-slot overrides', () => {
    const { result } = renderHook(() => usePrintContext(), { wrapper });

    act(() => result.current.setFilamentOverride(0, 'nozzle_temperature', '230'));
    act(() => result.current.setFilamentOverride(1, 'filament_flow_ratio', '0.95'));
    expect(result.current.filamentOverrides).toEqual({
      0: { nozzle_temperature: '230' },
      1: { filament_flow_ratio: '0.95' },
    });

    act(() => result.current.revertFilamentOverride(0, 'nozzle_temperature'));
    expect(result.current.filamentOverrides[0]).toBeUndefined();

    act(() => result.current.resetAllFilamentOverrides());
    expect(result.current.filamentOverrides).toEqual({});
  });
});
```

If the provider requires props (e.g. a QueryClient), wrap accordingly — check how `print.test.tsx` sets up providers and copy that.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/lib/print-context.test.tsx`
Expected: FAIL (`setFilamentOverride is not a function`).

- [ ] **Step 3: Implement**

In `web/src/lib/print-context.tsx`, mirror the `processOverrides` block:

3a. Add to the context type (near `processOverrides: Record<string, string>;`):

```tsx
  filamentOverrides: Record<number, Record<string, string>>;
  setFilamentOverride(slot: number, key: string, value: string): void;
  revertFilamentOverride(slot: number, key: string): void;
  resetAllFilamentOverrides(): void;
```

3b. Add state next to `const [processOverrides, setProcessOverrides] = useState(...)`:

```tsx
  const [filamentOverrides, setFilamentOverrides] =
    useState<Record<number, Record<string, string>>>({});
```

3c. Add callbacks mirroring the process ones:

```tsx
  const setFilamentOverride = useCallback((slot: number, key: string, value: string) => {
    setFilamentOverrides((prev) => ({
      ...prev,
      [slot]: { ...(prev[slot] ?? {}), [key]: value },
    }));
  }, []);

  const revertFilamentOverride = useCallback((slot: number, key: string) => {
    setFilamentOverrides((prev) => {
      const slotKeys = { ...(prev[slot] ?? {}) };
      delete slotKeys[key];
      const next = { ...prev };
      if (Object.keys(slotKeys).length === 0) delete next[slot];
      else next[slot] = slotKeys;
      return next;
    });
  }, []);

  const resetAllFilamentOverrides = useCallback(() => {
    setFilamentOverrides({});
  }, []);
```

3d. Add all four to the context value object and to its `useMemo` dependency array (alongside `processOverrides`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/lib/print-context.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/print-context.tsx web/src/lib/print-context.test.tsx
git commit -m "feat: add per-slot filamentOverrides state to print context"
```

---

### Task 7: Web — send `filament_overrides` and notify drops

**Files:**
- Modify: `web/src/lib/api/slice-jobs.ts` (args + form field)
- Modify: `web/src/lib/api/types.ts` (add `filament_overrides_applied` to the slice result/settings_transfer type + a `FilamentOverrideApplied` type)
- Modify: `web/src/lib/process/drop-notice.ts` (add a per-slot filament drop notifier)
- Test: extend `web/src/lib/process/drop-notice.test.ts` if present, else add `web/src/lib/process/filament-drop-notice.test.ts`

**Interfaces:**
- Consumes: `SubmitSliceJobArgs` (extends with `filamentOverrides`); `settings_transfer.filament_overrides_applied` from the slice result.
- Produces:
  - `SubmitSliceJobArgs.filamentOverrides?: Record<string, Record<string, string>>`; appended as `filament_overrides` form field only when non-empty.
  - `notifyDroppedFilamentOverrides(requested, applied)` — toasts any requested `(slot,key)` absent from `applied`.

- [ ] **Step 1: Write the failing test**

Read `web/src/lib/process/drop-notice.ts` to match its toast mechanism, then create `web/src/lib/process/filament-drop-notice.test.ts`:

```ts
import { describe, it, expect, vi } from 'vitest';
import { toast } from 'sonner';
import { notifyDroppedFilamentOverrides } from './drop-notice';

vi.mock('sonner', () => ({ toast: { warning: vi.fn() } }));

describe('notifyDroppedFilamentOverrides', () => {
  it('warns when a requested key was not applied', () => {
    notifyDroppedFilamentOverrides(
      { 0: { nozzle_temperature: '230', made_up_key: 'x' } },
      [{ slot: 0, key: 'nozzle_temperature', value: '230', previous: '220' }],
    );
    expect(toast.warning).toHaveBeenCalled();
  });

  it('stays silent when everything applied', () => {
    vi.mocked(toast.warning).mockClear();
    notifyDroppedFilamentOverrides(
      { 0: { nozzle_temperature: '230' } },
      [{ slot: 0, key: 'nozzle_temperature', value: '230', previous: '220' }],
    );
    expect(toast.warning).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/lib/process/filament-drop-notice.test.ts`
Expected: FAIL (`notifyDroppedFilamentOverrides` not exported).

- [ ] **Step 3: Implement the types**

In `web/src/lib/api/types.ts`, near `ProcessOverrideApplied` / `process_overrides_applied` (lines ~196, ~398):

```ts
export interface FilamentOverrideApplied {
  slot: number;
  key: string;
  value: string;
  previous: string | null;
}
```
and add to the settings-transfer/slice-result interface beside `process_overrides_applied`:
```ts
  filament_overrides_applied?: FilamentOverrideApplied[];
```

- [ ] **Step 4: Implement the drop notifier**

In `web/src/lib/process/drop-notice.ts`, add (mirror `notifyDroppedOverrides`, but iterate slots):

```ts
import type { FilamentOverrideApplied } from '@/lib/api/types';

export function notifyDroppedFilamentOverrides(
  requested: Record<number, Record<string, string>>,
  applied: FilamentOverrideApplied[] | undefined,
): void {
  const appliedSet = new Set((applied ?? []).map((a) => `${a.slot}:${a.key}`));
  const dropped: string[] = [];
  for (const [slot, keys] of Object.entries(requested)) {
    for (const key of Object.keys(keys)) {
      if (!appliedSet.has(`${slot}:${key}`)) dropped.push(`slot ${slot} · ${key}`);
    }
  }
  if (dropped.length > 0) {
    toast.warning(
      `Some filament settings weren't applied: ${dropped.join(', ')}`,
    );
  }
}
```

Ensure `toast` is imported in the file (it already is for the process notifier).

- [ ] **Step 5: Implement the slice-jobs arg + form field**

In `web/src/lib/api/slice-jobs.ts`:

5a. Add to `SubmitSliceJobArgs` after `processOverrides?:`:

```ts
  processOverrides?: Record<string, string>;
  filamentOverrides?: Record<string, Record<string, string>>;
```

5b. After the `process_overrides` append block:

```ts
  if (args.filamentOverrides && Object.keys(args.filamentOverrides).length > 0) {
    fd.append('filament_overrides', JSON.stringify(args.filamentOverrides));
  }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd web && npx vitest run src/lib/process/filament-drop-notice.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/api/slice-jobs.ts web/src/lib/api/types.ts web/src/lib/process/drop-notice.ts web/src/lib/process/filament-drop-notice.test.ts
git commit -m "feat: submit filament_overrides and warn on dropped keys"
```

---

### Task 8: Web — "Filament settings" card + sheet, wired into the print flow

**Files:**
- Create: `web/src/components/print/filament-parameters-card.tsx`
- Create: `web/src/components/print/filament-all-sheet.tsx`
- Modify: `web/src/routes/print.tsx` (render the card; pass `filamentOverrides` into `submitSliceJob`; call `notifyDroppedFilamentOverrides`)
- Test: `web/src/components/print/filament-parameters-card.test.tsx` (new — mirror `process-parameters-card.test.tsx`)

**Interfaces:**
- Consumes: `useFilamentOptions`, `useFilamentLayout`, `useFilamentBaseline` (Task 5); context `filamentOverrides`/`setFilamentOverride`/`revertFilamentOverride` (Task 6); `effectiveValue`/`revertTarget` from `web/src/lib/process/effective-value`; `ProcessOptionRow`. The card receives the list of used filament slots with `{ slot: number; label: string; settingId: string }`.
- Produces: `<FilamentParametersCard slots={...} />` rendered on the print page; `filamentOverrides` reaching the slicer.

- [ ] **Step 1: Write the failing test**

Read `web/src/components/print/process-parameters-card.test.tsx` for the render/provider harness, then create `web/src/components/print/filament-parameters-card.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
// ...import the same providers process-parameters-card.test.tsx uses
import { FilamentParametersCard } from './filament-parameters-card';

describe('FilamentParametersCard', () => {
  it('renders a slot selector for each used filament slot', () => {
    renderWithProviders(
      <FilamentParametersCard
        slots={[
          { slot: 0, label: 'PLA Basic', settingId: 'GFL99' },
          { slot: 1, label: 'PETG HF', settingId: 'GFG96' },
        ]}
      />,
    );
    expect(screen.getByText(/Filament settings/i)).toBeInTheDocument();
  });
});
```

Use the same `renderWithProviders`/QueryClient harness the process card test uses (copy it). Mock the option/layout queries the same way that test does, or wrap with a `QueryClientProvider` and let the fetches no-op.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/print/filament-parameters-card.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `filament-all-sheet.tsx`**

Copy `web/src/components/print/process-all-sheet.tsx` to `filament-all-sheet.tsx` and adapt:
- Source catalogue/layout from `useFilamentOptions`/`useFilamentLayout`.
- Add a `slot: number` prop and a `settingId` for the baseline (`useFilamentBaseline(settingId)`).
- Read/write overrides via `filamentOverrides[slot]` / `setFilamentOverride(slot, key, v)` / `revertFilamentOverride(slot, key)` instead of the flat process callbacks.
- Effective value chain: `effectiveValue(key, filamentOverrides[slot] ?? {}, /*modifications*/ null, baseline, catalogue)`.

Keep its structure otherwise identical (page → optgroup drill-down, `ProcessOptionRow`).

- [ ] **Step 4: Implement `filament-parameters-card.tsx`**

Copy `process-parameters-card.tsx` to `filament-parameters-card.tsx` and adapt:
- Props: `{ slots: Array<{ slot: number; label: string; settingId: string }> }`.
- Local state: `const [activeSlot, setActiveSlot] = useState(slots[0]?.slot ?? 0)`.
- Render a slot selector at the top (a `Select` over `slots`, labeled `${slot} · ${label}`). When `slots.length <= 1`, render the single slot's label as static text instead of a dropdown.
- Catalogue/layout from `useFilamentOptions`/`useFilamentLayout`; baseline from `useFilamentBaseline(slots.find(s => s.slot === activeSlot)?.settingId)`.
- `rowKeys` = keys present in `filamentOverrides[activeSlot]` (v1 has no file-modified filament keys — see spec "Out of scope").
- The "modified" badge count = number of keys across `filamentOverrides[activeSlot]`.
- Rows use `ProcessOptionRow` with `value = effectiveValue(key, filamentOverrides[activeSlot] ?? {}, null, baseline, catalogue)`, `onCommit = (v) => setFilamentOverride(activeSlot, key, v)`, `onRevert = () => revertFilamentOverride(activeSlot, key)`.
- "Show all settings" opens `FilamentAllSheet` for `activeSlot`.
- Title: `Filament settings`.

The empty state ("No customizations from default profile") and loading/error states copy the process card verbatim.

- [ ] **Step 5: Wire into `print.tsx`**

5a. Build the `slots` array from the same data `buildFilamentProfilesPayload` uses — the used project filaments and their selected filament setting ids. Add a memo near the existing filament logic:

```tsx
const filamentSlots = useMemo(() => {
  if (!info) return [];
  // position == slicer slot; reuse the same used-position predicate as
  // buildFilamentProfilesPayload. settingId = selected filament for the slot
  // (matched tray's filament, else authored). label = its display name.
  // Derive from info.filaments + filamentMapping + resolveQuery, mirroring
  // buildFilamentProfilesPayload's per-position resolution.
  return computeFilamentSlots(info, filamentMapping, resolveQuery.data);
}, [info, filamentMapping, resolveQuery.data, selectedPlateId]);
```

Implement `computeFilamentSlots` as a small local helper that returns `Array<{ slot: number; label: string; settingId: string }>` for each used position, reusing the exact `isPositionUsed` predicate and the resolved-name lookup already in `buildFilamentProfilesPayload`. (Factor `isPositionUsed` out if it eases reuse.)

5b. Render the card next to `<ProcessParametersCard ... />`:

```tsx
{filamentSlots.length > 0 && <FilamentParametersCard slots={filamentSlots} />}
```

5c. Pass overrides into the slice request in `startSlicing`'s `submitSliceJob({...})` call, after `processOverrides,`:

```tsx
        processOverrides,
        filamentOverrides,
```
(destructure `filamentOverrides` from `usePrintContext()` at the top, alongside `processOverrides`.)

5d. After the existing `notifyDroppedOverrides(processOverrides, ...)` call (line ~679), add:

```tsx
        notifyDroppedFilamentOverrides(
          filamentOverrides,
          current.settings_transfer?.filament_overrides_applied ?? undefined,
        );
```

Import `notifyDroppedFilamentOverrides` from `@/lib/process/drop-notice` and `FilamentParametersCard` from `@/components/print/filament-parameters-card`.

- [ ] **Step 6: Run the card test + type-check + build**

Run: `cd web && npx vitest run src/components/print/filament-parameters-card.test.tsx`
Expected: PASS.

Run: `cd web && npm run build`
Expected: Vite build succeeds (TypeScript clean). This rebuilds `app/static/dist/`.

- [ ] **Step 7: Run the web suite**

Run: `cd web && npm test`
Expected: PASS (no regressions; `print.test.tsx` still green — update it if it asserts the exact card list).

- [ ] **Step 8: Commit**

```bash
git add web/src/components/print/filament-parameters-card.tsx web/src/components/print/filament-all-sheet.tsx web/src/routes/print.tsx web/src/components/print/filament-parameters-card.test.tsx app/static/dist
git commit -m "feat: add per-slot Filament settings card to print UI"
```

---

### Task 9: End-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Full backend suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 2: Full web suite + build**

Run: `cd web && npm test && npm run build`
Expected: PASS + successful build.

- [ ] **Step 3: Manual smoke (if a slicer is reachable)**

With `ORCASLICER_API_URL` pointed at a slicer that has the filament-overrides feature: `python -m app`, open the print page, import a multi-filament 3MF, open **Filament settings**, set slot 1's nozzle temperature, slice, and confirm via the `result`/response that `filament_overrides_applied` contains the change and no drop toast appears. Confirm `GET /api/slicer/options/filament` returns a catalogue.

- [ ] **Step 4: Final commit (if any verification fixes were needed)**

```bash
git add -A && git commit -m "test: verify filament overrides end-to-end"
```
```
```

---

## Self-Review

**Spec coverage:**
- Slice plumbing + applied report → Task 1. ✓
- `/options/filament` + `/options/filament/layout` fetchers → Task 2; proxy endpoints → Task 4. ✓
- Per-profile baseline endpoint → Task 4 Step 7. ✓
- Slice-job persistence → Task 3. ✓
- Form-field parse + threading on both `/api/slice-jobs` and `/api/print` → Task 4. ✓
- Web hooks (options/layout/baseline) → Task 5. ✓
- Context state → Task 6. ✓
- Slice request submission + drop notice → Task 7. ✓
- Dedicated slot-selector card + sheet + print-page wiring → Task 8. ✓
- Effective-value reuse → Tasks 5/8. ✓
- Testing (pytest + vitest) → each task + Task 9. ✓
- Out-of-scope (3MF filament modifications, named-profile save, gateway slot validation) → respected; the card's `rowKeys` are user-overrides only. ✓

**Type consistency:** `filament_overrides` (Python) ↔ `filamentOverrides` (TS) used consistently; `filament_overrides_applied` list shape `{slot,key,value,previous}` matches across `SliceResult`, response model, TS `FilamentOverrideApplied`, and the drop notifier. Slice-job field name `filament_overrides` matches the `submit`/`new` kwargs.

**Note for the implementer:** Several Task 4 / Task 8 steps say "mirror whatever the process path does" for the response-model shape and the print-page card list — read the cited process lines first and match them exactly, since this repo's print response may surface applied-overrides as typed models or raw dicts.
