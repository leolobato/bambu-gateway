# Process Parameter Editor — Gateway API Design

Server-side support in bambu-gateway for the orcaslicer-cli process-parameter
editor surface (slicer API rev 41, version `2.3.2-41`). The iOS app is the
first consumer; web UI follows later. **No gateway UI in this spec.**

Slicer-side reference: `../orcaslicer-cli/docs/process-parameter-editor-api.md`.

## Goal

Surface the slicer's three new pieces of editor data through the gateway, and
plumb a per-slice `process_overrides` dict end-to-end (form field →
`SliceJob` → `SlicerClient` → `/slice/v2` body → response → SSE).

The gateway adds no domain logic. The slicer owns the option catalogue, the
allowlist, and override resolution; the gateway is a thin proxy plus
serialisation glue.

## Non-goals

- No gateway UI. The Modified-view / All-view editors live in iOS (and
  later web).
- No allowlist enforcement on the gateway. The slicer is permissive on
  submission; the allowlist is a UI concept.
- No server-side caching of `/options/process` (the ~150 KB catalogue).
  Clients cache by `version`.
- No persistence of overrides outside a single slice job. `SliceJob`
  carries them so an in-flight slice survives a gateway restart, but
  there is no override library or per-printer default store.
- No filament/machine editor surface — out of scope for v1.

## New endpoints (pass-through)

Two new routes on the gateway, mirroring the existing `/api/slicer/*` proxy
pattern:

| Gateway | Slicer | Notes |
|---|---|---|
| `GET /api/slicer/options/process` | `GET /options/process` | Forward JSON verbatim. ~150 KB. |
| `GET /api/slicer/options/process/layout` | `GET /options/process/layout` | Forward JSON verbatim. |

Behaviour:

- `slicer_client is None` (i.e. `ORCASLICER_API_URL` unset) → HTTP 400
  `"Slicer not configured: ORCASLICER_API_URL not set"` (matches the
  existing `_proxy_slicer_profiles` shape).
- Slicer 200 → forward body verbatim.
- Slicer 5xx (including `503 options_not_loaded` /
  `503 options_layout_not_loaded`) → forward the slicer's status code and
  body. Clients decide whether to retry; the gateway does not retry.
- Slicer unreachable / network error → HTTP 502 with the error message.

Implementation:

- Two new `SlicerClient` methods, `get_process_options()` and
  `get_process_layout()`, returning `dict`. They use the existing
  `httpx.AsyncClient(transport=self._transport)` pattern, with a 30s
  timeout matching `get_profiles`.
- Two new FastAPI routes alongside `/api/slicer/machines` etc. in
  `app/main.py`.

## Extended `ThreeMFInfo`

The slicer's `/3mf/{token}/inspect` response gains a `process_modifications`
top-level field at schema_version 4. The gateway's `parse_3mf_via_slicer`
adapts that response into `ThreeMFInfo`; we extend the model:

```python
class ProcessModifications(BaseModel):
    process_setting_id: str = ""
    modified_keys: list[str] = []
    values: dict[str, str] = {}

class ThreeMFInfo(BaseModel):
    # ...existing fields...
    process_modifications: ProcessModifications = ProcessModifications()
```

`parse_3mf_via_slicer` reads the new field and copies it through. Two edge
cases:

- Field present but empty (`{"process_setting_id": "", "modified_keys": [],
  "values": {}}`) → defaults already match; no special-casing.
- Field missing entirely (older slicer build) → default to the empty model.
  The gateway must not crash if pointed at a pre-rev-41 slicer; the editor
  views simply have nothing to render.

This automatically flows through `/api/parse-3mf` and any other caller of
`parse_3mf_via_slicer`. No new endpoint is needed for this data.

## `process_overrides` on slice paths

### Form field

Three existing endpoints gain one new optional form field
`process_overrides: str = ""` (JSON-encoded `dict[str, str]`):

