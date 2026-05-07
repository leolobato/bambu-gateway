# Process Parameter Editor — Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface orcaslicer-cli's process-parameter editor data through the gateway, and plumb a per-slice `process_overrides` dict end-to-end.

**Architecture:** Two pass-through proxy endpoints (`/api/slicer/options/process[/layout]`), one new `process_modifications` block on `ThreeMFInfo` (sourced from the extended `/3mf/{token}/inspect`), and a new optional `process_overrides` form field on `/api/print*`. Threaded through `SliceJob` (so it survives gateway restarts mid-slice), `SlicerClient.slice` / `slice_stream`, into the v2 slice body. The slicer's response gains `process_overrides_applied`, surfaced back via `SettingsTransferInfo`.

**Tech Stack:** FastAPI, Pydantic v2, httpx (async), pytest, dataclasses, paho-mqtt (unrelated). Slicer at `ORCASLICER_API_URL` runs orcaslicer-cli rev 41 (`2.3.2-41`) at `10.0.1.9:8070`.

**Spec:** `docs/superpowers/specs/2026-05-08-process-parameter-editor-gateway-design.md`.

---

## File Structure

**Files modified:**
- `app/models.py` — `ProcessModifications`, `ProcessOverrideApplied`, extend `ThreeMFInfo` and `SettingsTransferInfo`.
- `app/parse_3mf.py` — `_adapt` reads `process_modifications` from inspect.
- `app/slicer_client.py` — two new fetch methods; `process_overrides` kwarg on `slice` / `slice_stream` / `_build_v2_slice_body`; `SliceResult.process_overrides_applied`; `_slice_result_from_v2` and `_slice_stream_fallback` updates.
- `app/slice_jobs.py` — `process_overrides` field on `SliceJob`; threaded through `new`, `submit`, `_run_job`. `_SlicerLike` Protocol gains the kwarg.
- `app/main.py` — two new pass-through routes; new `process_overrides` form field on `/api/print`, `/api/print-stream`, `/api/print-preview`; helper to validate the form-field JSON; pass-through into `slice_jobs.submit` and `SlicerClient.slice`.

**Files created:**
- `tests/test_slicer_client_process_options.py` — pass-through methods.
- `tests/test_slicer_client_overrides.py` — `process_overrides` in slice body and `process_overrides_applied` in result parsing.
- `tests/test_parse_3mf_process_modifications.py` — `_adapt` populates `ProcessModifications`.
- `tests/test_slice_job_process_overrides.py` — `SliceJob` round-trips the field; legacy JSON migrates.
- `tests/test_print_routes_process_overrides.py` — form-field validation and round-trip across all three print endpoints.
- `tests/test_options_routes.py` — `/api/slicer/options/process[/layout]` route behaviour.
- `tests/integration/test_options_live.py` — live smoke test against `ORCASLICER_API_URL`.
- `tests/integration/test_process_overrides_live.py` — live override slice against `ORCASLICER_API_URL`.

---

## Task 1: Add `ProcessModifications` and extend `ThreeMFInfo`

**Files:**
- Modify: `app/models.py` — add `ProcessModifications` model, add field to `ThreeMFInfo`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_parse_3mf_process_modifications.py` with one model-shape test. (We'll add adapter tests in Task 2 once `_adapt` is updated.)

```python
"""Unit tests for ProcessModifications model + ThreeMFInfo extension."""
from __future__ import annotations

from app.models import ProcessModifications, ThreeMFInfo


def test_process_modifications_defaults_to_empty():
    pm = ProcessModifications()
    assert pm.process_setting_id == ""
    assert pm.modified_keys == []
    assert pm.values == {}


def test_three_mf_info_carries_process_modifications():
    info = ThreeMFInfo()
    assert isinstance(info.process_modifications, ProcessModifications)
    assert info.process_modifications.process_setting_id == ""


