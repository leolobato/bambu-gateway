# Reprint a previous job via the Print UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `/jobs` print icon open the Print UI on a review screen pre-filled with that job's configuration and sliced result, so one Confirm Print reprints it without re-slicing, with an option to edit first.

**Architecture:** A new read-only backend endpoint returns a stored job's reuse config. The `/jobs` print icon navigates to `/print?reprint=<jobId>` instead of printing directly. `print.tsx` detects the query param, re-parses the persisted input 3MF (for rendering only), restores settings/filaments/overrides, and lands on the existing `previewReady` state whose Confirm Print already reuses the stored sliced 3MF. A new Edit settings button drops to the editable `imported` state.

**Tech Stack:** FastAPI + Pydantic (backend), React + TypeScript + Vite + TanStack Query + React Router (frontend), pytest (backend tests), vitest + Testing Library (frontend tests).

## Global Constraints

- Backend tests run with `.venv/bin/pytest` (the project virtualenv has the deps the system Python lacks).
- Frontend commands run from `web/`. Typecheck/lint: `npm run lint` (`tsc --noEmit`). Tests: `npm run test` (`vitest run`). Build: `npm run build`.
- TS API types in `web/src/lib/api/types.ts` mirror `app/models.py` field names verbatim — no camelCase conversion; JSON is cast straight onto the types.
- Work on the `main` branch (no feature branch) per project preference. Commit after each task.
- `filament_profiles` is stored **position-keyed** (dense position in `info.filaments`), not slot-keyed. `FilamentMapping` is keyed by the 3MF's authored slot `index`. The two diverge for sparse 3MFs.

---

### Task 1: Backend `GET /api/slice-jobs/{job_id}/reprint-config` endpoint

**Files:**
- Modify: `app/models.py` (add `SliceJobReprintConfig` after `SliceJobResponse`, ~line 568)
- Modify: `app/main.py` (import the model ~line 79; add endpoint after `get_slice_job`, ~line 2735)
- Test: `tests/test_slice_jobs_api.py` (append)