- `POST /api/print`
- `POST /api/print-stream`
- `POST /api/print-preview`

Validation, performed in the gateway before calling the slicer:

- Empty string or absent → no-op; do not include in the slice body.
- Non-JSON string → HTTP 400 `"Invalid process_overrides JSON"`.
- JSON that is not a dict → HTTP 400 `"process_overrides must be a JSON object"`.
- Dict containing non-string values → HTTP 400 `"process_overrides values must be strings"`.

The gateway does **not** filter filament-domain keys, unknown keys, or
unparseable values. The slicer is permissive and silently drops them; the
gateway forwards verbatim and reports back what was actually applied via
`process_overrides_applied`.

### `SliceJob` plumbing

`SliceJob` (in `app/slice_jobs.py`) gains:

```python
process_overrides: dict[str, str] | None = None
```

Threaded through `SliceJob.new(...)`, `SliceJobStore.create(...)`, and
`SliceJobManager.create_job(...)`. Persisted in `slice_jobs.json` as part
of the dataclass `asdict` round-trip — `dict[str, str] | None` is
JSON-friendly, so `to_dict` / `from_dict` need no special handling. Older
jobs without the field deserialise to `None` because the dataclass field
has a default.

At slice-run time, the manager passes `job.process_overrides` to
`slicer.slice_stream(...)` alongside the existing `filament_profiles`
arguments.

### `SlicerClient` plumbing

`SlicerClient.slice` and `SlicerClient.slice_stream` gain:

```python
process_overrides: dict[str, str] | None = None
```

`_build_v2_slice_body` includes `"process_overrides": process_overrides`
in the body when the dict is non-empty. `None` and `{}` are skipped to
match the doc's "absent / null / {} is a no-op" rule.

### Response plumbing

The slicer's `settings_transfer` block gains
`process_overrides_applied: list[{key, value, previous}]`.

Gateway changes:

- `SliceResult` gains `process_overrides_applied: list[dict] = field(default_factory=list)`.
- `_slice_result_from_v2` reads the new key:
  ```python
  applied = transfer.get("process_overrides_applied") or []
  ```
  storing the list of raw dicts on `SliceResult` (entries are already
  `{key, value, previous}` shaped per the slicer doc).
- `SettingsTransferInfo` gains
  `process_overrides_applied: list[ProcessOverrideApplied] = []`,
  where `ProcessOverrideApplied` is a new Pydantic model:
  ```python
  class ProcessOverrideApplied(BaseModel):
      key: str
      value: str
      previous: str
  ```
- The route handlers (`/api/print`, `/api/print-preview`) build the
  Pydantic models from the raw dicts when constructing
  `SettingsTransferInfo` from `SliceResult`:
  ```python
  process_overrides_applied=[
      ProcessOverrideApplied(**entry)
      for entry in result.process_overrides_applied
  ],
  ```
  Entries with unexpected shape (e.g. a future slicer adding extra
  fields) are silently passed through Pydantic's normal extra-field
  handling — we don't validate beyond the three documented keys.
- `/api/print-stream`'s SSE `result` event already forwards the
  slicer's `settings_transfer` block as-is (via `_slice_stream_real` →
  `_inflate_v2_result`), so `process_overrides_applied` arrives in the
  event payload automatically once the slicer emits it.
- The fallback path `_slice_stream_fallback` builds a synthetic
  `result` event from `SliceResult`. It must include
  `process_overrides_applied` from the result so the SSE schema is the
  same on real-stream and fallback paths.

## Error handling summary

