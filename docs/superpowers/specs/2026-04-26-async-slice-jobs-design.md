# Async Slice Jobs — Design

**Status:** approved (brainstorm), pending implementation plan
**Date:** 2026-04-26

## Goal

Let clients (web dashboard and iOS app) start slicing as a fire-and-forget
background job, then check progress and pick up the result later. Multiple
clients can slice in parallel up to a configurable limit.

Today, slicing is tied to a connected client: `/api/print-stream` (SSE) and
`/api/print-preview` (sync) both keep the requester on the line until the
slicer finishes. If the client disconnects, the work is wasted. The async
job system removes that coupling.

## Non-goals

- Replacing the printer-side print queue. Once a print is uploaded and
  started, live tracking stays the printer's domain.
- Distributed/horizontal scaling. The gateway is a single-host LAN service.
- Authenticated multi-tenancy. Job listing is global; the LAN is the
  trust boundary.

## Clients and notification channels

- **Web dashboard** — polls `GET /api/slice-jobs/{id}` for progress and
  terminal state.
- **iOS app** — polls when foregrounded; APNs push covers terminal states
  when backgrounded. APNs is optional (`push_enabled` gate, same as today's
  printer-status pushes).

## High-level architecture

One new module: `app/slice_jobs.py`. It owns:

- **`SliceJob`** — dataclass holding inputs, status, progress, result,
  error, and blob paths.
- **`SliceJobStore`** — persistence for `slice_jobs.json` and the
  `slice_jobs/` blob directory next to `printers.json`. Single
  `asyncio.Lock` guards writes.
- **`SliceJobManager`** — owns an `asyncio.Queue` and N worker tasks
  (`SLICE_MAX_CONCURRENT`, default `1`). Started/stopped from the FastAPI
  `lifespan`. Public surface: `submit()`, `get()`, `list()`, `cancel(id)`,
  `delete(id)`, `clear(statuses)`.

Workers call the existing `SlicerClient.slice_stream()` (already async),
update progress as SSE events arrive, write the output blob, then either
hand off to the existing print/upload pipeline (auto_print) or settle in
`ready`.

Why in-process asyncio (vs threads or external worker): the gateway only
orchestrates HTTP — slicing CPU lives on the OrcaSlicer host. So the
gateway-side work is purely I/O, which is exactly what asyncio is for. The
existing `SlicerClient` and `httpx.AsyncClient` plumbing reuses cleanly.
Threads would force a sync/async bridge; an external worker (Redis/ARQ)
would add infra for no benefit on a single host.

## Job model

```python
@dataclass
class SliceJob:
    id: str                              # 12-hex
    created_at: str                      # ISO 8601
    updated_at: str

    # inputs (immutable after submit)
    filename: str
    machine_profile: str
    process_profile: str
    filament_profiles: list | dict       # same shape /api/print accepts
    plate_id: int
    plate_type: str
    project_filament_count: int | None

    # target
    printer_id: str | None               # required if auto_print
    auto_print: bool

    # blobs
    input_path: Path                     # slice_jobs/<id>.input.3mf
    output_path: Path | None             # slice_jobs/<id>.output.3mf

    # progress
    status: str                          # see state machine
    progress: int                        # 0–100
    phase: str | None                    # "slicing" | "uploading" | "starting_print"

    # result
    estimate: PrintEstimate | None
    settings_transfer: dict | None
    output_size: int | None

    # failure
    error: str | None
```

## State machine

```
queued ─► slicing ─┬─► (auto_print && printer idle) ─► uploading ─► printing
                   │
                   ├─► (auto_print && printer busy)  ─► ready
                   ├─► (auto_print, validation warn) ─► ready
                   └─► (!auto_print)                 ─► ready

any non-terminal ─► failed
{queued, slicing, ready, uploading} ─► cancelled
```

- Terminal states: `ready`, `printing`, `failed`, `cancelled`.
- `printing` is terminal from the slice job's perspective. Once the print
  start command is acknowledged, the job is done; live print state is the
  printer's domain.