**Interfaces:**
- Produces: `GET /api/slice-jobs/{job_id}/reprint-config` → JSON with fields `job_id, filename, machine_profile, process_profile, filament_profiles, plate_id, plate_type, copies, process_overrides, slot_indices, estimate, settings_transfer, has_output`. 404 when the job is unknown.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_slice_jobs_api.py`:

```python
async def test_reprint_config_returns_stored_config(app_client):
    import app.main as main_mod
    from app.slice_jobs import SliceJob, SliceJobStatus

    out = main_mod.slice_jobs._store.output_path("reprintjob")
    Path(out).write_bytes(b"sliced")
    seed = SliceJob.new(
        filename="cube.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={"0": {"profile_setting_id": "GFL99", "tray_slot": 2}},
        plate_id=3, plate_type="textured_pei_plate",
        project_filament_count=1, printer_id="PRINTER1", auto_print=False,
        input_path=main_mod.slice_jobs._store.input_path("reprintjob"),
        copies=2, process_overrides={"sparse_infill_density": "15%"},
        slot_indices=[1],
    )
    seed.id = "reprintjob"
    seed.status = SliceJobStatus.READY
    seed.output_path = str(out)
    await main_mod.slice_jobs._store.upsert(seed)

    resp = await app_client.get(f"/api/slice-jobs/{seed.id}/reprint-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == "cube.3mf"
    assert body["machine_profile"] == "GM014"
    assert body["process_profile"] == "0.20mm"
    assert body["plate_id"] == 3
    assert body["plate_type"] == "textured_pei_plate"
    assert body["copies"] == 2
    assert body["process_overrides"] == {"sparse_infill_density": "15%"}
    assert body["slot_indices"] == [1]
    assert body["filament_profiles"] == {"0": {"profile_setting_id": "GFL99", "tray_slot": 2}}
    assert body["has_output"] is True


async def test_reprint_config_has_output_false_when_blob_missing(app_client):
    import app.main as main_mod
    from app.slice_jobs import SliceJob, SliceJobStatus

    seed = SliceJob.new(
        filename="x.3mf", machine_profile="GM014", process_profile="0.20mm",
        filament_profiles={}, plate_id=1, plate_type="",
        project_filament_count=0, printer_id=None, auto_print=False,
        input_path=main_mod.slice_jobs._store.input_path("nooutputjob"),
    )
    seed.id = "nooutputjob"
    seed.status = SliceJobStatus.READY
    seed.output_path = None
    await main_mod.slice_jobs._store.upsert(seed)

    resp = await app_client.get(f"/api/slice-jobs/{seed.id}/reprint-config")
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_output"] is False


async def test_reprint_config_404_unknown_job(app_client):
    resp = await app_client.get("/api/slice-jobs/deadbeef/reprint-config")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_slice_jobs_api.py -k reprint_config -v`
Expected: FAIL — 404 (route not registered) / model import error.

- [ ] **Step 3: Add the response model**

In `app/models.py`, immediately after the `SliceJobResponse` class (before `SliceJobListResponse`, ~line 568):

```python
class SliceJobReprintConfig(BaseModel):
    """Stored config for rehydrating the Print UI to reprint a past job.

    Kept separate from `SliceJobResponse` (which the jobs list serializes per
    row) so the list stays lean; this is fetched only when the user opens one
    job for reprint.
    """

    job_id: str
    filename: str
    machine_profile: str
    process_profile: str
    # Position-keyed (dense position in the project's filament list), exactly
    # as stored — not slot-keyed. The UI maps it back to slot indices.
    filament_profiles: list | dict
    plate_id: int
    plate_type: str
    copies: int
    process_overrides: dict[str, str] | None = None
    slot_indices: list[int] | None = None
    estimate: dict | None = None
    settings_transfer: dict | None = None
    # True when the sliced output blob still exists on disk and can be
    # reprinted as-is (no re-slice). False once it has been cleared.
    has_output: bool
```

- [ ] **Step 4: Register the endpoint**

In `app/main.py`, add `SliceJobReprintConfig` to the model import block (the one containing `SliceJobResponse`, ~line 79):

```python
    SliceJobReprintConfig,
```

Then add the route immediately after the `get_slice_job` function (after ~line 2735):

```python
@app.get("/api/slice-jobs/{job_id}/reprint-config", response_model=SliceJobReprintConfig)
async def get_slice_job_reprint_config(job_id: str):
    """Return a past job's stored config so the Print UI can be rehydrated for
    a reprint (reusing the already-sliced output)."""
    if slice_jobs is None:
        raise HTTPException(status_code=404, detail="Slice jobs disabled")
    job = await slice_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    has_output = bool(job.output_path and Path(job.output_path).exists())
    return SliceJobReprintConfig(
        job_id=job.id,
        filename=job.filename,
        machine_profile=job.machine_profile,
        process_profile=job.process_profile,
        filament_profiles=job.filament_profiles,
        plate_id=job.plate_id,
        plate_type=job.plate_type,
        copies=job.copies,
        process_overrides=job.process_overrides,
        slot_indices=job.slot_indices,
        estimate=job.estimate,
        settings_transfer=job.settings_transfer,
        has_output=has_output,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_slice_jobs_api.py -k reprint_config -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/main.py tests/test_slice_jobs_api.py
git commit -m "feat: add slice-job reprint-config endpoint"
```

---

### Task 2: Frontend API type + client for reprint-config

**Files:**
- Modify: `web/src/lib/api/types.ts` (add `SliceJobReprintConfig` interface)
- Modify: `web/src/lib/api/slice-jobs.ts` (add `fetchReprintConfig`)

**Interfaces:**
- Consumes: backend `GET /api/slice-jobs/{job_id}/reprint-config` (Task 1).
- Produces: `fetchReprintConfig(jobId: string): Promise<SliceJobReprintConfig>` and the `SliceJobReprintConfig` type. `filament_profiles` typed as `Record<string, FilamentProfileEntry> | FilamentProfileEntry[]` where `FilamentProfileEntry = { profile_setting_id: string; tray_slot: number } | string`.

- [ ] **Step 1: Add the type**

In `web/src/lib/api/types.ts`, after the `ThreeMFInfo` interface (~line 205) add:

```typescript
export type FilamentProfileEntry =
  | { profile_setting_id: string; tray_slot: number }
  | string;

export interface SliceJobReprintConfig {
  job_id: string;
  filename: string;
  machine_profile: string;
  process_profile: string;
  // Position-keyed (object) or positional (array), as stored server-side.
  filament_profiles: Record<string, FilamentProfileEntry> | FilamentProfileEntry[];
  plate_id: number;
  plate_type: string;
  copies: number;
  process_overrides: Record<string, string> | null;
  slot_indices: number[] | null;
  estimate: PrintEstimate | null;
  settings_transfer: SettingsTransferInfo | null;
  has_output: boolean;
}
```

`PrintEstimate` and `SettingsTransferInfo` are already declared in this file; no new imports needed.

- [ ] **Step 2: Add the client function**

In `web/src/lib/api/slice-jobs.ts`, extend the type import on line 2 and add the function after `fetchSliceJob` (~line 11):

```typescript
import type { SliceJob, SliceJobListResponse, SliceJobReprintConfig, SliceJobStatus } from './types';
```

```typescript
export async function fetchReprintConfig(jobId: string): Promise<SliceJobReprintConfig> {
  return fetchJson<SliceJobReprintConfig>(
    `/api/slice-jobs/${encodeURIComponent(jobId)}/reprint-config`,
  );
}
```

- [ ] **Step 3: Typecheck**

Run from `web/`: `npm run lint`
Expected: PASS (no type errors).

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/api/types.ts web/src/lib/api/slice-jobs.ts
git commit -m "feat: add reprint-config API client"
```

---

### Task 3: Pure `buildReprintMapping` helper (position → slot index)

**Files:**
- Create: `web/src/lib/print/reprint-mapping.ts`
- Test: `web/src/lib/print/reprint-mapping.test.ts`

**Interfaces:**
- Consumes: `FilamentMapping` (`web/src/components/print/filaments-group.ts[x]`, `= Record<number, number>`), `FilamentProfileEntry` and `ThreeMFInfo` (Task 2 / types.ts). `ThreeMFInfo.filaments[i]` has `{ index: number; used: boolean; ... }`.
- Produces: `buildReprintMapping(filamentProfiles, info): FilamentMapping`.

- [ ] **Step 1: Write the failing test**

Create `web/src/lib/print/reprint-mapping.test.ts`:

```typescript
import { describe, expect, test } from 'vitest';
import { buildReprintMapping } from './reprint-mapping';
import type { ThreeMFInfo } from '@/lib/api/types';

function infoWith(indices: number[]): ThreeMFInfo {
  return {
    filaments: indices.map((index) => ({ index, used: true })),
  } as unknown as ThreeMFInfo;
}

describe('buildReprintMapping', () => {
  test('maps dense positions to the 3MF authored slot indices', () => {
    const info = infoWith([1, 3]); // sparse authored slots
    const mapping = buildReprintMapping(
      {
        '0': { profile_setting_id: 'GFA00', tray_slot: 0 },
        '1': { profile_setting_id: 'GFB01', tray_slot: 2 },
      },
      info,
    );
    expect(mapping).toEqual({ 1: 0, 3: 2 });
  });

  test('skips bare-string resolver-fallback entries (no tray)', () => {
    const info = infoWith([0, 1]);
    const mapping = buildReprintMapping(
      { '0': { profile_setting_id: 'GFA00', tray_slot: 1 }, '1': 'GFB01' },
      info,
    );
    expect(mapping).toEqual({ 0: 1 });
  });

  test('accepts the positional array form', () => {
    const info = infoWith([2]);
    const mapping = buildReprintMapping([{ profile_setting_id: 'GFA00', tray_slot: 3 }], info);
    expect(mapping).toEqual({ 2: 3 });
  });

  test('ignores positions with no matching filament', () => {
    const info = infoWith([0]);
    const mapping = buildReprintMapping(
      { '0': { profile_setting_id: 'GFA00', tray_slot: 1 }, '5': { profile_setting_id: 'GFB01', tray_slot: 2 } },
      info,
    );
    expect(mapping).toEqual({ 0: 1 });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `web/`: `npm run test -- reprint-mapping`
Expected: FAIL — module `./reprint-mapping` not found.

- [ ] **Step 3: Implement the helper**

Create `web/src/lib/print/reprint-mapping.ts`:

```typescript
import type { FilamentMapping } from '@/components/print/filaments-group';
import type { FilamentProfileEntry, ThreeMFInfo } from '@/lib/api/types';

/**
 * Rebuild the Print UI's filament→tray mapping from a stored job's
 * `filament_profiles`.
 *
 * `filament_profiles` is keyed by dense *position* in `info.filaments`, while
 * `FilamentMapping` is keyed by the 3MF's authored slot `index` (the two
 * diverge for sparse 3MFs). Bare-string entries are resolver fallbacks with no
 * tray, so they contribute no mapping.
 */
export function buildReprintMapping(
  filamentProfiles: Record<string, FilamentProfileEntry> | FilamentProfileEntry[],
  info: ThreeMFInfo,
): FilamentMapping {
  const mapping: FilamentMapping = {};
  const entries: [string, FilamentProfileEntry][] = Array.isArray(filamentProfiles)
    ? filamentProfiles.map((v, i) => [String(i), v])
    : Object.entries(filamentProfiles);
  for (const [posKey, entry] of entries) {
    if (typeof entry === 'string') continue;
    const position = Number.parseInt(posKey, 10);
    const filament = info.filaments[position];
    if (!filament) continue;
    mapping[filament.index] = entry.tray_slot;
  }
  return mapping;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run from `web/`: `npm run test -- reprint-mapping`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/print/reprint-mapping.ts web/src/lib/print/reprint-mapping.test.ts
git commit -m "feat: add buildReprintMapping helper"
```

---

### Task 4: Rehydrate `print.tsx` from a `?reprint=` param + Edit settings button

**Files:**
- Modify: `web/src/routes/print.tsx`
- Test: `web/src/routes/print.test.tsx` (append a describe block)

**Interfaces:**
- Consumes: `fetchReprintConfig` (Task 2), `buildReprintMapping` (Task 3), `sliceJobInputUrl` (existing in `slice-jobs.ts`), `parse3mf` (existing), `usePrintContext` setters incl. `setProcessOverride`.
- Produces: a `reprint` query-param entry point that lands on `previewReady` (or `imported` when `has_output` is false), and an `onEdit` action on the `previewReady` button row.

- [ ] **Step 1: Write the failing test**

Append to `web/src/routes/print.test.tsx`. First extend the mocks/imports near the top of the file (after the existing `vi.mock('@/lib/api/stl-drafts', ...)` block, ~line 61):

```typescript
vi.mock('@/lib/api/3mf', () => ({
  parse3mf: vi.fn(),
}));

vi.mock('@/lib/api/slice-jobs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/slice-jobs')>();
  return { ...actual, fetchReprintConfig: vi.fn() };
});
```

Add to the existing import group (~line 63):

```typescript
import { parse3mf } from '@/lib/api/3mf';
import { fetchReprintConfig } from '@/lib/api/slice-jobs';
```

Then add a `renderPrintRouteAt` helper and a describe block at the end of the file:

```typescript
function renderPrintRouteAt(initialEntry: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <PrinterProvider>
        <PrintProvider>
          <MemoryRouter initialEntries={[initialEntry]}>
            <PrintRoute />
          </MemoryRouter>
        </PrintProvider>
      </PrinterProvider>
    </QueryClientProvider>,
  );
}

