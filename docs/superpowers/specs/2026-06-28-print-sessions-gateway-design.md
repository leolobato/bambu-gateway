# Print sessions — agent-driven configure & handoff (gateway)

**Status:** approved (brainstorm) · **Date:** 2026-06-28 · **Sub-project A of 2** (the MCP server is sub-project B, separate spec)

## Goal

Let an agent pre-configure a print from a model and hand the user a URL to
continue tweaking or send to print — and, with explicit per-call approval,
let the agent start the print itself. This sub-project is the **gateway**
capability the agent (via the MCP server in sub-project B) drives over HTTP.
It must be fully usable on its own (any HTTP client can call it).

Most of the machinery already exists; this spec mostly composes it behind a
clean agent-facing surface and closes a few gaps.

## What already exists (reused, not rebuilt)

- `POST /api/slice-jobs` creates a job from a model (file or `input_token`) +
  full config (machine, process, filament, plate, `process_overrides`,
  `filament_overrides`, copies) and slices it in the background via
  `SliceJobManager.submit(...)`.
- `/print?reprint=<job_id>` deep-links into the print page and rehydrates from
  a job's `reprint-config`: with a sliced output → **previewReady** (review &
  Print); without → **imported** (settings applied, ready to tweak/Preview).
- STL flow: `POST /api/stl-drafts` (`import_stl_draft(machine, process)`) →
  `POST /api/stl-drafts/{token}/3mf` (`materialize`) → `input_token`.
- `resolve-for-machine` defaults process + filament for a machine; AMS tray
  state is available via `get_ams_info` and validated by
  `validate_selected_trays`.
- Per-slot AMS filament selection already flows through the slice job as
  `filament_profiles` with `tray_slot`, driving `build_ams_mapping`.
- Printing a finished job reuses the existing print-from-job path.

## New surface

### `POST /api/print-sessions` — create a configured print session

Multipart request (mirrors `/api/slice-jobs`):

| field | required | meaning |
|---|---|---|
| `file` | yes | model, `.3mf` or `.stl` (detected by extension) |
| `printer_id` | yes | target printer |
| `slice` | no (default `false`) | pre-slice for a ready-to-print handoff |
| `machine_profile` | no | override the printer's machine |
| `process_profile` | no | override the resolved process |
| `plate_type` | no | override the plate (see precedence) |
| `plate_id` | no (default 0) | plate to print |
| `copies` | no (default 1) | |
| `filament_profiles` | no | JSON per-slot selection incl. AMS `tray_slot` |
| `process_overrides` | no | JSON |
| `filament_overrides` | no | JSON (per-slot, the just-shipped feature) |

**Never prints.** Printing is exclusively the separate `/print` action below.

Response: `PrintSessionResponse`:

```json
{ "job_id": "ab12cd34ef56", "sliced": false,
  "handoff_url": "https://gateway.example/print?reprint=ab12cd34ef56" }
```

### `GET /api/print-sessions/{id}` — session status

Thin read returning the job's status, `sliced` (has_output), printer, and
`handoff_url`. Lets the MCP poll a `slice:true` session until it's sliced
before offering to print.

### `POST /api/print-sessions/{id}/print` — start the print (gated)

Starts the session's job on its printer, reusing the existing print-from-job
machinery. **Separate, authenticated, logged action**, never a side effect of
configuration. Requires the session to have a sliced output (409 if not
sliced). Can be globally disabled by config (`ALLOW_AGENT_PRINT`, default on
for self-hosted; when off → 403). The human-approval guarantee is enforced by
the MCP server's elicitation (sub-project B) — see "Trust model".

## Behavior — `POST /api/print-sessions`

1. **Resolve config server-side** ("printer only, gateway defaults the rest"):
   - `printer_id` → the printer's `machine_model` (a machine `setting_id`);
     `machine_profile` overrides it. Error 400 if the printer is unknown or has
     no `machine_model` and none was supplied.
   - `resolve-for-machine(machine)` defaults `process_profile` and per-slot
     filament; AMS-aware from loaded trays. Any agent-supplied field/slot wins.
   - **Plate type precedence** (first match wins): request `plate_type` →
     **printer's `default_plate_type`** → 3MF's authored plate → machine
     default. (See "Per-printer default plate".)
   - Validate referenced AMS trays are loaded (`validate_selected_trays`) → 400.