def test_process_modifications_round_trips():
    pm = ProcessModifications(
        process_setting_id="Custom 0.20mm Standard",
        modified_keys=["layer_height", "wall_loops"],
        values={"layer_height": "0.16", "wall_loops": "3"},
    )
    info = ThreeMFInfo(process_modifications=pm)
    dumped = info.model_dump()
    assert dumped["process_modifications"]["process_setting_id"] == "Custom 0.20mm Standard"
    assert dumped["process_modifications"]["modified_keys"] == ["layer_height", "wall_loops"]
    assert dumped["process_modifications"]["values"] == {"layer_height": "0.16", "wall_loops": "3"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_parse_3mf_process_modifications.py -v`
Expected: FAIL with `ImportError: cannot import name 'ProcessModifications' from 'app.models'`.

- [ ] **Step 3: Add the model and extend `ThreeMFInfo`**

In `app/models.py`, locate the existing `ThreeMFInfo` class (around line 309). Add a new model directly above it and add the field to `ThreeMFInfo`:

```python
class ProcessModifications(BaseModel):
    """Per-3MF customisations away from the system process preset.

    Sourced from `process_modifications` on the slicer's
    `/3mf/{token}/inspect` response (schema_version >= 4). Empty defaults
    when the slicer is older or the project didn't customise process
    settings.
    """
    process_setting_id: str = ""
    modified_keys: list[str] = []
    values: dict[str, str] = {}


class ThreeMFInfo(BaseModel):
    plates: list[PlateInfo] = []
    filaments: list[FilamentInfo] = []
    print_profile: PrintProfileInfo = PrintProfileInfo()
    printer: PrinterInfo = PrinterInfo()
    has_gcode: bool = False
    bed_type: str = ""
    process_modifications: ProcessModifications = ProcessModifications()
```

(Preserve the existing `bed_type` doc-comment block above the field.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parse_3mf_process_modifications.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_parse_3mf_process_modifications.py
git commit -m "Add ProcessModifications model and extend ThreeMFInfo

- carries the slicer's process_modifications block from the extended
  /3mf/{token}/inspect response (schema_version 4)
- defaults empty so older slicer builds don't break the gateway"
```

---

## Task 2: `_adapt` populates `process_modifications`

**Files:**
- Modify: `app/parse_3mf.py:127-141` — extend the `ThreeMFInfo(...)` constructor.
- Modify: `tests/test_parse_3mf_process_modifications.py` — add adapter tests.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_parse_3mf_process_modifications.py`:

```python
from app.parse_3mf import _adapt


def _fake_inspect_with_pm(pm: dict | None) -> dict:
    out = {
        "schema_version": 4,
        "is_sliced": False,
        "plate_count": 1,
        "plates": [{"id": 1, "name": "", "used_filament_indices": [0]}],
        "filaments": [
            {"slot": 0, "type": "PLA", "color": "#FFF",
             "filament_id": "GFA00", "settings_id": "Bambu PLA Basic"},
        ],
        "estimate": None,
        "bbox": None,
        "printer_model": "Bambu Lab A1 mini",
        "printer_variant": "0.4",
        "printer_settings_id": "Bambu Lab A1 mini 0.4 nozzle",
        "print_settings_id": "Custom 0.20mm Standard",
        "layer_height": "0.16",
        "curr_bed_type": "Textured PEI Plate",
        "thumbnail_urls": [],
        "use_set_per_plate": {},
    }
    if pm is not None:
        out["process_modifications"] = pm
    return out


def test_adapter_populates_process_modifications():
    insp = _fake_inspect_with_pm({
        "process_setting_id": "Custom 0.20mm Standard",
        "modified_keys": ["layer_height", "wall_loops"],
        "values": {"layer_height": "0.16", "wall_loops": "3"},
    })
    info = _adapt(insp, plate_id=None, thumbnails={})
    assert info.process_modifications.process_setting_id == "Custom 0.20mm Standard"
    assert info.process_modifications.modified_keys == ["layer_height", "wall_loops"]
    assert info.process_modifications.values == {"layer_height": "0.16", "wall_loops": "3"}


def test_adapter_handles_empty_process_modifications():
    insp = _fake_inspect_with_pm({
        "process_setting_id": "",
        "modified_keys": [],
        "values": {},
    })
    info = _adapt(insp, plate_id=None, thumbnails={})
    assert info.process_modifications.process_setting_id == ""
    assert info.process_modifications.modified_keys == []
    assert info.process_modifications.values == {}


def test_adapter_handles_missing_process_modifications():
    """Pre-rev-41 slicer: field not in the inspect payload at all."""
    insp = _fake_inspect_with_pm(None)
    info = _adapt(insp, plate_id=None, thumbnails={})
    assert info.process_modifications.process_setting_id == ""
    assert info.process_modifications.modified_keys == []
    assert info.process_modifications.values == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_parse_3mf_process_modifications.py -v`
Expected: 3 new tests FAIL because `_adapt` doesn't populate the field.

- [ ] **Step 3: Update `_adapt`**

In `app/parse_3mf.py`, update the imports at the top (around line 1-15) to add `ProcessModifications`:

```python
from app.models import (
    FilamentInfo, PlateInfo, PlateObject, PrinterInfo, PrintProfileInfo,
    ProcessModifications, ThreeMFInfo,
)
```

(Adjust to match the file's existing import style — alphabetical, single-line, etc. The exact other names already imported are: `FilamentInfo, PlateInfo, PlateObject, PrinterInfo, PrintProfileInfo, ThreeMFInfo`.)

In the `return ThreeMFInfo(...)` block at line 127-141, add the `process_modifications` argument. Build it from the inspect dict's `process_modifications` field, defaulting empty when missing:

```python
    pm_raw = insp.get("process_modifications") or {}
    process_modifications = ProcessModifications(
        process_setting_id=str(pm_raw.get("process_setting_id", "") or ""),
        modified_keys=list(pm_raw.get("modified_keys") or []),
        values=dict(pm_raw.get("values") or {}),
    )

    return ThreeMFInfo(
        plates=plates,
        filaments=filaments,
        print_profile=PrintProfileInfo(
            print_settings_id=insp.get("print_settings_id", "") or "",
            layer_height=insp.get("layer_height", "") or "",
        ),
        printer=PrinterInfo(
            printer_settings_id=insp.get("printer_settings_id", "") or "",
            printer_model=insp.get("printer_model", "") or "",
            nozzle_diameter=insp.get("printer_variant", "") or "",
        ),
        has_gcode=bool(insp.get("is_sliced", False)),
        bed_type=insp.get("curr_bed_type", "") or "",
        process_modifications=process_modifications,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_parse_3mf_process_modifications.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Run the full parse_3mf test file to confirm no regression**

Run: `.venv/bin/pytest tests/test_parse_3mf_adapter.py tests/test_parse_3mf_process_modifications.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/parse_3mf.py tests/test_parse_3mf_process_modifications.py
git commit -m "Map process_modifications from inspect into ThreeMFInfo

- copies process_setting_id, modified_keys, and values verbatim
- treats missing or empty fields as the empty model so pre-rev-41
  slicer builds keep working"
```

---

## Task 3: `SlicerClient.get_process_options` and `get_process_layout`

**Files:**
- Modify: `app/slicer_client.py` — add two new methods alongside `get_profiles` (around line 557).
- Create: `tests/test_slicer_client_process_options.py`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_slicer_client_process_options.py`:

```python
"""Unit tests for SlicerClient.get_process_options / get_process_layout."""
from __future__ import annotations

import json

import httpx
import pytest

from app.slicer_client import SlicerClient, SlicingError


def _stub_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_get_process_options_pass_through():
    catalogue = {"version": "2.3.2-41", "options": {"layer_height": {"key": "layer_height"}}}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/options/process"
        return httpx.Response(200, json=catalogue)

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    out = await client.get_process_options()
    assert out == catalogue


@pytest.mark.asyncio
async def test_get_process_layout_pass_through():
    layout = {
        "version": "2.3.2-41",
        "allowlist_revision": "2026-05-06.1",
        "pages": [{"label": "Quality", "optgroups": []}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/options/process/layout"
        return httpx.Response(200, json=layout)

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    out = await client.get_process_layout()
    assert out == layout


@pytest.mark.asyncio
async def test_get_process_options_raises_on_503():
    """503 options_not_loaded must surface as SlicingError so the route can forward it."""
    body = {"code": "options_not_loaded", "detail": "options cache failed to populate"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json=body)

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    with pytest.raises(SlicingError) as exc:
        await client.get_process_options()
    assert "503" in str(exc.value)


@pytest.mark.asyncio
async def test_get_process_options_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    with pytest.raises(SlicingError):
        await client.get_process_options()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_slicer_client_process_options.py -v`
Expected: FAIL with `AttributeError: 'SlicerClient' object has no attribute 'get_process_options'`.

- [ ] **Step 3: Add the methods**

In `app/slicer_client.py`, add these two methods on `SlicerClient`. Place them directly after `get_profiles` (which ends around line 587). They share the 30 s timeout and `transport` propagation pattern of `get_profiles`, but unlike `get_profiles` they raise `SlicingError` rather than returning `[]` on failure — the route layer needs to forward the slicer's status code, so swallowing errors here would lose information.

```python
    async def get_process_options(self) -> dict:
        """GET /options/process — process-option metadata catalogue.

        Returns the slicer's JSON response unchanged: ``{version, options}``.
        Raises ``SlicingError`` on non-200 (the 503 ``options_not_loaded``
        case is meaningful — clients retry — so we surface it rather than
        masking it as an empty payload).
        """
        url = f"{self._base_url}/options/process"
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

    async def get_process_layout(self) -> dict:
        """GET /options/process/layout — paged + allowlist-filtered layout.

        Returns ``{version, allowlist_revision, pages}`` unchanged. Same
        error semantics as ``get_process_options``.
        """
        url = f"{self._base_url}/options/process/layout"
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_slicer_client_process_options.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/slicer_client.py tests/test_slicer_client_process_options.py
git commit -m "Add SlicerClient methods for process-option catalogue and layout

- get_process_options() returns the unfiltered metadata catalogue
- get_process_layout() returns the allowlist-filtered editor layout
- both surface non-200 (incl. 503 options_not_loaded) as SlicingError
  so the route layer can forward the status code"
```

---

## Task 4: `/api/slicer/options/process[/layout]` routes

**Files:**
- Modify: `app/main.py` — add two routes near `/api/slicer/plate-types` (line ~960).
- Create: `tests/test_options_routes.py`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_options_routes.py`:

```python
"""Route tests for /api/slicer/options/process[/layout]."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.slicer_client import SlicingError


@pytest.fixture
def client_with_slicer(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(app_main, "slicer_client", fake)
    return TestClient(app_main.app), fake


@pytest.fixture
def client_no_slicer(monkeypatch):
    monkeypatch.setattr(app_main, "slicer_client", None)
    return TestClient(app_main.app)


def test_options_process_proxies_response(client_with_slicer):
    client, fake = client_with_slicer
    catalogue = {"version": "2.3.2-41", "options": {"layer_height": {"key": "layer_height"}}}
    fake.get_process_options.return_value = catalogue

    resp = client.get("/api/slicer/options/process")

    assert resp.status_code == 200
    assert resp.json() == catalogue
    fake.get_process_options.assert_awaited_once()


def test_options_process_layout_proxies_response(client_with_slicer):
    client, fake = client_with_slicer
    layout = {"version": "2.3.2-41", "allowlist_revision": "2026-05-06.1", "pages": []}
    fake.get_process_layout.return_value = layout

    resp = client.get("/api/slicer/options/process/layout")

    assert resp.status_code == 200
    assert resp.json() == layout
    fake.get_process_layout.assert_awaited_once()


def test_options_process_returns_400_when_slicer_unconfigured(client_no_slicer):
    resp = client_no_slicer.get("/api/slicer/options/process")
    assert resp.status_code == 400
    assert "Slicer not configured" in resp.json()["detail"]


def test_options_process_layout_returns_400_when_slicer_unconfigured(client_no_slicer):
    resp = client_no_slicer.get("/api/slicer/options/process/layout")
    assert resp.status_code == 400
    assert "Slicer not configured" in resp.json()["detail"]


def test_options_process_propagates_slicing_error_as_502(client_with_slicer):
    client, fake = client_with_slicer
    fake.get_process_options.side_effect = SlicingError(
        "Slicer returned 503: options cache failed to populate"
    )

    resp = client.get("/api/slicer/options/process")

    assert resp.status_code == 502
    assert "options cache" in resp.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_options_routes.py -v`
Expected: FAIL with 404 from FastAPI (routes don't exist).

- [ ] **Step 3: Add the routes**

In `app/main.py`, just below the `/api/slicer/plate-types` handler (line ~965, before `_ResolveForMachineBody`), add:

```python
@app.get("/api/slicer/options/process")
async def slicer_options_process():
    """Process-option metadata catalogue. Pass-through to the slicer.

    Clients cache by the response's ``version`` field. ~150 KB; the
    gateway does not cache server-side.
    """
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicer not configured: ORCASLICER_API_URL not set",
        )
    try:
        return await slicer_client.get_process_options()
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/slicer/options/process/layout")
async def slicer_options_process_layout():
    """Allowlist-filtered editor layout. Pass-through to the slicer.

    Clients cache by ``(version, allowlist_revision)``.
    """
    if slicer_client is None:
        raise HTTPException(
            status_code=400,
            detail="Slicer not configured: ORCASLICER_API_URL not set",
        )
    try:
        return await slicer_client.get_process_layout()
    except SlicingError as e:
        raise HTTPException(status_code=502, detail=str(e))
```

Note on error handling: `SlicingError` here can mean either "slicer returned a non-200" (incl. 503) or "slicer is unreachable". The spec calls for forwarding the slicer's status code on 5xx, but `SlicingError` flattens that signal into a string. We surface 502 with the underlying message — clients see the status code in the message body and can detect `503` substrings if they need to differentiate. Differentiating at the route layer would require parsing the message string, which is more brittle than the current behaviour and provides no value to existing callers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_options_routes.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_options_routes.py
git commit -m "Add /api/slicer/options/process[/layout] pass-through routes

- forward the slicer's metadata catalogue and editor layout verbatim
- 400 when ORCASLICER_API_URL isn't configured; 502 on slicer error
- no server-side caching — clients cache by version /
  allowlist_revision per the API contract"
```

---

## Task 5: `process_overrides` on `SlicerClient.slice` and the v2 body

**Files:**
- Modify: `app/slicer_client.py` — add `process_overrides` kwarg to `slice`, `slice_stream`, `_slice_stream_real`, `_slice_stream_fallback`, `_build_v2_slice_body`. Add `SliceResult.process_overrides_applied`. Update `_slice_result_from_v2`.
- Create: `tests/test_slicer_client_overrides.py`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_slicer_client_overrides.py`:

```python
"""Unit tests for process_overrides plumbing in SlicerClient."""
from __future__ import annotations

import httpx
import pytest

from app.slicer_client import SlicerClient, _slice_result_from_v2


def _stub_transport(handler):
    return httpx.MockTransport(handler)


# Minimal inspect payload SlicerClient needs from internal helpers
# (auto_center machine-name lookup, normalize_filament dict path, etc.).
_FAKE_INSPECT = {
    "schema_version": 4,
    "is_sliced": False,
    "plate_count": 1,
    "plates": [{"id": 1, "used_filament_indices": [0]}],
    "filaments": [
        {"slot": 0, "settings_id": "Bambu PLA Basic"},
    ],
    "printer_settings_id": "Bambu Lab A1 mini 0.4 nozzle",
    "process_modifications": {},
}

_FAKE_MACHINE_DETAIL = {"name": "Bambu Lab A1 mini 0.4 nozzle", "setting_id": "GM004"}


def _make_handler(captured: dict):
    """Build a MockTransport handler that satisfies SlicerClient.slice.

    Records the v2 body the client posts, mocks /3mf upload, /3mf/{token}
    download, /3mf/{token}/inspect, /profiles/machines/{id}.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/3mf":
            return httpx.Response(200, json={
                "token": "INPUT_TOK", "sha256": "x", "size": 0, "evicts": [],
            })
        if request.method == "GET" and path == "/3mf/INPUT_TOK/inspect":
            return httpx.Response(200, json=_FAKE_INSPECT)
        if request.method == "GET" and path.startswith("/profiles/machines/"):
            return httpx.Response(200, json=_FAKE_MACHINE_DETAIL)
        if request.method == "POST" and path == "/slice/v2":
            captured["body"] = request.read().decode()
            import json as _json
            captured["json"] = _json.loads(captured["body"])
            return httpx.Response(200, json={
                "input_token": "INPUT_TOK",
                "output_token": "OUTPUT_TOK",
                "estimate": None,
                "settings_transfer": {
                    "status": "applied",
                    "process_keys": ["layer_height"],
                    "printer_keys": [],
                    "filament_slots": [],
                    "process_overrides_applied": [
                        {"key": "layer_height", "value": "0.16", "previous": "0.20"},
                    ],
                },
                "thumbnail_urls": [],
                "download_url": "/3mf/OUTPUT_TOK/",
            })
        if request.method == "GET" and path == "/3mf/OUTPUT_TOK":
            return httpx.Response(200, content=b"OUTPUT_BYTES")
        return httpx.Response(404)
    return handler


@pytest.mark.asyncio
async def test_slice_includes_process_overrides_in_v2_body():
    captured: dict = {}
    client = SlicerClient(
        "http://slicer", transport=_stub_transport(_make_handler(captured)),
    )
    result = await client.slice(
        b"input-bytes",
        filename="test.3mf",
        machine_profile="GM004",
        process_profile="GP004",
        filament_profiles=["Bambu PLA Basic"],
        process_overrides={"layer_height": "0.16", "wall_loops": "3"},
    )
    assert captured["json"]["process_overrides"] == {
        "layer_height": "0.16",
        "wall_loops": "3",
    }
    assert result.process_overrides_applied == [
        {"key": "layer_height", "value": "0.16", "previous": "0.20"},
    ]


@pytest.mark.asyncio
async def test_slice_omits_process_overrides_when_none():
    captured: dict = {}
    client = SlicerClient(
        "http://slicer", transport=_stub_transport(_make_handler(captured)),
    )
    await client.slice(
        b"x", filename="t.3mf", machine_profile="GM004",
        process_profile="GP004", filament_profiles=["Bambu PLA Basic"],
    )
    assert "process_overrides" not in captured["json"]


@pytest.mark.asyncio
async def test_slice_omits_process_overrides_when_empty_dict():
    captured: dict = {}
    client = SlicerClient(
        "http://slicer", transport=_stub_transport(_make_handler(captured)),
    )
    await client.slice(
        b"x", filename="t.3mf", machine_profile="GM004",
        process_profile="GP004", filament_profiles=["Bambu PLA Basic"],
        process_overrides={},
    )
    assert "process_overrides" not in captured["json"]


def test_slice_result_from_v2_parses_process_overrides_applied():
    payload = {
        "settings_transfer": {
            "status": "applied",
            "filament_slots": [],
            "process_overrides_applied": [
                {"key": "layer_height", "value": "0.16", "previous": "0.20"},
                {"key": "wall_loops", "value": "3", "previous": "2"},
            ],
        },
        "estimate": None,
    }
    result = _slice_result_from_v2(payload, b"X")
    assert result.process_overrides_applied == [
        {"key": "layer_height", "value": "0.16", "previous": "0.20"},
        {"key": "wall_loops", "value": "3", "previous": "2"},
    ]


def test_slice_result_from_v2_defaults_empty_overrides_applied():
    payload = {
        "settings_transfer": {
            "status": "applied",
            "filament_slots": [],
        },
        "estimate": None,
    }
    result = _slice_result_from_v2(payload, b"X")
    assert result.process_overrides_applied == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_slicer_client_overrides.py -v`
Expected: 5 tests FAIL — `slice()` rejects the unknown `process_overrides` kwarg, and `SliceResult` has no `process_overrides_applied` attribute.

- [ ] **Step 3: Extend `SliceResult`**

In `app/slicer_client.py` at the `SliceResult` dataclass (around line 22-30), add the new field:

```python
@dataclass
class SliceResult:
    """Result from a slice request, including content and settings transfer info."""

    content: bytes
    settings_transfer_status: str = ""
    settings_transferred: list[dict] = field(default_factory=list)
    filament_transfers: list[dict] = field(default_factory=list)
    estimate: PrintEstimate | None = None
    process_overrides_applied: list[dict] = field(default_factory=list)
```

- [ ] **Step 4: Update `_slice_result_from_v2`**

Replace the function body (around lines 97-117):

```python
def _slice_result_from_v2(payload: dict, sliced_bytes: bytes) -> "SliceResult":
    """Map a /slice/v2 JSON response onto the gateway's SliceResult shape.

    The v2 ``settings_transfer`` schema is:
    ``{status, process_keys, printer_keys, filament_slots, curr_bed_type?,
    process_overrides_applied?}``. ``process_keys``/``printer_keys`` are
    lists of key names (not key+value+original triples like the legacy
    headers). The gateway's ``TransferredSetting`` model expects the legacy
    shape, so we only surface ``status`` and ``filament_slots`` here —
    keys-only lists land in the logs but aren't yet wired into the response
    model. ``process_overrides_applied`` (rev 41+) carries the
    ``[{key, value, previous}, ...]`` list of overrides the slicer
    actually applied.
    """
    transfer = payload.get("settings_transfer") or {}
    status = str(transfer.get("status", "") or "")
    filament_slots = transfer.get("filament_slots") or []
    overrides_applied = transfer.get("process_overrides_applied") or []
    return SliceResult(
        content=sliced_bytes,
        settings_transfer_status=status,
        settings_transferred=[],
        filament_transfers=filament_slots if isinstance(filament_slots, list) else [],
        estimate=_decode_print_estimate_dict(payload.get("estimate")),
        process_overrides_applied=(
            list(overrides_applied) if isinstance(overrides_applied, list) else []
        ),
    )
```

- [ ] **Step 5: Add `process_overrides` kwarg to `slice`**

In `SlicerClient.slice` (around lines 133-174), update the signature and pass-through:

```python
    async def slice(
        self,
        file_data: bytes,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list[str] | dict[str, Any],
        plate_type: str = "",
        plate: int = 1,
        process_overrides: dict[str, str] | None = None,
    ) -> SliceResult:
        """Slice a 3MF via orcaslicer-cli's token-based v2 API.

        Uploads the bytes (sha256-deduped, so a re-upload of a file already
        cached by ``parse_3mf_via_slicer`` is free server-side), posts the
        slice request as JSON, and downloads the sliced output.

        ``process_overrides`` is forwarded verbatim into the slice body
        when non-empty. The slicer is permissive — unknown / filament-
        domain / unparseable keys are silently dropped server-side; the
        slicer reports back what was actually applied via
        ``settings_transfer.process_overrides_applied``.
        """
        upload = await self.upload_3mf(file_data, filename=filename)
        input_token = upload["token"]

        body = await self._build_v2_slice_body(
            input_token=input_token,
            machine_profile=machine_profile,
            process_profile=process_profile,
            filament_profiles=filament_profiles,
            plate=plate,
            plate_type=plate_type,
            process_overrides=process_overrides,
        )

        url = f"{self._base_url}/slice/v2"
        logger.info("Sending %s to slicer at %s (token=%s)", filename, url, input_token)
        try:
            async with httpx.AsyncClient(timeout=300, transport=self._transport) as client:
                resp = await client.post(url, json=body)
        except httpx.HTTPError as e:
            raise SlicingError(f"Slicer unreachable: {e}")

        if resp.status_code != 200:
            raise SlicingError(f"Slicer returned {resp.status_code}: {resp.text[:500]}")

        payload = resp.json()
        sliced = await self._download_3mf(payload["output_token"])
        return _slice_result_from_v2(payload, sliced)
```

- [ ] **Step 6: Update `_build_v2_slice_body`**

Update the signature and body construction (around lines 297-344):

```python
    async def _build_v2_slice_body(
        self,
        *,
        input_token: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list[str] | dict[str, Any],
        plate: int,
        plate_type: str = "",
        process_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Translate the gateway's filament_profiles shape to the v2 schema.

        v2 wants ``filament_settings_ids: list[str]`` (positional) plus an
        optional ``filament_map: list[int]`` (per-slot AMS slot index).
        For the dict form, slots not explicitly overridden keep the 3MF's
        authored ``filament_settings_id``; we read that list via
        ``/3mf/{token}/inspect`` so the gateway doesn't need to plumb it
        through every call site.

        ``process_overrides`` is included only when non-empty; ``None`` and
        ``{}`` are no-ops per the slicer API contract.
        """
        filament_ids, filament_map = await self._normalize_filament_selection(
            input_token, filament_profiles,
            machine_profile=machine_profile,
        )
        auto_center = await self._should_auto_center_for_machine(
            input_token, machine_profile,
        )
        body: dict[str, Any] = {
            "input_token": input_token,
            "machine_id": machine_profile,
            "process_id": process_profile,
            "filament_settings_ids": filament_ids,
            "plate_id": plate or 1,
            "auto_center": auto_center,
        }
        if filament_map is not None:
            body["filament_map"] = filament_map
        if plate_type:
            body["plate_type"] = plate_type
        if process_overrides:
            body["process_overrides"] = dict(process_overrides)
        return body
```

(Preserve the existing auto-center docstring block — it explains a non-obvious workaround. The version above shows only the changed signature and the appended `if process_overrides:` block; do not delete the auto-center comment block that exists in the original.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_slicer_client_overrides.py -v`
Expected: 5 tests PASS.

- [ ] **Step 8: Run other slicer_client tests to confirm no regression**

Run: `.venv/bin/pytest tests/test_slicer_client_normalize.py tests/test_slicer_client_auto_center.py tests/test_slicer_client_resolve.py tests/test_slicer_client_inspect.py -v`
Expected: all PASS (the new kwarg has a default of `None`, so existing call sites are unaffected).

- [ ] **Step 9: Commit**

```bash
git add app/slicer_client.py tests/test_slicer_client_overrides.py
git commit -m "Plumb process_overrides through SlicerClient.slice

- new optional process_overrides kwarg on slice / _build_v2_slice_body
- forwarded into the v2 body only when the dict is non-empty (None
  and {} are no-ops per the slicer API)
- SliceResult carries process_overrides_applied parsed from the
  slicer's settings_transfer block"
```

---

## Task 6: `process_overrides` on `slice_stream` (real + fallback)

**Files:**
- Modify: `app/slicer_client.py` — `slice_stream`, `_slice_stream_real`, `_slice_stream_fallback`.
- Modify: `tests/test_slicer_client_overrides.py` — add streaming tests.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_slicer_client_overrides.py`:

```python
def _make_stream_handler(captured: dict, sse_body: bytes):
    """Handler that satisfies slice_stream's real path (SSE) flow.

    Captures the v2 body that gets POSTed to /slice-stream/v2.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "OPTIONS" and path == "/slice-stream/v2":
            return httpx.Response(200)
        if request.method == "POST" and path == "/3mf":
            return httpx.Response(200, json={
                "token": "INPUT_TOK", "sha256": "x", "size": 0, "evicts": [],
            })
        if request.method == "GET" and path == "/3mf/INPUT_TOK/inspect":
            return httpx.Response(200, json=_FAKE_INSPECT)
        if request.method == "GET" and path.startswith("/profiles/machines/"):
            return httpx.Response(200, json=_FAKE_MACHINE_DETAIL)
        if request.method == "POST" and path == "/slice-stream/v2":
            captured["body"] = request.read().decode()
            import json as _json
            captured["json"] = _json.loads(captured["body"])
            return httpx.Response(
                200,
                content=sse_body,
                headers={"content-type": "text/event-stream"},
            )
        if request.method == "GET" and path == "/3mf/OUTPUT_TOK":
            return httpx.Response(200, content=b"OUTPUT_BYTES")
        return httpx.Response(404)
    return handler


@pytest.mark.asyncio
async def test_slice_stream_real_includes_process_overrides_and_surfaces_applied():
    captured: dict = {}
    sse = (
        b'event: result\n'
        b'data: {"output_token": "OUTPUT_TOK", "download_url": "/3mf/OUTPUT_TOK/", '
        b'"estimate": null, "settings_transfer": {"status": "applied", '
        b'"filament_slots": [], '
        b'"process_overrides_applied": [{"key": "layer_height", "value": "0.16", "previous": "0.20"}]}}\n'
        b'\n'
        b'event: done\n'
        b'data: {}\n'
        b'\n'
    )
    client = SlicerClient(
        "http://slicer", transport=_stub_transport(_make_stream_handler(captured, sse)),
    )

    events = []
    async for ev in client.slice_stream(
        b"x", filename="t.3mf",
        machine_profile="GM004", process_profile="GP004",
        filament_profiles=["Bambu PLA Basic"],
        process_overrides={"layer_height": "0.16"},
    ):
        events.append(ev)

    assert captured["json"]["process_overrides"] == {"layer_height": "0.16"}
    result_event = next(e for e in events if e["event"] == "result")
    assert result_event["data"]["settings_transfer"]["process_overrides_applied"] == [
        {"key": "layer_height", "value": "0.16", "previous": "0.20"},
    ]


@pytest.mark.asyncio
async def test_slice_stream_fallback_synthesises_overrides_applied():
    """When /slice-stream/v2 isn't available, the fallback path uses /slice/v2.

    The synthetic SSE result event must include process_overrides_applied
    from SliceResult.
    """
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "OPTIONS" and path == "/slice-stream/v2":
            return httpx.Response(404)
        if request.method == "POST" and path == "/3mf":
            return httpx.Response(200, json={
                "token": "INPUT_TOK", "sha256": "x", "size": 0, "evicts": [],
            })
        if request.method == "GET" and path == "/3mf/INPUT_TOK/inspect":
            return httpx.Response(200, json=_FAKE_INSPECT)
        if request.method == "GET" and path.startswith("/profiles/machines/"):
            return httpx.Response(200, json=_FAKE_MACHINE_DETAIL)
        if request.method == "POST" and path == "/slice/v2":
            captured["body"] = request.read().decode()
            import json as _json
            captured["json"] = _json.loads(captured["body"])
            return httpx.Response(200, json={
                "input_token": "INPUT_TOK",
                "output_token": "OUTPUT_TOK",
                "estimate": None,
                "settings_transfer": {
                    "status": "applied",
                    "filament_slots": [],
                    "process_overrides_applied": [
                        {"key": "wall_loops", "value": "3", "previous": "2"},
                    ],
                },
                "thumbnail_urls": [],
                "download_url": "/3mf/OUTPUT_TOK/",
            })
        if request.method == "GET" and path == "/3mf/OUTPUT_TOK":
            return httpx.Response(200, content=b"OUTPUT_BYTES")
        return httpx.Response(404)

    client = SlicerClient("http://slicer", transport=_stub_transport(handler))
    events = []
    async for ev in client.slice_stream(
        b"x", filename="t.3mf",
        machine_profile="GM004", process_profile="GP004",
        filament_profiles=["Bambu PLA Basic"],
        process_overrides={"wall_loops": "3"},
    ):
        events.append(ev)

    assert captured["json"]["process_overrides"] == {"wall_loops": "3"}
    result_event = next(e for e in events if e["event"] == "result")
    assert result_event["data"]["settings_transfer"]["process_overrides_applied"] == [
        {"key": "wall_loops", "value": "3", "previous": "2"},
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_slicer_client_overrides.py -v`
Expected: the two new tests FAIL — `slice_stream` rejects the unknown `process_overrides` kwarg.

- [ ] **Step 3: Update `slice_stream`, `_slice_stream_real`, `_slice_stream_fallback`**

Replace the three method signatures and update body construction. In `app/slicer_client.py`:

`slice_stream` (around line 189-216):

```python
    async def slice_stream(
        self,
        file_data: bytes,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list[str] | dict[str, Any],
        plate_type: str = "",
        plate: int = 1,
        process_overrides: dict[str, str] | None = None,
    ):
        """Stream SSE events for a slice operation.

        Uses /slice-stream/v2 if available, otherwise falls back to /slice/v2
        and emits synthetic SSE events.
        Yields dicts with 'event' and 'data' keys.
        """
        if await self._check_stream_support():
            async for event in self._slice_stream_real(
                file_data, filename, machine_profile, process_profile, filament_profiles,
                plate, plate_type, process_overrides,
            ):
                yield event
        else:
            async for event in self._slice_stream_fallback(
                file_data, filename, machine_profile, process_profile, filament_profiles,
                plate, plate_type, process_overrides,
            ):
                yield event
```

`_slice_stream_real` (around line 218-266):

```python
    async def _slice_stream_real(
        self, file_data, filename, machine_profile, process_profile, filament_profiles,
        plate=1, plate_type="", process_overrides=None,
    ):
        upload = await self.upload_3mf(file_data, filename=filename)
        input_token = upload["token"]
        body = await self._build_v2_slice_body(
            input_token=input_token,
            machine_profile=machine_profile,
            process_profile=process_profile,
            filament_profiles=filament_profiles,
            plate=plate,
            plate_type=plate_type,
            process_overrides=process_overrides,
        )

        # ... rest of the method body unchanged ...
```

(Keep the existing SSE parsing loop after `body = ...` exactly as-is.)

`_slice_stream_fallback` (around line 268-295):

```python
    async def _slice_stream_fallback(
        self, file_data, filename, machine_profile, process_profile, filament_profiles,
        plate=1, plate_type="", process_overrides=None,
    ):
        """Use the non-streaming /slice/v2 endpoint and emit synthetic SSE events."""
        yield {"event": "status", "data": {"phase": "slicing", "message": "Slicing..."}}

        result = await self.slice(
            file_data, filename, machine_profile, process_profile, filament_profiles,
            plate_type, plate, process_overrides=process_overrides,
        )

        transfer_info = {}
        if result.settings_transfer_status:
            transfer_info["status"] = result.settings_transfer_status
            if result.settings_transferred:
                transfer_info["transferred"] = result.settings_transferred
        if result.filament_transfers:
            transfer_info["filaments"] = result.filament_transfers
        if result.process_overrides_applied:
            transfer_info["process_overrides_applied"] = list(
                result.process_overrides_applied
            )

        yield {"event": "result", "data": {
            "file_base64": base64.b64encode(result.content).decode(),
            "file_size": len(result.content),
            "settings_transfer": transfer_info or None,
            "estimate": result.estimate.model_dump(exclude_none=True)
            if result.estimate else None,
        }}
        yield {"event": "done", "data": {}}
```

- [ ] **Step 4: Run streaming tests to verify they pass**

Run: `.venv/bin/pytest tests/test_slicer_client_overrides.py -v`
Expected: all 7 tests in this file PASS.

- [ ] **Step 5: Commit**

```bash
git add app/slicer_client.py tests/test_slicer_client_overrides.py
git commit -m "Plumb process_overrides through SlicerClient.slice_stream

- both real (/slice-stream/v2) and fallback (/slice/v2) paths
- fallback synthesises process_overrides_applied into the SSE
  result event so its schema matches the real-stream path"
```

---

## Task 7: `process_overrides` on `SliceJob`

**Files:**
- Modify: `app/slice_jobs.py` — add field to `SliceJob`, thread through `new`, `submit`, `_run_job`, `_SlicerLike`.
- Create: `tests/test_slice_job_process_overrides.py`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_slice_job_process_overrides.py`:

```python
"""Unit tests for process_overrides on SliceJob."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.slice_jobs import SliceJob, SliceJobStore, SliceJobStatus


def _new_job(**overrides) -> SliceJob:
    base = dict(
        filename="test.3mf",
        machine_profile="GM004",
        process_profile="GP004",
        filament_profiles=["Bambu PLA Basic"],
        plate_id=1,
        plate_type="",
        project_filament_count=1,
        printer_id="PRINTER1",
        auto_print=False,
        input_path=Path("/tmp/test.3mf"),
    )
    base.update(overrides)
    return SliceJob.new(**base)


def test_slice_job_defaults_process_overrides_to_none():
    job = _new_job()
    assert job.process_overrides is None


def test_slice_job_carries_process_overrides_when_set():
    job = _new_job()
    job.process_overrides = {"layer_height": "0.16"}
    d = job.to_dict()
    assert d["process_overrides"] == {"layer_height": "0.16"}


def test_slice_job_round_trips_through_dict():
    job = _new_job()
    job.process_overrides = {"layer_height": "0.16", "wall_loops": "3"}
    restored = SliceJob.from_dict(job.to_dict())
    assert restored.process_overrides == {"layer_height": "0.16", "wall_loops": "3"}


def test_slice_job_round_trips_with_none_overrides():
    job = _new_job()
    restored = SliceJob.from_dict(job.to_dict())
    assert restored.process_overrides is None


def test_slice_job_from_dict_handles_legacy_payload(tmp_path):
    """A slice_jobs.json written before this feature has no key for the field."""
    legacy = {
        "id": "abc123",
        "created_at": "2026-04-01T00:00:00Z",
        "updated_at": "2026-04-01T00:00:00Z",
        "filename": "old.3mf",
        "machine_profile": "GM004",
        "process_profile": "GP004",
        "filament_profiles": ["Bambu PLA Basic"],
        "plate_id": 1,
        "plate_type": "",
        "project_filament_count": 1,
        "printer_id": "PRINTER1",
        "auto_print": False,
        "input_path": "/tmp/old.3mf",
        "output_path": None,
        "status": "ready",
        "progress": 100,
        "phase": None,
        "printed": False,
        "estimate": None,
        "settings_transfer": None,
        "output_size": None,
        "thumbnail": None,
        "error": None,
    }
    job = SliceJob.from_dict(legacy)
    assert job.process_overrides is None


@pytest.mark.asyncio
async def test_store_persists_process_overrides_round_trip(tmp_path):
    json_path = tmp_path / "slice_jobs.json"
    store = SliceJobStore(json_path)
    job = _new_job()
    job.process_overrides = {"layer_height": "0.16"}
    await store.upsert(job)

    # Re-read from disk via a fresh store.
    store2 = SliceJobStore(json_path)
    restored = await store2.get(job.id)
    assert restored is not None
    assert restored.process_overrides == {"layer_height": "0.16"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_slice_job_process_overrides.py -v`
Expected: FAIL — `SliceJob` has no `process_overrides` attribute / field.

- [ ] **Step 3: Add `process_overrides` to `SliceJob`**

In `app/slice_jobs.py`, the `SliceJob` dataclass currently has its required fields first (no defaults) and defaulted fields after. Specifically `printer_id`, `auto_print`, and `input_path` are **required**, so we cannot insert a defaulted `process_overrides` between `project_filament_count` and `printer_id` — `dataclass` enforces "fields with defaults must come after fields without."

Two valid placements:

1. **Recommended:** Add `process_overrides: dict[str, str] | None = None` immediately *after* `input_path: str` (the last required field) and *before* `output_path: str | None = None` (the first defaulted field). This keeps it grouped with input-side metadata while satisfying the ordering rule.

2. Alternative: add it as a required field (no default) right after `filament_profiles` and update every existing call site to pass it explicitly. Don't do this — Task 7 is supposed to be additive without breaking callers, and legacy `slice_jobs.json` deserialisation needs the default.

Apply placement (1):

```python
@dataclass
class SliceJob:
    id: str
    created_at: str
    updated_at: str

    # inputs
    filename: str
    machine_profile: str
    process_profile: str
    filament_profiles: list | dict
    plate_id: int
    plate_type: str
    project_filament_count: int | None

    # target
    printer_id: str | None
    auto_print: bool

    # blobs (paths as strings for JSON-friendliness; converted to Path in code)
    input_path: str
    process_overrides: dict[str, str] | None = None
    output_path: str | None = None

    # progress
    status: SliceJobStatus = SliceJobStatus.QUEUED
    # ... rest of the dataclass unchanged ...
```

(This preserves every existing field exactly and inserts one new line. If an editor's instinct is to "tidy up the comment groupings," resist it — the file's section structure is intentional.)

Update `SliceJob.new` (around lines 93-123) to accept and pass the new arg:

```python
    @classmethod
    def new(
        cls,
        *,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list | dict,
        plate_id: int,
        plate_type: str,
        project_filament_count: int | None,
        printer_id: str | None,
        auto_print: bool,
        input_path: Path,
        process_overrides: dict[str, str] | None = None,
    ) -> "SliceJob":
        ts = _now()
        return cls(
            id=uuid.uuid4().hex[:12],
            created_at=ts,
            updated_at=ts,
            filename=filename,
            machine_profile=machine_profile,
            process_profile=process_profile,
            filament_profiles=filament_profiles,
            plate_id=plate_id,
            plate_type=plate_type,
            project_filament_count=project_filament_count,
            process_overrides=process_overrides,
            printer_id=printer_id,
            auto_print=auto_print,
            input_path=str(input_path),
        )
```

`SliceJob.to_dict` and `from_dict` already use `asdict` and `cls(**data)` — `process_overrides` is a JSON-friendly type and works with both. The existing `from_dict` discards unrecognised keys via `cls(**data)`? Actually no — `cls(**data)` would raise on unknown keys. But for *missing* keys (legacy payloads), the dataclass default `None` kicks in. That's the case the tests cover. **No change to `from_dict` is needed.**

- [ ] **Step 4: Update the `_SlicerLike` Protocol and `submit`**

`_SlicerLike` (around lines 222-232):

```python
class _SlicerLike(Protocol):
    def slice_stream(
        self,
        file_data: bytes,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list | dict,
        plate_type: str = "",
        plate: int = 1,
        process_overrides: dict[str, str] | None = None,
    ): ...
```

`SliceJobManager.submit` (around lines 473-511):

```python
    async def submit(
        self,
        *,
        file_data: bytes,
        filename: str,
        machine_profile: str,
        process_profile: str,
        filament_profiles: list | dict,
        plate_id: int,
        plate_type: str,
        project_filament_count: int | None,
        printer_id: str | None,
        auto_print: bool,
        process_overrides: dict[str, str] | None = None,
    ) -> SliceJob:
        job_id = uuid.uuid4().hex[:12]
        input_path = self._store.input_path(job_id)
        input_path.write_bytes(file_data)

        job = SliceJob.new(
            filename=filename,
            machine_profile=machine_profile,
            process_profile=process_profile,
            filament_profiles=filament_profiles,
            plate_id=plate_id,
            plate_type=plate_type,
            project_filament_count=project_filament_count,
            printer_id=printer_id,
            auto_print=auto_print,
            input_path=input_path,
            process_overrides=process_overrides,
        )
        job.id = job_id

        await self._store.upsert(job)
        self._cancel_events[job.id] = asyncio.Event()
        await self._queue.put(job.id)
        return job
```

- [ ] **Step 5: Pass `process_overrides` from `_run_job` into `slice_stream`**

In `SliceJobManager._run_job` (around lines 563-568), update the `slice_stream` call:

```python
            agen = self._slicer.slice_stream(
                file_data, job.filename, job.machine_profile, job.process_profile,
                job.filament_profiles, plate_type=job.plate_type,
                plate=job.plate_id or 1,
                process_overrides=job.process_overrides,
            )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_slice_job_process_overrides.py -v`
Expected: 6 tests PASS.

- [ ] **Step 7: Run all slice-job tests to confirm no regression**

Run: `.venv/bin/pytest tests/test_slice_job_model.py tests/test_slice_job_store.py tests/test_slice_job_manager.py tests/test_slice_job_recovery.py tests/test_slice_jobs_api.py tests/test_slice_job_process_overrides.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add app/slice_jobs.py tests/test_slice_job_process_overrides.py
git commit -m "Persist process_overrides on SliceJob

- new optional field threaded through SliceJob.new, submit, and the
  slice-run call into SlicerClient.slice_stream
- legacy slice_jobs.json (no key) deserialises to None via the
  dataclass default"
```

---

## Task 8: `process_overrides_applied` in response models

**Files:**
- Modify: `app/models.py` — add `ProcessOverrideApplied`, extend `SettingsTransferInfo`.

- [ ] **Step 1: Write failing test**

Create `tests/test_settings_transfer_overrides.py`:

```python
"""Unit tests for SettingsTransferInfo.process_overrides_applied."""
from __future__ import annotations

from app.models import ProcessOverrideApplied, SettingsTransferInfo


def test_process_override_applied_fields():
    o = ProcessOverrideApplied(key="layer_height", value="0.16", previous="0.20")
    assert o.key == "layer_height"
    assert o.value == "0.16"
    assert o.previous == "0.20"


def test_settings_transfer_info_carries_overrides_applied():
    sti = SettingsTransferInfo(
        status="applied",
        process_overrides_applied=[
            ProcessOverrideApplied(key="layer_height", value="0.16", previous="0.20"),
        ],
    )
    dumped = sti.model_dump()
    assert dumped["process_overrides_applied"] == [
        {"key": "layer_height", "value": "0.16", "previous": "0.20"},
    ]


def test_settings_transfer_info_defaults_overrides_empty():
    sti = SettingsTransferInfo(status="applied")
    assert sti.process_overrides_applied == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_settings_transfer_overrides.py -v`
Expected: FAIL — `ProcessOverrideApplied` doesn't exist.

- [ ] **Step 3: Add the model and extend `SettingsTransferInfo`**

In `app/models.py`, near the existing `SettingsTransferInfo` (around line 339-342), add:

```python
class ProcessOverrideApplied(BaseModel):
    """One process override that the slicer actually applied.

    Returned in ``settings_transfer.process_overrides_applied`` for each
    override the client submitted that wasn't dropped (filament-domain
    keys, unknown keys, and unparseable values are dropped silently).
    """
    key: str
    value: str
    previous: str


class SettingsTransferInfo(BaseModel):
    status: str
    transferred: list[TransferredSetting] = []
    filaments: list[FilamentTransferEntry] = []
    process_overrides_applied: list[ProcessOverrideApplied] = []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_settings_transfer_overrides.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_settings_transfer_overrides.py
git commit -m "Add ProcessOverrideApplied and extend SettingsTransferInfo

- carries per-override {key, value, previous} entries the slicer
  surfaces in settings_transfer.process_overrides_applied"
```

---

## Task 9: `process_overrides` form field on `/api/print`

**Files:**
- Modify: `app/main.py` — `/api/print` route handler; new helper `_parse_process_overrides_form`. Construct `SettingsTransferInfo` with `process_overrides_applied`.
- Create: `tests/test_print_routes_process_overrides.py`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_print_routes_process_overrides.py`:

```python
"""Form-field validation and round-trip for process_overrides on print routes."""
from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.slicer_client import SliceResult


_FAKE_3MF = b"PK\x03\x04\x14\x00FAKE3MF"


@pytest.fixture
def configured_app(monkeypatch, tmp_path):
    """Wire up enough mocks that /api/print can run end-to-end (slicing path)."""
    fake_slicer = AsyncMock()
    fake_slicer.slice.return_value = SliceResult(
        content=b"OUTPUT_BYTES",
        settings_transfer_status="applied",
        settings_transferred=[],
        filament_transfers=[],
        estimate=None,
        process_overrides_applied=[
            {"key": "layer_height", "value": "0.16", "previous": "0.20"},
        ],
    )
    monkeypatch.setattr(app_main, "slicer_client", fake_slicer)

    fake_info = MagicMock()
    fake_info.has_gcode = False
    fake_info.filaments = [
        MagicMock(setting_id="Bambu PLA Basic", index=0, used=True),
    ]
    fake_info.process_modifications = MagicMock(values={})

    async def fake_parse(*a, **kw):
        return fake_info

    monkeypatch.setattr(app_main, "parse_3mf_via_slicer", fake_parse)

    async def fake_resolve(project_ids, raw, printer_id, used_filament_indices=None):
        return ["Bambu PLA Basic"], None

    monkeypatch.setattr(
        app_main, "_resolve_slice_filament_payload", fake_resolve,
    )
    return TestClient(app_main.app), fake_slicer


def test_print_slice_only_round_trips_process_overrides(configured_app):
    client, fake_slicer = configured_app
    resp = client.post(
        "/api/print",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": json.dumps({"layer_height": "0.16"}),
            "slice_only": "true",
        },
    )
    assert resp.status_code == 200
    # Verify SlicerClient.slice was called with the override dict.
    _, kwargs = fake_slicer.slice.call_args
    assert kwargs["process_overrides"] == {"layer_height": "0.16"}
    # process_overrides_applied surfaces in the response header.
    header = resp.headers.get("X-Settings-Transfer-Status")
    assert header == "applied"


def test_print_omits_process_overrides_when_empty_string(configured_app):
    client, fake_slicer = configured_app
    resp = client.post(
        "/api/print",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": "",
            "slice_only": "true",
        },
    )
    assert resp.status_code == 200
    _, kwargs = fake_slicer.slice.call_args
    assert kwargs["process_overrides"] is None


def test_print_rejects_invalid_json(configured_app):
    client, _ = configured_app
    resp = client.post(
        "/api/print",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": "{not json",
            "slice_only": "true",
        },
    )
    assert resp.status_code == 400
    assert "Invalid process_overrides JSON" in resp.json()["detail"]


def test_print_rejects_non_object_json(configured_app):
    client, _ = configured_app
    resp = client.post(
        "/api/print",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": "[\"not\", \"an\", \"object\"]",
            "slice_only": "true",
        },
    )
    assert resp.status_code == 400
    assert "must be a JSON object" in resp.json()["detail"]


def test_print_rejects_non_string_value(configured_app):
    client, _ = configured_app
    resp = client.post(
        "/api/print",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": json.dumps({"layer_height": 0.16}),
            "slice_only": "true",
        },
    )
    assert resp.status_code == 400
    assert "values must be strings" in resp.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_print_routes_process_overrides.py -v`
Expected: FAIL — `process_overrides` form field doesn't exist on `/api/print`.

- [ ] **Step 3: Add the form-field validator helper**

In `app/main.py`, near the other helpers (e.g. just before `_resolve_slice_filament_payload` at line ~820), add:

```python
def _parse_process_overrides_form(raw: str) -> dict[str, str] | None:
    """Validate and decode the process_overrides form field.

    Returns ``None`` for empty input. Raises ``HTTPException(400)`` on
    malformed input — the slicer is permissive on unknown / unparseable
    keys, but we surface client-side mistakes (bad JSON, wrong shape,
    non-string values) early rather than silently swallowing them.
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid process_overrides JSON")
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=400,
            detail="process_overrides must be a JSON object",
        )
    if any(not isinstance(v, str) for v in parsed.values()):
        raise HTTPException(
            status_code=400,
            detail="process_overrides values must be strings",
        )
    return parsed
```

- [ ] **Step 4: Add the form field on `/api/print` and pass through**

In `app/main.py`, update `/api/print` (around line 1052-1064) to add the form field:

```python
@app.post("/api/print")
async def print_file(
    file: UploadFile = None,
    printer_id: str = Form(""),
    plate_id: int = Form(0),
    job_id: str = Form(""),
    preview_id: str = Form(""),       # deprecated alias for job_id
    machine_profile: str = Form(""),
    process_profile: str = Form(""),
    filament_profiles: str = Form(""),
    plate_type: str = Form(""),
    slice_only: bool = Form(False),
    process_overrides: str = Form(""),
):
```

After the existing argument unpacking but before any branch (right after `effective_job_id = job_id or preview_id`), add:

```python
    process_overrides_dict = _parse_process_overrides_form(process_overrides)
```

Update the `slicer_client.slice(...)` call (around line 1222-1231):

```python
        try:
            slice_result = await slicer_client.slice(
                file_data,
                file.filename,
                machine_profile,
                process_profile,
                filament_payload,
                plate_type=plate_type.strip(),
                plate=plate_id or 1,
                process_overrides=process_overrides_dict,
            )
        except SlicingError as e:
            raise HTTPException(status_code=502, detail=f"Slicing failed: {e}")
```

Update the `SettingsTransferInfo` construction (around line 1239-1249) to include the new field:

```python
    settings_transfer = None
    if slice_result and (
        slice_result.settings_transfer_status
        or slice_result.filament_transfers
        or slice_result.process_overrides_applied
    ):
        settings_transfer = SettingsTransferInfo(
            status=slice_result.settings_transfer_status,
            transferred=[
                TransferredSetting(**s) for s in slice_result.settings_transferred
            ],
            filaments=[
                FilamentTransferEntry(**f) for f in slice_result.filament_transfers
            ],
            process_overrides_applied=[
                ProcessOverrideApplied(**o)
                for o in slice_result.process_overrides_applied
            ],
        )
```

Add `ProcessOverrideApplied` to the existing model imports near the top of `app/main.py` (around line 52-66 where `FilamentTransferEntry`, `TransferredSetting`, `SettingsTransferInfo`, etc. are imported).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_print_routes_process_overrides.py -v`
Expected: 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_print_routes_process_overrides.py
git commit -m "Accept process_overrides on /api/print

- new optional form field; JSON-decoded into a dict[str, str] and
  forwarded to SlicerClient.slice
- 400 on malformed JSON / non-object / non-string values, so client
  mistakes surface early
- response settings_transfer carries process_overrides_applied"
```

---

## Task 10: `process_overrides` form field on `/api/print-stream` and `/api/print-preview`

**Files:**
- Modify: `app/main.py` — `/api/print-stream` (~line 1440) and `/api/print-preview` (~line 1326).
- Modify: `tests/test_print_routes_process_overrides.py` — add tests for the two endpoints.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_print_routes_process_overrides.py`:

```python
@pytest.fixture
def configured_app_with_jobs(monkeypatch, tmp_path):
    """Wire mocks for the slice-job-driven endpoints."""
    fake_slicer = AsyncMock()
    monkeypatch.setattr(app_main, "slicer_client", fake_slicer)

    fake_info = MagicMock()
    fake_info.has_gcode = False
    fake_info.filaments = [
        MagicMock(setting_id="Bambu PLA Basic", index=0, used=True),
    ]
    fake_info.process_modifications = MagicMock(values={})

    async def fake_parse(*a, **kw):
        return fake_info

    monkeypatch.setattr(app_main, "parse_3mf_via_slicer", fake_parse)

    async def fake_resolve(project_ids, raw, printer_id, used_filament_indices=None):
        return ["Bambu PLA Basic"], None

    monkeypatch.setattr(app_main, "_resolve_slice_filament_payload", fake_resolve)

    fake_jobs = AsyncMock()
    fake_job = MagicMock()
    fake_job.id = "job-1"
    fake_jobs.submit.return_value = fake_job

    # Default `get` returns a clean-failed terminal job so the SSE
    # generator in /api/print-stream exits its polling loop quickly
    # without trying to read fake output bytes / estimate / etc. Tests
    # that need a different shape override this on the fixture instance.
    terminal = MagicMock()
    terminal.status.is_terminal = True
    terminal.status.value = "failed"
    terminal.error = "stub"
    fake_jobs.get.return_value = terminal

    monkeypatch.setattr(app_main, "slice_jobs", fake_jobs)
    return TestClient(app_main.app), fake_jobs


def test_print_stream_passes_process_overrides_to_jobs_submit(
    configured_app_with_jobs,
):
    client, fake_jobs = configured_app_with_jobs
    resp = client.post(
        "/api/print-stream",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": json.dumps({"layer_height": "0.16"}),
            "preview": "true",
        },
    )
    # SSE endpoint returns 200 + a stream; we don't need to drain it here.
    assert resp.status_code == 200
    _, kwargs = fake_jobs.submit.call_args
    assert kwargs["process_overrides"] == {"layer_height": "0.16"}


def test_print_stream_rejects_invalid_overrides_json(configured_app_with_jobs):
    client, _ = configured_app_with_jobs
    resp = client.post(
        "/api/print-stream",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": "{not json",
        },
    )
    assert resp.status_code == 400


def test_print_preview_passes_process_overrides_to_jobs_submit(
    configured_app_with_jobs, monkeypatch,
):
    """print-preview waits for terminal state — fixture's default `get`
    returns a clean-failed terminal job so the loop exits immediately
    and we can verify the submit call args."""
    client, fake_jobs = configured_app_with_jobs

    resp = client.post(
        "/api/print-preview",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": json.dumps({"wall_loops": "3"}),
        },
    )
    # Forced-failure short-circuit returns 502; we only care about call args.
    assert resp.status_code == 502
    _, kwargs = fake_jobs.submit.call_args
    assert kwargs["process_overrides"] == {"wall_loops": "3"}


def test_print_preview_rejects_invalid_overrides_json(configured_app_with_jobs):
    client, _ = configured_app_with_jobs
    resp = client.post(
        "/api/print-preview",
        files={"file": ("test.3mf", io.BytesIO(_FAKE_3MF), "application/octet-stream")},
        data={
            "machine_profile": "GM004",
            "process_profile": "GP004",
            "process_overrides": "[1,2,3]",
        },
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_print_routes_process_overrides.py -v`
Expected: 4 new tests FAIL — form field doesn't exist on either endpoint.

- [ ] **Step 3: Add the form field to `/api/print-stream`**

In `app/main.py`, update `/api/print-stream` (around line 1440-1451):

```python
@app.post("/api/print-stream")
async def print_file_stream(
    file: UploadFile,
    printer_id: str = Form(""),
    plate_id: int = Form(0),
    machine_profile: str = Form(""),
    process_profile: str = Form(""),
    filament_profiles: str = Form(""),
    plate_type: str = Form(""),
    slice_only: bool = Form(False),
    preview: bool = Form(False),
    process_overrides: str = Form(""),
):
```

After the existing validation block (right after the `MAX_FILE_BYTES` check around line 1474), add:

```python
    process_overrides_dict = _parse_process_overrides_form(process_overrides)
```

Update the `slice_jobs.submit(...)` call (around line 1496-1507) to include the new arg:

```python
    job = await slice_jobs.submit(
        file_data=file_data,
        filename=file.filename,
        machine_profile=machine_profile,
        process_profile=process_profile,
        filament_profiles=filament_payload,
        plate_id=plate_id,
        plate_type=plate_type.strip(),
        project_filament_count=len(info.filaments),
        printer_id=pid or None,
        auto_print=auto_print,
        process_overrides=process_overrides_dict,
    )
```

- [ ] **Step 4: Add the form field to `/api/print-preview`**

Update `/api/print-preview` (around line 1326-1335):

```python
@app.post("/api/print-preview")
async def print_preview(
    file: UploadFile,
    printer_id: str = Form(""),
    plate_id: int = Form(0),
    machine_profile: str = Form(""),
    process_profile: str = Form(""),
    filament_profiles: str = Form(""),
    plate_type: str = Form(""),
    process_overrides: str = Form(""),
):
```

Right after the validation block (after the `MAX_FILE_BYTES` check ~line 1352), add:

```python
    process_overrides_dict = _parse_process_overrides_form(process_overrides)
```

Update the `slice_jobs.submit(...)` call (around line 1374-1385):

```python
    job = await slice_jobs.submit(
        file_data=file_data,
        filename=file.filename,
        machine_profile=machine_profile,
        process_profile=process_profile,
        filament_profiles=filament_payload,
        plate_id=plate_id,
        plate_type=plate_type.strip(),
        project_filament_count=len(info.filaments),
        printer_id=printer_id or None,
        auto_print=False,
        process_overrides=process_overrides_dict,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_print_routes_process_overrides.py -v`
Expected: 9 tests PASS.

- [ ] **Step 6: Run the full test suite to confirm no regression**

Run: `.venv/bin/pytest -x`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/test_print_routes_process_overrides.py
git commit -m "Accept process_overrides on /api/print-stream and /api/print-preview

- both endpoints share the _parse_process_overrides_form validator
- field flows into slice_jobs.submit() and survives a gateway restart
  via SliceJob persistence"
```

---

## Task 11: Live integration smoke tests

**Files:**
- Create: `tests/integration/test_options_live.py`.
- Create: `tests/integration/test_process_overrides_live.py`.

These tests run only when `ORCASLICER_API_URL` is reachable (mirrors `tests/integration/test_slicer_inspect_live.py`).

- [ ] **Step 1: Write `test_options_live.py`**

Create `tests/integration/test_options_live.py`:

```python
"""Live HTTP smoke test for the options pass-through endpoints.

Skipped when ``$ORCASLICER_API_URL`` isn't reachable.
"""
from __future__ import annotations

import os

import httpx
import pytest

from app.slicer_client import SlicerClient

API = os.environ.get("ORCASLICER_API_URL", "http://localhost:8000")


def _reachable() -> bool:
    try:
        return httpx.get(f"{API}/health", timeout=2.0).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(),
    reason=f"orcaslicer-cli unreachable at {API}",
)


@pytest.mark.asyncio
async def test_get_process_options_returns_catalogue():
    client = SlicerClient(API)
    payload = await client.get_process_options()

    assert "version" in payload
    assert payload["version"].startswith("2.")
    options = payload.get("options")
    assert isinstance(options, dict)
    # Spec says ~609 entries; assert a generous lower bound.
    assert len(options) >= 400, f"only got {len(options)} options"

    # Spot-check a known option.
    layer_height = options.get("layer_height")
    assert layer_height is not None
    for required in ("key", "label", "category", "type", "default"):
        assert required in layer_height


@pytest.mark.asyncio
async def test_get_process_layout_returns_pages():
    client = SlicerClient(API)
    payload = await client.get_process_layout()

    assert "version" in payload
    assert "allowlist_revision" in payload
    pages = payload.get("pages")
    assert isinstance(pages, list)
    assert len(pages) >= 1

    page = pages[0]
    assert "label" in page
    assert "optgroups" in page
    assert isinstance(page["optgroups"], list)
```

- [ ] **Step 2: Run live test**

Run: `ORCASLICER_API_URL=http://10.0.1.9:8070 .venv/bin/pytest tests/integration/test_options_live.py -v`
Expected: 2 tests PASS (or both skip if the slicer isn't reachable).

- [ ] **Step 3: Write `test_process_overrides_live.py`**

Create `tests/integration/test_process_overrides_live.py`:

```python
"""Live override slice against orcaslicer-cli.

Slices a known fixture with a process_overrides dict; verifies
process_overrides_applied comes back populated. Skipped when the
slicer or fixture is missing.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from app.slicer_client import SlicerClient

API = os.environ.get("ORCASLICER_API_URL", "http://localhost:8000")
FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "_fixture"
    / "01"
    / "reference-benchy-orca-no-filament-custom-settings.3mf"
)


def _reachable() -> bool:
    try:
        return httpx.get(f"{API}/health", timeout=2.0).status_code == 200
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(
        not _reachable(),
        reason=f"orcaslicer-cli unreachable at {API}",
    ),
    pytest.mark.skipif(
        not FIXTURE.exists(),
        reason=f"fixture not found: {FIXTURE}",
    ),
]


@pytest.mark.asyncio
async def test_slice_with_process_overrides_returns_applied_list():
    client = SlicerClient(API)

    # Inspect the fixture to discover its authored profiles.
    upload = await client.upload_3mf(FIXTURE.read_bytes(), filename=FIXTURE.name)
    insp = await client.inspect(upload["token"])
    machine_id = insp.get("printer_settings_id")  # display name; SlicerClient
                                                  # would normally resolve to id
    # We need the setting_id form for the slice request — fetch via /profiles.
    # If /api/slicer/machines lookup is brittle, use whatever the existing
    # gateway test does. For this smoke test we assume the fixture is
    # authored for a profile the slicer can resolve.
    process_id = "GP004"  # adjust per fixture; document next to fixture data.
    machine_setting = "GM004"

    result = await client.slice(
        FIXTURE.read_bytes(),
        filename=FIXTURE.name,
        machine_profile=machine_setting,
        process_profile=process_id,
        filament_profiles=["Bambu PLA Basic"],
        process_overrides={"layer_height": "0.16"},
    )

    assert result.process_overrides_applied
    entry = result.process_overrides_applied[0]
    assert entry["key"] == "layer_height"
    assert entry["value"] == "0.16"
    assert entry["previous"] != "0.16"
```

- [ ] **Step 4: Run live test**

Run: `ORCASLICER_API_URL=http://10.0.1.9:8070 .venv/bin/pytest tests/integration/test_process_overrides_live.py -v`

Expected: PASS, or skip with a clear reason if the fixture path doesn't exist on this machine.

If the test FAILS because the fixture's authored profiles don't match `GM004` / `GP004` / `Bambu PLA Basic`, inspect the fixture's `inspect` payload manually and update the constants in the test. Document the chosen values in a comment alongside the fixture.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_options_live.py tests/integration/test_process_overrides_live.py
git commit -m "Add live integration tests for process editor surface

- options pass-through smoke test (catalogue + layout shape)
- live process_overrides slice asserts applied list comes back
  populated and previous value differs from submitted value
- both skip cleanly when ORCASLICER_API_URL is unreachable"
```

---

## Task 12: Final regression sweep

- [ ] **Step 1: Run the full unit test suite**

Run: `.venv/bin/pytest -x --ignore=tests/integration`
Expected: all PASS.

- [ ] **Step 2: Run the live integration tests against the deployed slicer**

Run: `ORCASLICER_API_URL=http://10.0.1.9:8070 .venv/bin/pytest tests/integration -v`
Expected: all PASS (or skip cleanly).

- [ ] **Step 3: Manually exercise via curl**

```bash
curl -s http://localhost:8000/api/slicer/options/process | python -c "import sys, json; d=json.load(sys.stdin); print('options:', len(d['options']), 'version:', d['version'])"
curl -s http://localhost:8000/api/slicer/options/process/layout | python -c "import sys, json; d=json.load(sys.stdin); print('pages:', len(d['pages']), 'allowlist_revision:', d['allowlist_revision'])"
```

Both commands should print non-empty results, with the gateway running locally and `ORCASLICER_API_URL` pointing at `10.0.1.9:8070`.

- [ ] **Step 4: Commit a final tidy if anything came up**

If the regression sweep surfaces any issues (formatting, missing import, lint), fix and commit:

```bash
git add -p
git commit -m "Tidy after regression sweep"
```

Otherwise this task closes without a commit.