describe('PrintRoute reprint rehydration', () => {
  beforeAll(() => {
    installLocalStorageStub();
  });

  beforeEach(() => {
    window.localStorage.setItem('bg.active-printer-id', 'printer-1');
    vi.mocked(listPrinters).mockResolvedValue({
      printers: [
        {
          id: 'printer-1', name: 'A1 Mini', machine_model: 'GM020', online: true,
          state: 'idle', stg_cur: 0, stage_name: null, stage_category: null,
          speed_level: 2, active_tray: null,
          temperatures: { nozzle_temp: 0, nozzle_target: 0, bed_temp: 0, bed_target: 0 },
          job: null, hms_codes: [], print_error: 0, error_message: null, camera: null,
        },
      ],
    });
    vi.mocked(getAms).mockResolvedValue({
      printer_id: 'printer-1', trays: [], units: [], vt_tray: null,
      auto_refill_enabled: null, auto_refill_supported: null,
    });
    vi.mocked(getSlicerMachines).mockResolvedValue([
      { setting_id: 'GM020', name: 'Bambu Lab A1 mini 0.4 nozzle', vendor: 'Bambu Lab', nozzle_diameter: '0.4', printer_model: 'A1 mini' },
    ]);
    vi.mocked(getSlicerProcesses).mockResolvedValue([
      { setting_id: 'GP000', name: '0.20mm Standard @BBL A1M', vendor: 'Bambu Lab', compatible_printers: ['GM020'], layer_height: '0.20' },
    ]);
    vi.mocked(getSlicerPlateTypes).mockResolvedValue([
      { value: 'textured_pei_plate', label: 'Textured PEI Plate' },
    ]);
    vi.mocked(parse3mf).mockResolvedValue({
      plates: [{ id: 1, used_filament_indices: [0] }],
      filaments: [{ index: 0, used: true, setting_id: 'GFA00' }],
      printer: { printer_settings_id: 'GM020' },
      print_profile: { print_settings_id: 'GP000' },
      bed_type: 'Textured PEI Plate',
      has_gcode: false,
      process_modifications: null,
    } as unknown as ThreeMFInfo);
    vi.mocked(fetchReprintConfig).mockResolvedValue({
      job_id: 'job123', filename: 'cube.3mf', machine_profile: 'GM020',
      process_profile: 'GP000', filament_profiles: { '0': { profile_setting_id: 'GFA00', tray_slot: 0 } },
      plate_id: 1, plate_type: 'textured_pei_plate', copies: 1,
      process_overrides: null, slot_indices: [0], estimate: null,
      settings_transfer: null, has_output: true,
    });
    // input-blob download
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new Blob([new Uint8Array([1])]), { status: 200 })));
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  test('lands on the preview screen with Confirm Print and Edit settings', async () => {
    renderPrintRouteAt('/print?reprint=job123');
    await waitFor(() => expect(fetchReprintConfig).toHaveBeenCalledWith('job123'));
    expect(await screen.findByText('Preview ready')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /confirm print/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /edit settings/i })).toBeInTheDocument();
  });

  test('Edit settings drops to the editable import state (Preview button shown)', async () => {
    renderPrintRouteAt('/print?reprint=job123');
    const editBtn = await screen.findByRole('button', { name: /edit settings/i });
    fireEvent.click(editBtn);
    expect(await screen.findByRole('button', { name: /preview/i })).toBeInTheDocument();
  });
});
```

Add `ThreeMFInfo` to the test file's type imports if not present:

```typescript
import type { ThreeMFInfo } from '@/lib/api/types';
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `web/`: `npm run test -- print.test`
Expected: FAIL — no "Preview ready" / no "Edit settings" button (reprint not wired yet).

