# Per-slot filament overrides — gateway API + web UI

**Status:** approved (brainstorm) · **Date:** 2026-06-28

## Goal

Let the user override individual **filament-domain** settings (e.g. nozzle
temperature, max volumetric speed) **per filament slot** at slice time, without
authoring a new filament profile. This is the filament-domain analog of the
existing `process_overrides` feature, and must work for both the web UI and the
iOS app (so the surface is the gateway HTTP API, with the web UI built on top).

The slicer side (orcaslicer-headless) is **already implemented**. This spec
covers only the bambu-gateway plumbing + web UI.

## Slicer contract (already shipped — for reference)

- **Slice:** `POST /slice/v2` and `/slice-stream/v2` accept
  `filament_overrides: { "<slot>": { "<key>": "<string value>" } }`. Slot index
  space == `filament_settings_ids` positions. Values are OrcaSlicer
  config-strings (same convention as `process_overrides`). Only filament-domain
  keys are honored; unknown/non-filament keys are silently dropped. Per-slot:
  overriding slot 1 changes only slot 1. Client override beats the 3MF's
  authored per-filament value.
- **Applied report:** response `settings_transfer.filament_overrides_applied`
  is a list of `{slot, key, value, previous}` for what actually landed (dropped
  keys are absent). Empty / omitted overrides → `[]`, behavior unchanged.
  `/slice-stream/v2` carries it on the final `result` SSE event.
- **Prepare (bake):** `POST /3mf/{token}/prepare` accepts the same
  `filament_overrides` shape and persists it into the 3MF.
- **Catalogue:** `GET /options/filament` (per-option metadata, same shape as
  `/options/process`) and `GET /options/filament/layout` (page → optgroup →
  option, harvested from the Filament tab, served verbatim). Both refresh on
  `POST /profiles/reload`.

## Wire shape (gateway ↔ clients)

The gateway accepts `filament_overrides` as a JSON **form field** on the print
endpoints (mirroring `process_overrides`):

```json
{ "0": { "nozzle_temperature": "230" },
  "1": { "nozzle_temperature_initial_layer": "240",
         "filament_max_volumetric_speed": "12" } }
```

Validation (reject with 400): must be a JSON object; each outer key an int-like
string; each value an object of `{string: string}`. Empty object == no-op.
Out-of-range slot indices are left to the slicer to drop (it already does), so
the gateway does not need project-filament context to validate slots — matching
how `process_overrides` is forwarded verbatim.

## Gateway changes (Python)

1. **`app/slicer_client.py`**
   - Add `filament_overrides: dict[str, dict[str, str]] | None = None` to
     `slice()`, `slice_stream()`, `_slice_stream_real()`,
     `_slice_stream_fallback()`, `_build_v2_slice_body()`, and
     `prepare_3mf_token()`. In `_build_v2_slice_body`, include
     `body["filament_overrides"]` only when non-empty (None/{} are no-ops).
   - Add `filament_overrides_applied: list[dict]` to `SliceResult`; populate it
     in `_slice_result_from_v2` from `settings_transfer.filament_overrides_applied`.
     Carry it through the `slice_stream` fallback `result` event alongside
     `process_overrides_applied`.
   - Add `get_filament_options()` and `get_filament_layout()` (mirror
     `get_process_options` / `get_process_layout`, hitting `/options/filament`
     and `/options/filament/layout`).

2. **`app/main.py`**
   - New pass-through endpoints: `GET /api/slicer/options/filament` and
     `GET /api/slicer/options/filament/layout` (mirror the process ones).
   - New per-profile baseline proxy: `GET /api/slicer/filaments/{setting_id}`
     returning the slicer's resolved filament values (via
     `get_filament_detail`), mirroring `/api/slicer/processes/{setting_id}`.
     This is the `filamentBaseline` rung for the web effective-value resolver.
   - Print endpoints (`print_file` `/api/print`, and the stream variant): add
     `filament_overrides: str = Form("")`, parse via a new
     `_parse_filament_overrides_form` helper (analogous to
     `_parse_process_overrides_form`), and thread the dict into the slice call /
     slice-job submit.
   - Surface `filament_overrides_applied` in the print response next to
     `process_overrides_applied`.

3. **`app/slice_jobs.py`**
   - Add `filament_overrides: dict | None` to the `SliceJob` dataclass, `new()`,
     `submit()`, the `slice_stream()` call, and `to_dict`/`from_dict`
     persistence — exactly mirroring `process_overrides`.

## Web UI changes (React/TS)

1. **API hooks** — `web/src/lib/api/filament-options.ts`: `useFilamentOptions()`
   and `useFilamentLayout()` hitting the new endpoints. The raw option shape is
   identical to process, so reuse the existing `adaptOption` adapter (extract it
   to a shared module if cleaner). Add a `useFilamentBaseline(settingId)` query
   hitting `/api/slicer/filaments/{id}` for the per-slot resolved baseline.

2. **`web/src/lib/print-context.tsx`** — add
   `filamentOverrides: Record<number, Record<string, string>>` (slot → key →
   value) plus `setFilamentOverride(slot, key, value)`,
   `revertFilamentOverride(slot, key)`, `resetAllFilamentOverrides()`,
   mirroring the process-override callbacks.

3. **Components** (mirror the process ones, add a slot dimension):
   - `filament-parameters-card.tsx` — dedicated **"Filament settings"** card
     with a **slot selector** at top (each used filament slot, labeled by its
     selected filament name). Below it, the modified rows for the selected slot,
     grouped by layout page, reusing `ProcessOptionRow` and the `effectiveValue`
     resolver. Baseline = the selected slot's filament resolved values. A
     "Show all settings" button opens the sheet.
   - `filament-all-sheet.tsx` — full filament-options drill-down for the
     selected slot (mirror `process-all-sheet.tsx`).
   - Placed next to `ProcessParametersCard` on the print page.

4. **Slice request** — `web/src/lib/api/slice-jobs.ts`: add
   `filamentOverrides?: Record<string, Record<string, string>>` to the args and
   append a `filament_overrides` form field when non-empty. `print.tsx` passes
   `filamentOverrides` from context, and emits a dropped-override notice for any
   requested key absent from `filament_overrides_applied` (mirror
   `notifyDroppedOverrides`, per slot).

## Effective-value resolver (per slot)

Reuse the process chain (`catalogue default → baseline → file modifications →
user override`), but the **baseline** rung is per slot: the resolved values of
that slot's selected filament profile. The card resolves values for the
currently-selected slot only; switching slots re-points the baseline query.
File-modified filament keys (from the 3MF) are out of scope for v1 surfacing —
the card shows catalogue/baseline + user overrides; we can layer 3MF
modifications later if needed (process card already has the machinery to copy).

## Testing

- **Python (pytest):** `_parse_filament_overrides_form` validation (valid,
  non-object, non-string values, empty); `_build_v2_slice_body` includes
  `filament_overrides` only when non-empty; `SliceResult` surfaces
  `filament_overrides_applied`; print endpoint forwards the parsed dict and
  echoes the applied report; slice-job round-trips `filament_overrides` through
  persistence.
- **Web (vitest):** context setters; card renders modified rows for the
  selected slot and switches baseline on slot change; slice-jobs appends the
  form field only when non-empty; dropped-override notice fires per slot.

## Out of scope (v1)

- Surfacing the 3MF's own authored per-filament modifications in the card
  (catalogue/baseline + user overrides only).
- Saving overrides as a reusable named filament profile.
- Validating slot indices against project filament count in the gateway (slicer
  already drops out-of-range slots).