| Failure | Gateway response |
|---|---|
| `ORCASLICER_API_URL` unset | 400 `"Slicer not configured..."` |
| Slicer 4xx (e.g. bad token) | Forward status + body |
| Slicer 5xx (incl. `503 options_not_loaded`) | Forward status + body verbatim |
| Slicer unreachable / network error | 502 with the underlying error |
| `process_overrides` not valid JSON | 400 `"Invalid process_overrides JSON"` |
| `process_overrides` not a JSON object | 400 `"process_overrides must be a JSON object"` |
| `process_overrides` value not a string | 400 `"process_overrides values must be strings"` |
| Pre-rev-41 slicer (no `process_modifications` in inspect) | `ThreeMFInfo.process_modifications` defaults to empty; no error |
| Pre-rev-41 slicer hit on `/api/slicer/options/process[/layout]` | Slicer returns 404; gateway forwards 404. Clients can detect and hide the editor UI. |

## Testing

Unit tests (mocked `httpx`):

- `SlicerClient.get_process_options()` and `get_process_layout()` —
  request URL, method, response shape pass-through, 503 forwarding,
  network-error → `SlicingError`.
- `parse_3mf_via_slicer` — populates `ProcessModifications` from the
  inspect payload; defaults to empty when the field is missing or
  `null`.
- `SlicerClient.slice` — `process_overrides={"layer_height": "0.16"}`
  is in the v2 body; `None` / `{}` produce no key. Response parsing
  populates `SliceResult.process_overrides_applied`.
- `SlicerClient.slice_stream` real path — `process_overrides` reaches
  the body; SSE `result` event surfaces `process_overrides_applied`
  from the slicer's payload.
- `SlicerClient._slice_stream_fallback` — synthetic `result` event
  carries `process_overrides_applied` from `SliceResult`.

Route tests (mocked `SlicerClient`):

- `/api/slicer/options/process` and `/api/slicer/options/process/layout`
  proxy correctly; return 400 when `slicer_client is None`.
- `/api/print` form-field round-trip:
  - Valid JSON dict → reaches `SlicerClient.slice` as a dict.
  - Empty string → omitted.
  - Invalid JSON → 400.
  - Non-object JSON → 400.
  - Non-string value → 400.
- Same form-field tests for `/api/print-stream` and `/api/print-preview`.

`SliceJob` persistence:

- `SliceJob.from_dict` round-trips a job with `process_overrides=None`
  and with a non-empty dict.
- A `slice_jobs.json` written by an older gateway (no
  `process_overrides` key) deserialises with `process_overrides=None`.

Live integration smoke test (against `http://10.0.1.9:8070`, rev 41):

- `GET /api/slicer/options/process` returns ≥ 600 entries with the
  documented per-option fields (`key`, `label`, `category`, `type`,
  `default`).
- `GET /api/slicer/options/process/layout` returns the documented
  page → optgroup → option structure with a stable `version` and
  `allowlist_revision`.
- Slicing a known fixture 3MF with
  `process_overrides={"layer_height": "0.16"}` produces a non-empty
  `process_overrides_applied` in the response, with `previous` matching
  the 3MF's authored value.

## Files touched

- `app/models.py` — add `ProcessModifications`, extend `ThreeMFInfo`,
  add `ProcessOverrideApplied`, extend `SettingsTransferInfo`.
- `app/parse_3mf.py` — copy `process_modifications` from inspect into
  `ThreeMFInfo`.
- `app/slicer_client.py` — `get_process_options`, `get_process_layout`,
  `process_overrides` kwarg on `slice` / `slice_stream`, `SliceResult`
  field, `_slice_result_from_v2` and `_slice_stream_fallback` updates.
- `app/slice_jobs.py` — `process_overrides` field on `SliceJob`,
  threaded through `new`, `SliceJobStore.create`,
  `SliceJobManager.create_job`, and the slice-run call site.
- `app/main.py` — new routes and form fields; pass-through to
  `SlicerClient`; route-level validation of the form field.
- `tests/` — unit + integration tests as listed above.

## Rollout

This is purely additive. No client breaks. Old iOS builds will keep
working (they just won't use the new fields). Pre-rev-41 slicers
continue to work for everything except the new editor data, which the
gateway returns as empty defaults rather than erroring.