- [ ] **Step 3: Wire the imports and router hook**

In `web/src/routes/print.tsx`:

Change the router import (line 2) to include `useSearchParams`:

```typescript
import { useNavigate, useSearchParams } from 'react-router-dom';
```

Extend the `slice-jobs` import block (~line 31-37) to add `fetchReprintConfig` and `sliceJobInputUrl`:

```typescript
import {
  cancelSliceJob,
  fetchReprintConfig,
  fetchSliceJob,
  submitSliceJob,
  sliceJobInputUrl,
  sliceJobOutputUrl,
  sliceJobThumbnailUrl,
} from '@/lib/api/slice-jobs';
```

Add the helper import (near the other `@/lib` imports, ~line 59):

```typescript
import { buildReprintMapping } from '@/lib/print/reprint-mapping';
```

Add `setProcessOverride` to the `usePrintContext()` destructure (~line 84-98):

```typescript
    setProcessOverride,
```

- [ ] **Step 4: Add the rehydrate function and effect**

In `print.tsx`, add the rehydrate callback right after `importFile` (after ~line 354) and the effect right after it. `importIdRef`, `appliedForMachineRef`, and all setters used are already in scope.

```typescript
  const rehydrateFromJob = useCallback(
    async (jobId: string) => {
      setProcessSheetOpen(false);
      const importId = `reprint-${jobId}`;
      importIdRef.current = importId;
      setState({ kind: 'importing', file: new File([], 'reprint.3mf'), importId });
      try {
        const config = await fetchReprintConfig(jobId);
        const res = await fetch(sliceJobInputUrl(jobId));
        if (!res.ok) throw new Error(`couldn't load the original file (${res.status})`);
        const blob = await res.blob();
        const file = new File([blob], config.filename || 'reprint.3mf', {
          type: 'application/octet-stream',
        });
        const info = await parse3mf(file);
        if (importIdRef.current !== importId) return;

        setSelectedPlateId(config.plate_id);
        setSettings((prev) => ({
          machine: config.machine_profile || prev.machine,
          process: config.process_profile || prev.process,
          plateType: config.plate_type || prev.plateType,
          copies: config.copies || 1,
        }));
        // Suppress the one-time resolve-for-machine auto-apply so entering the
        // editable state later doesn't clobber the restored process/plate-type.
        appliedForMachineRef.current = config.machine_profile;
        resetAllProcessOverrides();
        for (const [key, value] of Object.entries(config.process_overrides ?? {})) {
          setProcessOverride(key, value);
        }
        setFilamentMapping(buildReprintMapping(config.filament_profiles, info));

        if (config.has_output) {
          setState({
            kind: 'previewReady',
            file,
            info,
            jobId,
            transfer: config.settings_transfer ?? null,
            estimate: config.estimate ?? null,
          });
        } else {
          setState({
            kind: 'imported',
            file,
            info,
            banner: {
              variant: 'warn',
              title: 'Previous slice is no longer available.',
              message: 'Settings were restored — Preview or Print to slice again.',
            },
          });
        }
      } catch (err) {
        if (importIdRef.current !== importId) return;
        toast.error(`Couldn't open job for reprint: ${(err as Error).message}`);
        setState({ kind: 'empty' });
      }
    },
    [
      setProcessSheetOpen,
      setSelectedPlateId,
      setSettings,
      resetAllProcessOverrides,
      setProcessOverride,
      setFilamentMapping,
      setState,
    ],
  );

  const [searchParams, setSearchParams] = useSearchParams();
  const reprintJobId = searchParams.get('reprint');
  const reprintHandledRef = useRef<string | null>(null);
  useEffect(() => {
    if (!reprintJobId) return;
    if (reprintHandledRef.current === reprintJobId) return;
    reprintHandledRef.current = reprintJobId;
    void rehydrateFromJob(reprintJobId);
    // Strip the param so a refresh/back doesn't re-trigger the rehydrate.
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('reprint');
        return next;
      },
      { replace: true },
    );
  }, [reprintJobId, rehydrateFromJob, setSearchParams]);
