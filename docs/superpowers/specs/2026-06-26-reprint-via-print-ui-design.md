# Reprint a previous job via the Print UI

## Problem

On the `/jobs` page, each previous slice job has a print icon. Clicking it
immediately uploads the job's sliced 3MF to the printer (`printFromJob` →
`POST /api/print {job_id}`). The user gets no chance to review what's about to
print or to confirm the target printer.

We want the icon to instead take the user to the Print UI, pre-filled with that
job's exact configuration and showing the already-sliced result, so a single
**Confirm Print** reprints it just like last time — reusing the stored sliced
3MF with no re-slice. The user can also choose to edit settings first.

## Behavior

Clicking the print icon on a previous job navigates to `/print` and lands on the
existing **Preview Ready** review screen:

- The job's sliced thumbnail and print estimate are shown.
- The plate, slicing settings (machine/process/plate type/copies), process
  overrides, and filament→tray mapping are pre-filled from the job, with the
  controls in their read-only review presentation.
- **Confirm Print** reuses the stored sliced 3MF — no re-slice — via the
  existing `startPrintUploadFromJob(jobId)` path.
- **Re-slice** and **Download 3MF** behave as they do today.
- A new **Edit settings** button drops to the editable `imported` state so the
  user can change anything; from there Preview/Print re-slice as normal (new
  job).

This only changes the `/jobs` list print icon. The live print flow's upload
step (`startPrintUploadFromJob`, also used by the normal slice-then-print path)
is unchanged; the new review screen's Confirm Print routes through it.

## Why re-parse the input 3MF

The Preview Ready / Imported screen renders plates, filaments, and process
modifications from a `ThreeMFInfo` object (`state.info`). The job record does
not store `ThreeMFInfo` — only the input and output 3MF blobs plus the slice
config. So to render the review screen we re-parse the persisted input blob via
the existing `parse3mf` (a fast slicer *inspect*, not a slice). This keeps
`info` consistent with the file and avoids storing/maintaining a serialized
`ThreeMFInfo`.

Reusing the *sliced* 3MF (the actual reprint) does not depend on this:
**Confirm Print** uploads the stored `output.3mf` by `job_id`. The re-parse is
only for rendering the review UI.

## Backend

### New endpoint: `GET /api/slice-jobs/{job_id}/reprint-config`

Returns the data needed to rehydrate the Print UI for a reprint. Kept separate
from `SliceJobResponse` (shared by the list and single-job GET) so those stay
lean.

Response fields (from the persisted `SliceJob`):

- `machine_profile: str`
- `process_profile: str`
- `filament_profiles: list | dict` — position-keyed, as stored
- `plate_id: int`
- `plate_type: str`
- `copies: int`
- `process_overrides: dict[str, str] | None`
- `slot_indices: list[int] | None`
- `estimate: dict | None`
- `settings_transfer: dict | None`
- `has_output: bool` — whether the sliced `output.3mf` blob still exists on disk

404 if the job is unknown.

### Reused, unchanged

- `GET /api/slice-jobs/{job_id}/input` — re-download the input 3MF blob.
- `POST /api/print {job_id}` — the reprint fast-path (already exists).
- `GET /api/slice-jobs/{job_id}/thumbnail` — review thumbnail.

## Frontend

### 1. `/jobs` print icon (`slice-jobs-list.tsx`)

Replace the `printMut.mutate({ jobId, printerId })` call with
`navigate('/print?reprint=<jobId>')`. The `printFromJob` mutation in this
component is removed (no longer used here).

### 2. Rehydration in `print.tsx`

A `useEffect` watching the `reprint` query param, guarded so it runs once per
distinct job id (track the last-handled id in a ref). On a new `reprint=<jobId>`:

1. Show the existing `importing` spinner state.
2. `GET /api/slice-jobs/{jobId}/reprint-config`.
3. Download `GET /api/slice-jobs/{jobId}/input` → construct a `File` (named from
   the job's `filename`).
4. `parse3mf(file)` → `info` (render-only).
5. Restore context state from the config:
   - `setSettings({ machine, process, plateType, copies })`
   - `setSelectedPlateId(plate_id)`
   - `setProcessOverrides(process_overrides ?? {})`
   - Reconstruct `filamentMapping`: `filament_profiles` is position-keyed, so for
     each entry map `info.filaments[position].index → tray_slot`. Entries stored
     as a bare string (resolver fallback, no tray) contribute no mapping. The AMS
     auto-matcher is **not** run.
6. `setState({ kind: 'previewReady', file, info, jobId, estimate, transfer })`
   when `has_output` is true.
7. Clear the `reprint` query param (so back/refresh doesn't re-trigger).

The machine `setting_id` translation effects already in `print.tsx` (name →
`setting_id` for machine/process) still apply, so a restored profile name
resolves correctly.

### 3. `Edit settings` button (`previewReady` action row)

Add a button that calls `setState({ kind: 'imported', file, info })`,
re-enabling the controls. The pre-filled settings/mapping remain in context, so
the user edits from where the job left off; Preview/Print then re-slice as a new
job.

## Edge cases

- **`has_output` false** (sliced blob was cleared): land in `imported` instead
  of `previewReady`, with an info banner that the previous slice is no longer
  available and a re-slice is needed. The Confirm-Print path is not offered.
- **Input blob missing / `parse3mf` fails**: surface a toast error and return to
  `empty` (the importFile failure path already does this); do not navigate into
  a broken review screen.
- **Non-terminal jobs**: the print icon is only shown for terminal jobs with
  output (`canPrint`), so no extra handling is needed.

## Out of scope (YAGNI)

- Caching/serializing `ThreeMFInfo` on the job — re-parsing the input is fast
  and keeps info consistent.
- Any change to the normal import/slice/print flow beyond adding the
  `Edit settings` button and the rehydration entry point.