- Auto-print is best-effort: if the target printer is busy when slicing
  finishes (or filament/tray validation produces a warning), the job
  lands in `ready` and (if APNs enabled) fires `slice_ready`. The user
  starts the print manually.

## API surface

### New endpoints

```
POST   /api/slice-jobs                  # create job
GET    /api/slice-jobs                  # list all
GET    /api/slice-jobs/{id}             # full record
POST   /api/slice-jobs/{id}/cancel      # cancel without deleting
DELETE /api/slice-jobs/{id}             # cancel (if active) + delete record + blobs
POST   /api/slice-jobs/clear            # bulk delete by status
```

**`POST /api/slice-jobs`** (multipart form, mirrors `/api/print`):

| Field | Required | Notes |
|---|---|---|
| `file` | yes | 3MF |
| `machine_profile` | yes | |
| `process_profile` | yes | |
| `filament_profiles` | yes | JSON, same shape as `/api/print` |
| `plate_id` | no | default 0 |
| `plate_type` | no | |
| `project_filament_count` | no | derived if absent |
| `printer_id` | iff `auto_print` | |
| `auto_print` | no | bool, default `false` |

Returns `{ "job_id": "...", "status": "queued" }` immediately. Validation
errors (missing fields, unparseable 3MF, missing printer when auto_print)
return 4xx and create no job.

**`POST /api/slice-jobs/clear`** body: `{ "statuses": ["ready", "failed",
"cancelled", "printing"] }`. Defaults to all terminal states if omitted.

### Modifications to existing endpoints

- **`/api/print`** — accept `job_id` as the new parameter. `preview_id` is
  accepted as an alias for one transition release (iOS compat) and then
  removed. A `ready` job is functionally a preview; the existing fast
  path becomes "load job, validate `status==ready`, run upload+print,
  transition job to `printing`."

- **`/api/print-stream`** — internally creates a slice job (auto_print
  taken from existing `slice_only` / `preview` flags) and SSE-tails the
  job. Same wire format as today, no client changes required.

- **`/api/print-preview`** — becomes a thin wrapper that submits a job
  with `auto_print=false`, awaits terminal state, returns sliced bytes.
  Kept for backward compat; removed once iOS no longer calls it.

## Persistence and restart

Files alongside `printers.json`:

```
slice_jobs.json            # array of all job records (metadata only)
slice_jobs/
  <id>.input.3mf           # original upload, kept until slicing succeeds
  <id>.output.3mf          # sliced result, kept until job is deleted
```

- `SliceJobStore` rewrites `slice_jobs.json` atomically (write-temp +
  rename) on every status/progress change.
- Progress updates are throttled to ~1/sec to avoid disk hammering during
  fast slices.
- Blobs are written once and never rewritten.

Startup recovery (in `lifespan`):

1. Load `slice_jobs.json`.
2. Jobs in `slicing` / `uploading` / `starting_print` → flip to
   `failed("interrupted by gateway restart")`. Input blob kept; stale
   output blob (if any) discarded.
3. Jobs in `queued` → re-enqueue in the worker pool.
4. `ready` / `printing` / `failed` / `cancelled` → loaded as-is.

**Retention is manual.** No TTL. The `clear` endpoint and `DELETE
/api/slice-jobs/{id}` are the only cleanup paths. Output 3MFs (a few MB
each) will accumulate; surfacing total job-disk usage in the dashboard
is a follow-up.

## Concurrency

- `SLICE_MAX_CONCURRENT` env var, default `1`.
- `SliceJobManager.start()` spawns N `asyncio.Task`s, each looping
  `job = await queue.get(); await self._run(job)`.
- `submit()` writes the job to the store as `queued`, then
  `queue.put_nowait(job)`.

## Cancellation

Each job carries an in-memory `asyncio.Event` (`cancel_event`), held only
in the manager — not persisted.