```

- [ ] **Step 5: Add the Edit settings button**

In `print.tsx`, the `ActionButtons` component (~line 1168) — add an `onEdit` prop and render it in the `previewReady` row.

Update the props type (~line 1175) and signature to include `onEdit: () => void;`. Replace the `previewReady` return (the `grid-cols-3` block, ~line 1204-1228) with:

```tsx
  // previewReady — Edit settings | Re-slice | Download 3MF, then Confirm Print.
  return (
    <div className="flex flex-col gap-2.5">
      <div className="grid grid-cols-3 gap-2.5">
        <Button
          type="button"
          onClick={onEdit}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          Edit settings
        </Button>
        <Button
          type="button"
          onClick={onReslice}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          <RotateCcw className="w-4 h-4 mr-1.5" aria-hidden /> Re-slice
        </Button>
        <Button
          type="button"
          onClick={onDownload}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          Download 3MF
        </Button>
      </div>
      <Button
        type="button"
        onClick={onConfirmPrint}
        className="rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11 text-[14px] font-semibold"
      >
        Confirm Print
      </Button>
    </div>
  );
```

Then pass `onEdit` where `<ActionButtons .../>` is rendered (~line 1070):

```tsx
            onEdit={() =>
              state.kind === 'previewReady' &&
              setState({ kind: 'imported', file: state.file, info: state.info })
            }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run from `web/`: `npm run test -- print.test`