2. **Model → `input_token`.** 3MF: upload. STL: resolve defaults first (STL
   import needs machine+process) → `import_stl_draft` → `materialize` →
   `input_token`. STL import failure → 422.
3. **Create the job; slice conditionally:**
   - `slice:true` → `SliceJobManager.submit(...)` (background slice). Handoff
     lands the user in **previewReady**.
   - `slice:false` → **persist a configured-but-unsliced job** (config + input
     blob, no enqueue). Handoff lands the user in **imported**. This is the one
     net-new job-store capability (see below).
4. **Build the handoff URL** = `{base}/print?reprint=<job_id>` where `base` is
   `PUBLIC_BASE_URL` if set, else derived from the request. Return
   `PrintSessionResponse`.

Errors: unknown printer / unloaded tray → 400; STL import failure → 422;
slicer unreachable → 502; `/print` when not sliced → 409; `/print` when
disabled → 403.

## Per-printer default plate

The plate kept on a printer is a physical fact about the printer, so it lives
in the printer config, not the request.

- Add `default_plate_type: str = ""` to `PrinterConfig` and the printer-CRUD
  request/response models (parallel to the existing `machine_model`).
- Expose it in the Settings → printer CRUD UI (a plate-type select, sourced
  from the slicer's plate-types catalogue; empty = "use file's plate").
- **Applied everywhere the printer is targeted**, not only the agent path:
  - `POST /api/print-sessions` applies the precedence above.
  - The web import/print flow (`print.tsx`) defaults the plate to the active
    printer's `default_plate_type` (a higher-precedence default than the 3MF's
    authored plate) until the user changes it in the UI.

## Configured-but-unsliced job (net-new)

The slice-job store can already hold a job with `has_output=false`; the new
piece is a path to **persist a job without enqueuing a slice**. Add a
`SliceJobManager.create_configured(...)` (or a `enqueue=False` option on
`submit`) that writes the input blob + job record (config incl.
`filament_overrides`) and upserts it, without putting it on the slice queue.
Its `reprint-config` then drives the **imported** rehydration. `slice:true`
keeps using `submit(...)` unchanged.

## Reprint-config carries filament overrides (gap closure)

`SliceJobReprintConfig` and the print-page rehydration predate
`filament_overrides` and don't restore them. Since the agent can now set them:

- Add `filament_overrides: dict[str, dict[str, str]] | None = None` to
  `SliceJobReprintConfig` (populated from the stored job).
- In `print.tsx` `rehydrateFromJob`, restore them via the context's
  `setFilamentOverride(slot, key, value)` (mirroring the existing
  `process_overrides` restore loop), after `resetAllFilamentOverrides()`.

## Config

- `PUBLIC_BASE_URL` (new) — absolute base for handoff URLs; falls back to the
  request's base URL when unset.
- `ALLOW_AGENT_PRINT` (new, default `true`) — global kill-switch for the
  `/print` action.

## Trust model (for the `/print` action)

Per-call human approval is enforced by the MCP server's **elicitation**
(sub-project B): the agent's `start_print` prompts the user in the agent
conversation and only calls `/print` on an explicit yes. The gateway's
guarantees are narrower and concrete: printing is a separate authenticated call
that only the trusted MCP client (holding a gateway token) can make, it is
distinct from configuration, it is logged, and it can be globally disabled. A
future hardening (gateway-issued one-time code verified server-side) can be
added without reshaping these endpoints.

## Testing

- **pytest:** plate-type precedence (request > printer default > authored >
  machine default); printer→machine resolution + process/filament defaulting;
  AMS tray validation (loaded vs not); STL → `input_token` path; 3MF path;
  `slice:false` persists a configured-but-unsliced job that yields an
  **imported** reprint-config; `slice:true` enqueues; handoff URL building with
  and without `PUBLIC_BASE_URL`; `/print` 409-when-unsliced and
  403-when-disabled; `reprint-config` round-trips `filament_overrides`.
- **vitest:** the web import flow defaults the plate to the active printer's
  `default_plate_type`; `rehydrateFromJob` restores `filament_overrides`; the
  Settings UI edits `default_plate_type`.

## Out of scope (this sub-project)

- The MCP server itself (sub-project B).
- Gateway-side cryptographic verification of the human approval (the one-time
  code variant) — deferred; the elicitation model is the agreed v1.
- Auto-printing as a side effect of session creation (explicitly excluded).