- **Queued:** `cancel()` sets the event. The worker, on `queue.get()`,
  checks the event first and skips to next.
- **Slicing in-flight:** the worker awaits `slice_stream()` inside
  `asyncio.wait([..., cancel_event.wait()], FIRST_COMPLETED)`. On cancel,
  the `httpx` stream context exits (connection drops), the result is
  discarded, status → `cancelled`. The slicer host keeps burning CPU
  until the slice finishes naturally — see "Coordinated dependency"
  below.
- **Uploading:** reuse existing `UploadState.cancel()` /
  `UploadCancelledError` from `app/upload_tracker.py`. The FTP loop
  already polls the cancel flag.

## APNs

Two new event types in `app/notification_hub.py`: `slice_ready` and
`slice_failed`. `SliceJobManager` accepts an optional `on_terminal_state`
callback wired in `lifespan`, mirroring the existing
`status_change_callback` plumbing.

Push payload:

```json
{
  "kind": "slice_job",
  "job_id": "...",
  "status": "ready|failed",
  "filename": "...",
  "error": "..."
}
```

(`error` only on failure.) Only fires when `push_enabled` and a registered
device exists in the existing `DeviceStore`. No new device registration.

## Error handling

| Where | Behavior |
|---|---|
| Bad submit (missing fields, invalid 3MF, missing printer when auto_print) | 4xx at submit, no job created |
| Slicer unreachable / non-200 | status=`failed`, error stored, input blob kept |
| Filament/tray validation fails before upload (auto_print path) | status=`ready` (degrades to manual), warning stored in `error`, output blob kept, APNs `slice_ready` fires |
| Printer busy at upload time (auto_print) | status=`ready`, no error, APNs `slice_ready` fires |
| FTP upload fails | status=`failed`, output blob kept so user can retry via `POST /api/print { job_id }` |
| Print start command rejected | status=`failed`, output blob kept |
| Cancelled mid-slice | status=`cancelled`, no output blob written |
| Cancelled mid-upload | status=`cancelled`, partial upload abandoned |

## Coordinated dependencies

- **`orcaslicer-cli`** (sibling repo, `../orcaslicer-cli`) — add a cancel
  endpoint so in-flight slicer cancellation actually frees the slicer
  host's CPU. Until that lands, gateway-side cancel is best-effort: the
  job flips to `cancelled` immediately but the slicer keeps working until
  the slice naturally finishes. Tracked as a separate change.

- **`bambu-gateway-ios`** (sibling repo, `../bambu-gateway-ios`) — needs
  to be reviewed and updated to: (a) call the new `/api/slice-jobs`
  endpoints for fire-and-forget flows; (b) handle the new APNs payload
  shape (`kind: "slice_job"`); (c) move from `preview_id` to `job_id`
  before the alias is removed. The web dashboard uses `/api/print-stream`,
  which keeps working unchanged.

## Migration / rollout order

1. Land `app/slice_jobs.py` (model, store, manager) with no endpoint
   exposure. Unit-testable in isolation.
2. Add new `/api/slice-jobs` endpoints. Web dashboard and iOS keep using
   existing endpoints; nothing breaks.
3. Rewrite `/api/print-stream` and `/api/print-preview` as thin wrappers.
   Verify the existing web flow still works end-to-end.
4. Add `job_id` parameter to `/api/print`; keep `preview_id` as alias.
5. Update iOS to use `/api/slice-jobs` directly. Remove `preview_id`
   alias once iOS is on `job_id`.
6. (Coordinated) `orcaslicer-cli` cancel endpoint.

## Open items deferred to implementation plan

- Exact JSON schema for `SliceJob.to_dict()` returned by the API.
- Throttling implementation detail for the 1/sec progress write.
- Whether `SLICE_MAX_CONCURRENT` lives in `Settings` (pydantic) or as a
  loose `os.getenv` — match whatever the rest of the codebase does for
  similar tuning knobs.