Expected: PASS (existing STL test + 2 new reprint tests).

- [ ] **Step 7: Typecheck**

Run from `web/`: `npm run lint`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add web/src/routes/print.tsx web/src/routes/print.test.tsx
git commit -m "feat: rehydrate Print UI from a job for reprint"
```

---

### Task 5: `/jobs` print icon navigates to the review screen

**Files:**
- Modify: `web/src/components/print/slice-jobs-list.tsx`

**Interfaces:**
- Consumes: the `?reprint=` entry point in `print.tsx` (Task 4).
- Produces: the `/jobs` print icon now navigates to `/print?reprint=<jobId>` instead of calling `printFromJob`.

- [ ] **Step 1: Swap the mutation for navigation**

In `web/src/components/print/slice-jobs-list.tsx`:

Remove the now-unused `printFromJob` import (line 44) and add the router hook import at the top:

```typescript
import { useNavigate } from 'react-router-dom';
```

Delete the `printMut` `useMutation` block (lines 137-145).

In the `SliceJobsList` component body, add (near the other hooks, e.g. after `const queryClient = useQueryClient();`):

```typescript
  const navigate = useNavigate();
```

Replace the `onPrint` prop passed to `<SliceJobRow>` (lines 298-303) with:

```tsx
                        onPrint={() => navigate(`/print?reprint=${encodeURIComponent(job.job_id)}`)}
```

Replace the `isPrinting` prop (line 306) with `isPrinting={false}` (the row no longer drives an async print; keep the prop so `SliceJobRow`'s signature is unchanged):

```tsx
                        isPrinting={false}
```

- [ ] **Step 2: Update the icon affordance copy**

In `SliceJobRow` (~line 443-444), the print icon currently says "Reprint"/"Print". Since it now opens the review screen rather than printing immediately, update the label/title to reflect that:

```tsx
              aria-label={`Open ${job.filename} to reprint`}
              title="Open to reprint"
```

- [ ] **Step 3: Typecheck**

Run from `web/`: `npm run lint`
Expected: PASS — no unused-import or type errors (confirms `printFromJob`/`printMut` fully removed).

- [ ] **Step 4: Build the SPA**

Run from `web/`: `npm run build`
Expected: PASS (tsc + vite build clean).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/print/slice-jobs-list.tsx
git commit -m "feat: jobs print icon opens the review screen"
```

---

### Task 6: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the backend suite**

Run: `.venv/bin/pytest tests/test_slice_jobs_api.py -v`
Expected: PASS (all, including the 3 new reprint-config tests).

- [ ] **Step 2: Run the frontend suite + build**

Run from `web/`: `npm run test && npm run build`
Expected: PASS.

- [ ] **Step 3: Manual smoke test (optional but recommended)**

Start the app (`python -m app` with a configured `ORCASLICER_API_URL`), open `/jobs`, click a finished job's print icon. Confirm: the app navigates to `/print`, shows "Preview ready" with the job's thumbnail/estimate and pre-filled settings, **Confirm Print** starts an upload without re-slicing, and **Edit settings** re-enables the controls.

- [ ] **Step 4: Final commit (if any verification fixes were needed)**

```bash
git add -A
git commit -m "test: verify reprint-via-print-ui flow"
```

---

## Self-Review

**Spec coverage:**
- Behavior — print icon → review screen pre-filled, reuse sliced 3MF on Confirm: Tasks 4 + 5 (lands on `previewReady`; Confirm Print uses existing `startPrintUploadFromJob`/`printFromJob {job_id}` fast-path, no re-slice). ✓
- Backend `reprint-config` endpoint with the listed fields incl. `has_output`: Task 1. ✓ (Added `filename` to the response — needed so the review screen's `PlateCard` shows the real name; noted in Task 1 model.)
- Reuse `GET /input`, `POST /api/print {job_id}`, `GET /thumbnail` unchanged: Tasks 4/existing. ✓
- Re-parse input for `ThreeMFInfo` (render-only): Task 4 (`parse3mf` on the downloaded blob). ✓
- Restore settings/plate/overrides/filament mapping without auto-matcher: Task 4 (explicit setters; `buildReprintMapping` instead of `getFilamentMatches`). ✓
- `Edit settings` button → editable `imported`: Task 4 Step 5. ✓
- Edge: `has_output` false → `imported` + banner, no Confirm path: Task 4 (the `else` branch lands in `imported`, where the action row is the `imported` Preview/Print buttons, not Confirm). ✓
- Edge: input missing / parse fail → toast + return to empty: Task 4 catch block. ✓
- YAGNI: no `ThreeMFInfo` caching; only the icon + rehydration + Edit button change. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step has full code. ✓

**Type consistency:** `SliceJobReprintConfig` fields match across `app/models.py` (Task 1), `types.ts` (Task 2), and the test mock (Task 4). `buildReprintMapping(filamentProfiles, info)` signature consistent between Task 3 definition and Task 4 call. `FilamentProfileEntry` defined once in `types.ts` (Task 2) and reused in Task 3. `fetchReprintConfig(jobId)` signature consistent across Tasks 2 and 4. ✓

**Note on the resolver-suppression line:** `appliedForMachineRef.current = config.machine_profile` in Task 4 prevents the `resolve-for-machine` effect from overwriting the restored process/plate-type when the user clicks Edit settings (the effect skips when `appliedForMachineRef.current === settings.machine`). The restored `machine_profile` is already a `setting_id`, so no name→id translation intervenes.
