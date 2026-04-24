# Web UI Redesign — Phase 4: Print Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder at `/beta/print` with the full Print flow from the spec — full-page drag-and-drop, parsed-3MF detail (file / slicing settings / filaments / info banner), SSE-driven slicing with cancel, preview-then-confirm, and FTP upload progress with cancel — all consuming the existing backend endpoints.

**Architecture:** A single `<PrintRoute/>` owns a discriminated-union state machine with six observable states (empty → imported → slicing → previewReady → uploading → sent). State transitions are pure reducers; side effects (parsing, slicing SSE, upload polling) are isolated in dedicated hooks (`useDropZone`, `usePrintStream`). The SSE consumer uses `fetch` + `ReadableStream` (NOT `EventSource`, which doesn't support POST with multipart/form-data). UI primitives mirror Phase 2's `<TrayRow/>` shape — a `<SettingRow/>` for the slicing-settings group and a custom `<FilamentMappingRow/>` for the filaments group, both rendered inside shadcn `<Card/>`s. Drag-and-drop attaches to `document` and is gated by a depth counter so nested drag-enter/leave events don't flicker.

**Tech Stack:** All Phase 1–3 deps stay. New shadcn primitive this phase installs: `alert` (banner variants info/warn/success/error). No backend changes — every endpoint Phase 4 consumes is already shipped (`/api/parse-3mf`, `/api/slicer/{machines,processes,filaments,plate-types}`, `/api/filament-matches`, `/api/print-stream`, `/api/print`, `/api/uploads/{id}`, `/api/uploads/{id}/cancel`).

**Spec reference:** `docs/superpowers/specs/2026-04-23-webui-redesign-design.md` — sections "Print flow" (states A–F), "Drag-and-drop", and the "Incremental ship plan" step 4.

---

## File structure

**Created in this plan:**

```
web/
└── src/
    ├── lib/
    │   ├── api/
    │   │   ├── 3mf.ts                  # POST /api/parse-3mf
    │   │   ├── slicer-profiles.ts      # GET /api/slicer/{machines,processes,filaments,plate-types}
    │   │   ├── filament-matches.ts     # POST /api/filament-matches
    │   │   ├── uploads.ts              # GET /api/uploads/{id}, POST /api/uploads/{id}/cancel
    │   │   └── print.ts                # POST /api/print (preview_id only — slicing flow uses use-print-stream.ts)
    │   ├── use-drop-zone.ts            # full-page dragenter/leave/drop with depth counter; .3mf only
    │   └── use-print-stream.ts         # POST /api/print-stream as a ReadableStream → typed event callbacks
    └── components/
        ├── ui/
        │   └── alert.tsx               # shadcn primitive
        └── print/
            ├── drop-zone.tsx           # State A card + full-page tinted overlay
            ├── plate-card.tsx          # thumbnail + filename + meta + Clear
            ├── setting-row.tsx         # one row for the slicing-settings grouped list
            ├── slicing-settings-group.tsx  # Card containing 3× SettingRow (machine/process/plate)
            ├── filament-mapping-row.tsx    # one row for the filaments group
            ├── filaments-group.tsx     # Card containing N× FilamentMappingRow
            ├── info-banner.tsx         # Alert wrapper with info/warn/success/error variants + Details expander
            ├── slicing-progress-card.tsx   # sticky State C / E card (spinner, progress, status line, Cancel)
            └── settings-transfer-note.tsx  # collapsible note showing settings_transfer details from SSE
```

**Modified in this plan:**

- `web/src/lib/api/types.ts` — add `ThreeMFInfo`, `PlateInfo`, `PlateObject`, `FilamentInfo`, `PrinterInfo`, `PrintProfileInfo`, `SlicerMachine`, `SlicerProcess`, `SlicerPlateType`, `FilamentMatchRequest`, `FilamentMatchResponse`, `ProjectFilamentMatch`, `FilamentMatchReason`, `UploadState`, `SettingsTransferInfo`, `TransferredSetting`, `FilamentTransferEntry` interfaces.
- `web/src/routes/print.tsx` — replace the 9-line placeholder with the full state machine + composition.
- `web/components.json`, `web/package.json`, `web/package-lock.json` — only via `npx shadcn@latest add alert`.

**Untouched in this plan:**

- All `app/*` Python files. Backend has no gaps for this phase.
- `web/src/components/{state-badge,printer-picker,stat-chip,tray-row,app-shell}.tsx` and the entire `web/src/components/dashboard/` directory.
- `web/src/lib/{api/{client,printers,ams,printer-commands},filament-color,format,printer-context,use-media-query,utils}.ts`.
- `web/src/components/ui/*` already installed.
- Old Jinja UI at `/` and `/settings`.
- Phase 6 cutover removes the legacy templates.

## Prerequisites

- Phase 3 must be on `main` (commit `568a4ae` or later — the README Phase 3 note).
- Local FastAPI at `http://localhost:4844` with `ORCASLICER_API_URL` configured (the slicer is required for any 3MF without baked gcode — most 3MFs).
- A real printer configured so the print flow can be smoke-tested end-to-end. Without a printer the empty/imported/slicing states render but State E (Upload) and State F (Sent) can't be exercised.
- A test 3MF on disk to drag onto the page (a small calibration cube works).
- `cd web && npm install` already done.

---

## Task 1: Mirror new backend types in `types.ts`

**Files:**
- Modify: `web/src/lib/api/types.ts`

This adds TypeScript mirrors for the Pydantic models in `app/models.py` that Phase 4 consumes. Keep field names identical — same convention as Phase 2 Task 1.

- [ ] **Step 1.1: Append new interfaces to `web/src/lib/api/types.ts`**

Open the file and append (at the end, after the existing Phase 2 types):

```typescript
// --- 3MF parse models (mirror app/models.py 3MF parse section) ---

export interface PlateObject {
  id: string;
  name: string;
}

export interface PlateInfo {
  id: number;
  name: string;
  objects: PlateObject[];
  /** Base64-encoded PNG; empty string when the 3MF has no thumbnail. */
  thumbnail: string;
}

export interface FilamentInfo {
  index: number;
  type: string;
  /** "RRGGBB" hex (no #), or "" for unset. */
  color: string;
  setting_id: string;
}

export interface PrinterInfo {
  printer_settings_id: string;
  printer_model: string;
  nozzle_diameter: string;
}

export interface PrintProfileInfo {
  print_settings_id: string;
  layer_height: string;
}

export interface ThreeMFInfo {
  plates: PlateInfo[];
  filaments: FilamentInfo[];
  print_profile: PrintProfileInfo;
  printer: PrinterInfo;
  /** When true, the file already contains G-code — slicing & filament overrides are ignored. */
  has_gcode: boolean;
}

// --- Slicer profile shapes (returned by GET /api/slicer/*) ---

export interface SlicerMachine {
  setting_id: string;
  name: string;
  vendor: string;
  nozzle_diameter: string;
  printer_model: string;
}

export interface SlicerProcess {
  setting_id: string;
  name: string;
  vendor: string;
  /** Machine setting_ids this process is compatible with. */
  compatible_printers: string[];
  layer_height: string;
}

export interface SlicerPlateType {
  value: string;
  label: string;
}

// --- Filament matching ---

export type FilamentMatchReason = 'exact_filament_id' | 'type_fallback' | 'none';

export interface ProjectFilamentMatch {
  index: number;
  setting_id: string;
  type: string;
  color: string;
  resolved_profile: SlicerFilament | null;
  preferred_tray_slot: number | null;
  match_reason: FilamentMatchReason;
}

export interface FilamentMatchRequest {
  printer_id: string;
  filaments: FilamentInfo[];
}

export interface FilamentMatchResponse {
  printer_id: string;
  matches: ProjectFilamentMatch[];
}

// --- Upload tracker ---

export type UploadStatus = 'pending' | 'uploading' | 'completed' | 'failed' | 'cancelled';

export interface UploadState {
  upload_id: string;
  filename: string;
  printer_id: string;
  total_bytes: number;
  bytes_sent: number;
  /** 0–100 integer. */
  progress: number;
  status: UploadStatus;
  error: string | null;
}

// --- Settings transfer (returned in print SSE result) ---

export interface TransferredSetting {
  key: string;
  value: string;
  original: string | null;
}

export interface FilamentTransferEntry {
  slot: number;
  original_filament: string;
  selected_filament: string;
  /** "applied" | "filament_changed" | "no_customizations" */
  status: string;
  transferred: TransferredSetting[];
  discarded: string[];
}

export interface SettingsTransferInfo {
  status: string;
  transferred: TransferredSetting[];
  filaments: FilamentTransferEntry[];
}
```

- [ ] **Step 1.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 1.3: Commit**

```bash
git add web/src/lib/api/types.ts
git commit -m "Add Phase 4 types: 3MF, slicer profiles, filament match, uploads"
```

The subject is exactly 60 chars — at the limit. If your hook rejects it, use `Add Phase 4 API types (3MF, slicer, matches, uploads)` (53 chars).

---

## Task 2: Add typed API helpers for the print flow

**Files:**
- Create: `web/src/lib/api/3mf.ts`, `web/src/lib/api/slicer-profiles.ts`, `web/src/lib/api/filament-matches.ts`, `web/src/lib/api/uploads.ts`, `web/src/lib/api/print.ts`

- [ ] **Step 2.1: Create `web/src/lib/api/3mf.ts`**

```typescript
import { ApiError } from './client';
import type { ThreeMFInfo } from './types';

/**
 * POST /api/parse-3mf with a multipart `file` field.
 * `fetchJson` is not used here because the body is `FormData`, not JSON,
 * and we need the request to set its own Content-Type with the boundary.
 */
export async function parse3mf(file: File): Promise<ThreeMFInfo> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/api/parse-3mf', { method: 'POST', body: fd });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // body wasn't JSON
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as ThreeMFInfo;
}
```

- [ ] **Step 2.2: Create `web/src/lib/api/slicer-profiles.ts`**

```typescript
import { fetchJson } from './client';
import type { SlicerFilament, SlicerMachine, SlicerPlateType, SlicerProcess } from './types';

export async function getSlicerMachines(): Promise<SlicerMachine[]> {
  return fetchJson<SlicerMachine[]>('/api/slicer/machines');
}

export async function getSlicerProcesses(machine?: string): Promise<SlicerProcess[]> {
  const path = machine
    ? `/api/slicer/processes?machine=${encodeURIComponent(machine)}`
    : '/api/slicer/processes';
  return fetchJson<SlicerProcess[]>(path);
}

export interface GetSlicerFilamentsParams {
  machine?: string;
  amsAssignable?: boolean;
}

export async function getSlicerFilaments(
  params: GetSlicerFilamentsParams = {},
): Promise<SlicerFilament[]> {
  const qs = new URLSearchParams();
  if (params.machine) qs.set('machine', params.machine);
  if (params.amsAssignable !== undefined) qs.set('ams_assignable', String(params.amsAssignable));
  const suffix = qs.toString() ? `?${qs}` : '';
  return fetchJson<SlicerFilament[]>(`/api/slicer/filaments${suffix}`);
}

export async function getSlicerPlateTypes(): Promise<SlicerPlateType[]> {
  return fetchJson<SlicerPlateType[]>('/api/slicer/plate-types');
}
```

- [ ] **Step 2.3: Create `web/src/lib/api/filament-matches.ts`**

```typescript
import { fetchJson } from './client';
import type { FilamentInfo, FilamentMatchResponse } from './types';

/**
 * POST /api/filament-matches — ask the backend to map each project filament
 * (from the parsed 3MF) to a preferred AMS tray slot for the given printer.
 */
export async function getFilamentMatches(
  printerId: string,
  filaments: FilamentInfo[],
): Promise<FilamentMatchResponse> {
  return fetchJson<FilamentMatchResponse>('/api/filament-matches', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ printer_id: printerId, filaments }),
  });
}
```

- [ ] **Step 2.4: Create `web/src/lib/api/uploads.ts`**

```typescript
import { fetchJson } from './client';
import type { UploadState } from './types';

export async function getUploadState(uploadId: string): Promise<UploadState> {
  return fetchJson<UploadState>(`/api/uploads/${encodeURIComponent(uploadId)}`);
}

export async function cancelUpload(uploadId: string): Promise<void> {
  await fetchJson<unknown>(`/api/uploads/${encodeURIComponent(uploadId)}/cancel`, {
    method: 'POST',
  });
}
```

- [ ] **Step 2.5: Create `web/src/lib/api/print.ts`**

The streaming endpoint (`/api/print-stream`) is consumed via the SSE hook in Task 5 — this file only handles the simple `POST /api/print` "print from preview" form path.

```typescript
import { ApiError } from './client';

/**
 * POST /api/print using a stored preview id (skip re-slicing).
 * The endpoint accepts multipart form data; only `preview_id` and
 * (optionally) `printer_id` are needed for the preview path.
 */
export async function printFromPreview(
  previewId: string,
  printerId?: string,
): Promise<void> {
  const fd = new FormData();
  fd.append('preview_id', previewId);
  if (printerId) fd.append('printer_id', printerId);

  const res = await fetch('/api/print', { method: 'POST', body: fd });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // body wasn't JSON
    }
    throw new ApiError(res.status, detail);
  }
}
```

- [ ] **Step 2.6: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 2.7: Commit**

```bash
git add web/src/lib/api/3mf.ts web/src/lib/api/slicer-profiles.ts web/src/lib/api/filament-matches.ts web/src/lib/api/uploads.ts web/src/lib/api/print.ts
git commit -m "Add API helpers: parse-3mf, slicer profiles, matches, uploads, print"
```

Subject is exactly 65 chars — over the limit. Use `Add API helpers for parse, slicer, matches, uploads, print` (58 chars).

---

## Task 3: Install shadcn `alert` primitive

**Files:**
- Create (via shadcn CLI): `web/src/components/ui/alert.tsx`
- Modify: `web/package.json`, `web/package-lock.json`

- [ ] **Step 3.1: Add the primitive**

```bash
cd web && npx shadcn@latest add alert --yes --overwrite
```

Expected: `web/src/components/ui/alert.tsx` is written. No new peer deps — `Alert` is pure HTML + `class-variance-authority`, both already present.

- [ ] **Step 3.2: Verify lint + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0.

- [ ] **Step 3.3: Commit**

```bash
git add web/components.json web/package.json web/package-lock.json web/src/components/ui/alert.tsx
git commit -m "Install shadcn alert primitive"
```

---

## Task 4: Build `useDropZone` hook

**Files:**
- Create: `web/src/lib/use-drop-zone.ts`

The hook attaches `dragenter` / `dragleave` / `dragover` / `drop` listeners to `document`. A depth counter avoids the well-known flicker bug where dragging over a child element fires `dragleave` even though the user is still on the page. When the user drops a `.3mf` file, the callback fires; other types raise an error toast.

- [ ] **Step 4.1: Create `web/src/lib/use-drop-zone.ts`**

```typescript
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

export interface UseDropZoneOptions {
  /** File extension to accept, lowercase, including the dot. */
  accept: string;
  /** Called once per accepted drop. */
  onFile: (file: File) => void;
  /** When false, the hook is inert (no listeners attached). Default true. */
  enabled?: boolean;
}

/**
 * Subscribe to document-level drag-and-drop. Returns `dragging: true`
 * whenever the user is mid-drag over the page so a tinted overlay can
 * be shown. Multi-file drops take only the first matching file.
 */
export function useDropZone({ accept, onFile, enabled = true }: UseDropZoneOptions) {
  const [dragging, setDragging] = useState(false);
  // Use a ref for the depth counter so listener identity stays stable across re-renders.
  const depthRef = useRef(0);

  useEffect(() => {
    if (!enabled) return;

    function onDragEnter(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depthRef.current += 1;
      setDragging(true);
    }

    function onDragOver(e: DragEvent) {
      if (!hasFiles(e)) return;
      // Required to allow `drop` to fire.
      e.preventDefault();
    }

    function onDragLeave(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depthRef.current = Math.max(0, depthRef.current - 1);
      if (depthRef.current === 0) setDragging(false);
    }

    function onDrop(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depthRef.current = 0;
      setDragging(false);

      const files = Array.from(e.dataTransfer?.files ?? []);
      const match = files.find((f) => f.name.toLowerCase().endsWith(accept));
      if (!match) {
        toast.error(`Drop a ${accept} file.`);
        return;
      }
      onFile(match);
    }

    document.addEventListener('dragenter', onDragEnter);
    document.addEventListener('dragover', onDragOver);
    document.addEventListener('dragleave', onDragLeave);
    document.addEventListener('drop', onDrop);
    return () => {
      document.removeEventListener('dragenter', onDragEnter);
      document.removeEventListener('dragover', onDragOver);
      document.removeEventListener('dragleave', onDragLeave);
      document.removeEventListener('drop', onDrop);
      depthRef.current = 0;
      setDragging(false);
    };
  }, [accept, onFile, enabled]);

  return { dragging };
}

/** True when the drag carries at least one file (vs text, URL, etc.). */
function hasFiles(e: DragEvent): boolean {
  const types = e.dataTransfer?.types;
  if (!types) return false;
  for (let i = 0; i < types.length; i++) {
    if (types[i] === 'Files') return true;
  }
  return false;
}
```

- [ ] **Step 4.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 4.3: Commit**

```bash
git add web/src/lib/use-drop-zone.ts
git commit -m "Add useDropZone hook with depth-counter flicker fix"
```

---

## Task 5: Build `usePrintStream` hook

**Files:**
- Create: `web/src/lib/use-print-stream.ts`

This wraps `POST /api/print-stream` — a multipart upload that streams Server-Sent Events back. The browser's `EventSource` API can't be used here (it's GET-only), so the hook reads the response body as a `ReadableStream` and parses SSE manually. Cancellation uses an `AbortController`.

The backend emits these event types (from `app/main.py:1290`):
- `status` — `{phase: string, message: string, upload_id?: string}`
- `progress` — slicer-forwarded data (typically `{percent?: number, status_line?: string, ...}`)
- `result` — `{file_base64: string, settings_transfer?: SettingsTransferInfo, preview_id?: string, ...}`
- `upload_progress` — `{percent: number, bytes_sent: number, total_bytes: number}`
- `print_started` — `{printer_id: string, file_name: string, settings_transfer?: SettingsTransferInfo}`
- `error` — `{error: string}`
- `done` — `{}` (always last)

- [ ] **Step 5.1: Create `web/src/lib/use-print-stream.ts`**

```typescript
import { useCallback, useRef, useState } from 'react';
import type { SettingsTransferInfo } from './api/types';

export interface PrintStreamStatus {
  phase: string;
  message: string;
  upload_id?: string;
}

export interface PrintStreamProgress {
  percent?: number;
  status_line?: string;
  [key: string]: unknown;
}

export interface PrintStreamUploadProgress {
  percent: number;
  bytes_sent: number;
  total_bytes: number;
}

export interface PrintStreamResult {
  file_base64: string;
  settings_transfer?: SettingsTransferInfo;
  preview_id?: string;
  [key: string]: unknown;
}

export interface PrintStreamPrintStarted {
  printer_id: string;
  file_name: string;
  settings_transfer?: SettingsTransferInfo;
}

export interface PrintStreamHandlers {
  onStatus?: (s: PrintStreamStatus) => void;
  onProgress?: (p: PrintStreamProgress) => void;
  onResult?: (r: PrintStreamResult) => void;
  onUploadProgress?: (u: PrintStreamUploadProgress) => void;
  onPrintStarted?: (p: PrintStreamPrintStarted) => void;
  onError?: (e: { error: string }) => void;
  onDone?: () => void;
}

export interface StartPrintStreamArgs {
  file: File;
  printerId?: string;
  plateId: number;
  machineProfile: string;
  processProfile: string;
  /** JSON-encoded mapping of project-filament-index → {profile_setting_id, tray_slot}; pass empty object to skip. */
  filamentProfiles: Record<string, { profile_setting_id: string; tray_slot: number }>;
  plateType?: string;
  /** True = only slice + return preview_id (no upload). */
  preview?: boolean;
}

/**
 * One-shot SSE consumer for /api/print-stream. Returns `start(args, handlers)`
 * and a stable `cancel()` that aborts the in-flight stream.
 *
 * The hook does NOT manage caller state — handlers fire as events arrive,
 * the caller keeps its own state machine.
 */
export function usePrintStream() {
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const start = useCallback(
    async (args: StartPrintStreamArgs, handlers: PrintStreamHandlers) => {
      cancel(); // cancel any in-flight stream
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setStreaming(true);

      const fd = new FormData();
      fd.append('file', args.file);
      if (args.printerId) fd.append('printer_id', args.printerId);
      fd.append('plate_id', String(args.plateId));
      fd.append('machine_profile', args.machineProfile);
      fd.append('process_profile', args.processProfile);
      fd.append('filament_profiles', JSON.stringify(args.filamentProfiles));
      if (args.plateType) fd.append('plate_type', args.plateType);
      if (args.preview) fd.append('preview', 'true');

      try {
        const res = await fetch('/api/print-stream', {
          method: 'POST',
          body: fd,
          signal: ctrl.signal,
        });
        if (!res.ok) {
          let detail = res.statusText;
          try {
            const body = (await res.json()) as { detail?: string };
            if (body?.detail) detail = body.detail;
          } catch {
            // not JSON
          }
          handlers.onError?.({ error: detail });
          handlers.onDone?.();
          return;
        }
        if (!res.body) {
          handlers.onError?.({ error: 'No response body' });
          handlers.onDone?.();
          return;
        }
        await consumeSse(res.body, handlers);
      } catch (err) {
        if ((err as { name?: string }).name === 'AbortError') {
          // Caller-initiated abort — don't surface as error.
          return;
        }
        handlers.onError?.({ error: (err as Error).message });
        handlers.onDone?.();
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null;
        setStreaming(false);
      }
    },
    [cancel],
  );

  return { start, cancel, streaming };
}

/**
 * Parse SSE frames from a streaming response body and dispatch to handlers.
 * Each frame ends with a blank line (\n\n). Lines beginning with "event:"
 * set the type; lines beginning with "data:" accumulate; the blank line
 * triggers a dispatch.
 */
async function consumeSse(stream: ReadableStream<Uint8Array>, handlers: PrintStreamHandlers) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let eventType: string | null = null;
  let dataLines: string[] = [];

  function dispatch() {
    if (!eventType || dataLines.length === 0) {
      eventType = null;
      dataLines = [];
      return;
    }
    let payload: unknown = {};
    try {
      payload = JSON.parse(dataLines.join('\n'));
    } catch {
      payload = { raw: dataLines.join('\n') };
    }
    switch (eventType) {
      case 'status':           handlers.onStatus?.(payload as PrintStreamStatus); break;
      case 'progress':         handlers.onProgress?.(payload as PrintStreamProgress); break;
      case 'result':           handlers.onResult?.(payload as PrintStreamResult); break;
      case 'upload_progress':  handlers.onUploadProgress?.(payload as PrintStreamUploadProgress); break;
      case 'print_started':    handlers.onPrintStarted?.(payload as PrintStreamPrintStarted); break;
      case 'error':            handlers.onError?.(payload as { error: string }); break;
      case 'done':             handlers.onDone?.(); break;
      // unknown event types are ignored
    }
    eventType = null;
    dataLines = [];
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newlineIdx: number;
    while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, newlineIdx).replace(/\r$/, '');
      buffer = buffer.slice(newlineIdx + 1);
      if (line === '') {
        dispatch();
      } else if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        dataLines.push(line.slice(6));
      }
      // ignore comment lines (":") and other prefixes
    }
  }
  // Flush any final frame without trailing newline.
  if (buffer.length > 0) {
    const line = buffer.replace(/\r$/, '');
    if (line.startsWith('data: ')) dataLines.push(line.slice(6));
    else if (line.startsWith('event: ')) eventType = line.slice(7).trim();
  }
  dispatch();
}
```

- [ ] **Step 5.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 5.3: Commit**

```bash
git add web/src/lib/use-print-stream.ts
git commit -m "Add usePrintStream hook (fetch + ReadableStream SSE consumer)"
```

---

## Task 6: Build `<DropZone/>`

**Files:**
- Create: `web/src/components/print/drop-zone.tsx`

Two render surfaces in one component:

1. The empty-state card (dashed border, centered icon, headline, subtext, "Choose file…" button).
2. A full-page tinted overlay rendered via React portal when `dragging` is true (active in all states, not just empty — the user can drop a new file at any time to swap).

The `useDropZone` hook is invoked by `<PrintRoute/>` (Task 11) — `<DropZone/>` only renders the visual chrome; the parent owns the file-selection callback.

- [ ] **Step 6.1: Create `web/src/components/print/drop-zone.tsx`**

```typescript
import { useRef } from 'react';
import { createPortal } from 'react-dom';
import { Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export function DropZoneCard({
  onFile,
  targetPrinterName,
}: {
  onFile: (file: File) => void;
  targetPrinterName: string | null;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  return (
    <div className="flex flex-col gap-4">
      {targetPrinterName && (
        <p className="text-sm text-text-1">
          Target printer: <span className="text-text-0">{targetPrinterName}</span>
        </p>
      )}
      <div className="rounded-2xl border border-dashed border-text-2 bg-card flex flex-col items-center gap-3 py-12 px-6 text-center">
        <Upload className="w-7 h-7 text-text-1" aria-hidden />
        <div className="text-[18px] font-semibold text-white">Drop a .3mf file here</div>
        <div className="text-sm text-text-1">Or import from your device</div>
        <input
          ref={inputRef}
          type="file"
          accept=".3mf"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onFile(f);
            // Reset so the same file picked twice still fires onChange.
            e.target.value = '';
          }}
        />
        <Button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="mt-2 rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-10 px-5 text-[14px] font-semibold"
        >
          Choose file…
        </Button>
      </div>
    </div>
  );
}

export function DropOverlay({ visible }: { visible: boolean }) {
  if (typeof document === 'undefined') return null;
  return createPortal(
    <div
      aria-hidden
      className={cn(
        'fixed inset-0 z-50 flex items-center justify-center pointer-events-none transition-opacity duration-fast',
        visible ? 'opacity-100' : 'opacity-0',
      )}
    >
      <div className="absolute inset-0 bg-bg-0/80 backdrop-blur-sm" />
      <div className="relative rounded-2xl border-2 border-dashed border-accent bg-card px-8 py-10 flex flex-col items-center gap-3">
        <Upload className="w-8 h-8 text-accent" aria-hidden />
        <div className="text-[18px] font-semibold text-white">Drop to import</div>
        <div className="text-sm text-text-1">Releases a .3mf file</div>
      </div>
    </div>,
    document.body,
  );
}
```

- [ ] **Step 6.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 6.3: Commit**

```bash
git add web/src/components/print/drop-zone.tsx
git commit -m "Add DropZoneCard + DropOverlay for the Print empty state"
```

---

## Task 7: Build `<PlateCard/>`

**Files:**
- Create: `web/src/components/print/plate-card.tsx`

Per spec ("State B"): 140px plate thumbnail (or placeholder), filename in white semibold (word-break, no truncation), meta line `Plate X of Y · N filaments · L layers`, "Clear" action in `danger` 12px.

The `info.plates[]` array contains base64-encoded PNG thumbnails. Render via `data:image/png;base64,…` URL.

- [ ] **Step 7.1: Create `web/src/components/print/plate-card.tsx`**

```typescript
import { Card } from '@/components/ui/card';
import type { PlateInfo, ThreeMFInfo } from '@/lib/api/types';
import { cn } from '@/lib/utils';

export function PlateCard({
  filename,
  info,
  selectedPlateId,
  onClear,
}: {
  filename: string;
  info: ThreeMFInfo;
  /** Currently-selected plate id (1-based). */
  selectedPlateId: number;
  onClear: () => void;
}) {
  const plate = info.plates.find((p) => p.id === selectedPlateId) ?? info.plates[0];
  const layers = info.print_profile.layer_height
    ? estimateLayers(info, plate)
    : null;

  return (
    <Card className="p-4 bg-card border-border flex gap-4 items-start">
      <PlateThumb plate={plate} />
      <div className="flex flex-col gap-2 min-w-0 flex-1">
        <div className="text-[15px] font-semibold text-white break-words">{filename}</div>
        <div className="text-xs text-text-1">
          Plate {plate?.id ?? '—'} of {info.plates.length} · {info.filaments.length} filament{info.filaments.length === 1 ? '' : 's'}
          {layers != null && (
            <>
              {' · '}
              <span className="font-mono tabular-nums">{layers} layers</span>
            </>
          )}
        </div>
        <button
          type="button"
          onClick={onClear}
          className="self-start text-[12px] font-semibold text-danger hover:underline"
        >
          Clear
        </button>
      </div>
    </Card>
  );
}

function PlateThumb({ plate }: { plate: PlateInfo | undefined }) {
  const hasThumb = !!plate?.thumbnail;
  return (
    <div
      className={cn(
        'shrink-0 w-[140px] h-[140px] rounded-xl bg-bg-0 overflow-hidden flex items-center justify-center',
        !hasThumb && 'border border-dashed border-text-2',
      )}
    >
      {hasThumb ? (
        <img
          src={`data:image/png;base64,${plate!.thumbnail}`}
          alt={`Plate ${plate!.id} preview`}
          className="w-full h-full object-contain"
          draggable={false}
        />
      ) : (
        <span className="text-xs text-text-2">No preview</span>
      )}
    </div>
  );
}

/**
 * Layers can't be derived exactly without slicing; we don't display a value
 * unless the 3MF has both layer_height and the printer reports plate height.
 * Returning null hides the segment cleanly.
 */
function estimateLayers(_info: ThreeMFInfo, _plate: PlateInfo | undefined): number | null {
  // The backend doesn't expose total_layers in parse-3mf — we can revisit
  // when slicing returns the count via the SSE result.
  return null;
}
```

- [ ] **Step 7.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0. The two underscored params (`_info`, `_plate`) satisfy `noUnusedParameters`.

- [ ] **Step 7.3: Commit**

```bash
git add web/src/components/print/plate-card.tsx
git commit -m "Add PlateCard with thumbnail, filename, meta and Clear action"
```

---

## Task 8: Build `<SettingRow/>` and `<SlicingSettingsGroup/>`

**Files:**
- Create: `web/src/components/print/setting-row.tsx`, `web/src/components/print/slicing-settings-group.tsx`

Per spec ("State B / Slicing settings"): a Card containing 3 rows — Machine, Process, Plate type. Each row label on the left, right-aligned `<Select/>` trigger showing the current value with a `›` chevron.

When the 3MF's embedded `setting_id` doesn't match anything in the catalog for the current printer, the row's dropdown still renders the file's value as the first option with a `(from file — different printer)` suffix. This matches the old Jinja UI's behavior for cross-printer 3MFs.

- [ ] **Step 8.1: Create `web/src/components/print/setting-row.tsx`**

```typescript
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';

export interface SettingOption {
  value: string;
  label: string;
  /** Italicized / dimmed when the option came from the 3MF but isn't in the catalog. */
  fromFileMismatch?: boolean;
}

export function SettingRow({
  label,
  value,
  options,
  onChange,
  disabled = false,
}: {
  label: string;
  value: string;
  options: SettingOption[];
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <span className="text-sm text-text-0">{label}</span>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger
          aria-label={label}
          className={cn(
            'h-auto py-1 px-2 max-w-[60%] border-0 bg-transparent text-text-1 text-sm',
            'focus:ring-0 focus:ring-offset-0',
          )}
        >
          <span className="truncate">{labelFor(value, options)}</span>
        </SelectTrigger>
        <SelectContent>
          {options.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>
              <span className={cn(opt.fromFileMismatch && 'italic text-text-2')}>
                {opt.label}
                {opt.fromFileMismatch && ' (from file — different printer)'}
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function labelFor(value: string, options: SettingOption[]): string {
  const opt = options.find((o) => o.value === value);
  return opt?.label ?? '—';
}
```

- [ ] **Step 8.2: Create `web/src/components/print/slicing-settings-group.tsx`**

```typescript
import { Card } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { SettingRow, type SettingOption } from '@/components/print/setting-row';

export interface SlicingSettings {
  machine: string;
  process: string;
  plateType: string;
}

export function SlicingSettingsGroup({
  settings,
  onChange,
  machineOptions,
  processOptions,
  plateTypeOptions,
  disabled = false,
}: {
  settings: SlicingSettings;
  onChange: (next: SlicingSettings) => void;
  machineOptions: SettingOption[];
  processOptions: SettingOption[];
  plateTypeOptions: SettingOption[];
  disabled?: boolean;
}) {
  return (
    <section className="flex flex-col gap-1">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-text-2 px-1">
        Slicing settings
      </div>
      <Card className="px-4 bg-card border-border">
        <SettingRow
          label="Machine"
          value={settings.machine}
          options={machineOptions}
          onChange={(machine) => onChange({ ...settings, machine })}
          disabled={disabled}
        />
        <Separator className="bg-border" />
        <SettingRow
          label="Process"
          value={settings.process}
          options={processOptions}
          onChange={(process) => onChange({ ...settings, process })}
          disabled={disabled}
        />
        <Separator className="bg-border" />
        <SettingRow
          label="Plate type"
          value={settings.plateType}
          options={plateTypeOptions}
          onChange={(plateType) => onChange({ ...settings, plateType })}
          disabled={disabled}
        />
      </Card>
    </section>
  );
}
```

- [ ] **Step 8.3: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 8.4: Commit**

```bash
git add web/src/components/print/setting-row.tsx web/src/components/print/slicing-settings-group.tsx
git commit -m "Add SettingRow + SlicingSettingsGroup for the Print form"
```

---

## Task 9: Build `<FilamentMappingRow/>` and `<FilamentsGroup/>`

**Files:**
- Create: `web/src/components/print/filament-mapping-row.tsx`, `web/src/components/print/filaments-group.tsx`

Per spec ("State B / Filaments"): a Card containing one row per project filament. Row layout: 24px color dot, project filament name (or generic "Filament N"), `type` eyebrow on the right side, then the mapped tray as `→ <colored dot> Tray N ›` opening a `<Select/>` listing all AMS trays for the active printer.

Tray options are sourced from the AMS data the Dashboard already fetches. The Print route (Task 11) reads `/api/ams?printer_id=…` and passes the tray list down.

- [ ] **Step 9.1: Create `web/src/components/print/filament-mapping-row.tsx`**

```typescript
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select';
import type { AMSTray, FilamentInfo } from '@/lib/api/types';
import { normalizeTrayColor } from '@/lib/filament-color';
import { cn } from '@/lib/utils';

export function FilamentMappingRow({
  filament,
  trays,
  selectedTraySlot,
  onChange,
  disabled = false,
}: {
  filament: FilamentInfo;
  trays: AMSTray[];
  /** -1 = unmapped (slicer will keep the file's filament profile). */
  selectedTraySlot: number;
  onChange: (slot: number) => void;
  disabled?: boolean;
}) {
  // FilamentInfo.color is "RRGGBB" without alpha — wrap to "RRGGBBFF" so
  // normalizeTrayColor's validation accepts it.
  const projectColor = normalizeTrayColor(filament.color ? `${filament.color}FF` : '');
  const selectedTray = trays.find((t) => t.slot === selectedTraySlot);
  const selectedTrayColor = selectedTray ? normalizeTrayColor(selectedTray.tray_color) : null;
  const projectName = filament.setting_id || `Filament ${filament.index + 1}`;

  return (
    <div className="flex items-center justify-between gap-3 py-3">
      <div className="flex items-center gap-2.5 min-w-0">
        <span
          className={cn(
            'shrink-0 w-6 h-6 rounded-full',
            projectColor == null && 'border border-dashed border-text-2',
          )}
          style={projectColor ? { backgroundColor: projectColor } : undefined}
          aria-hidden
        />
        <div className="flex flex-col gap-0.5 min-w-0">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
            {filament.type || '—'}
          </span>
          <span className="text-sm text-text-0 truncate">{projectName}</span>
        </div>
      </div>
      <Select
        value={String(selectedTraySlot)}
        onValueChange={(v) => onChange(Number(v))}
        disabled={disabled}
      >
        <SelectTrigger
          aria-label={`Map ${projectName} to AMS tray`}
          className={cn(
            'h-auto py-1 px-2 max-w-[55%] border-0 bg-transparent text-text-1 text-sm',
            'focus:ring-0 focus:ring-offset-0',
          )}
        >
          <span className="flex items-center gap-1.5 min-w-0">
            <span aria-hidden>→</span>
            {selectedTrayColor && (
              <span
                className="shrink-0 w-3 h-3 rounded-full"
                style={{ backgroundColor: selectedTrayColor }}
                aria-hidden
              />
            )}
            <span className="truncate">
              {selectedTray ? `Tray ${selectedTray.slot + 1}` : 'Skip'}
            </span>
          </span>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="-1">Skip (use file's profile)</SelectItem>
          {trays.map((tray) => (
            <SelectItem key={`${tray.ams_id}-${tray.slot}`} value={String(tray.slot)}>
              Tray {tray.slot + 1}
              {tray.tray_type ? ` · ${tray.tray_type}` : ''}
              {tray.matched_filament?.name ? ` · ${tray.matched_filament.name}` : ''}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
```

- [ ] **Step 9.2: Create `web/src/components/print/filaments-group.tsx`**

```typescript
import { Card } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { FilamentMappingRow } from '@/components/print/filament-mapping-row';
import type { AMSTray, FilamentInfo } from '@/lib/api/types';

export type FilamentMapping = Record<number, number>;

export function FilamentsGroup({
  projectFilaments,
  trays,
  mapping,
  onChange,
  disabled = false,
}: {
  projectFilaments: FilamentInfo[];
  trays: AMSTray[];
  /** Map of project-filament-index → tray-slot (-1 = unmapped). */
  mapping: FilamentMapping;
  onChange: (next: FilamentMapping) => void;
  disabled?: boolean;
}) {
  if (projectFilaments.length === 0) return null;

  return (
    <section className="flex flex-col gap-1">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-text-2 px-1">
        Filaments
      </div>
      <Card className="px-4 bg-card border-border">
        {projectFilaments.map((filament, idx) => (
          <div key={filament.index}>
            <FilamentMappingRow
              filament={filament}
              trays={trays}
              selectedTraySlot={mapping[filament.index] ?? -1}
              onChange={(slot) => onChange({ ...mapping, [filament.index]: slot })}
              disabled={disabled}
            />
            {idx < projectFilaments.length - 1 && <Separator className="bg-border" />}
          </div>
        ))}
      </Card>
    </section>
  );
}
```

- [ ] **Step 9.3: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 9.4: Commit**

```bash
git add web/src/components/print/filament-mapping-row.tsx web/src/components/print/filaments-group.tsx
git commit -m "Add FilamentMappingRow + FilamentsGroup for Print filament tray map"
```

---

## Task 10: Build `<InfoBanner/>`, `<SlicingProgressCard/>`, `<SettingsTransferNote/>`

**Files:**
- Create: `web/src/components/print/info-banner.tsx`, `web/src/components/print/slicing-progress-card.tsx`, `web/src/components/print/settings-transfer-note.tsx`

Three small UI pieces grouped into one task because each is a thin wrapper.

- [ ] **Step 10.1: Create `web/src/components/print/info-banner.tsx`**

```typescript
import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { cn } from '@/lib/utils';

export type InfoBannerVariant = 'info' | 'warn' | 'success' | 'error';

const VARIANT_CLASSES: Record<InfoBannerVariant, string> = {
  info:    'bg-accent/10 border-accent/40 text-text-0 [&>svg+div]:text-text-0',
  warn:    'bg-warm/10 border-warm/40 text-text-0',
  success: 'bg-success/10 border-success/40 text-text-0',
  error:   'bg-danger/10 border-danger/40 text-text-0',
};

export function InfoBanner({
  variant,
  title,
  message,
  details,
  action,
}: {
  variant: InfoBannerVariant;
  title: string;
  /** Short body line (always shown). */
  message?: string;
  /** Long expandable body (e.g. an error stack). When present, a Details toggle appears. */
  details?: string;
  /** Optional right-aligned action button (e.g. Retry). */
  action?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <Alert className={cn('flex flex-col gap-2', VARIANT_CLASSES[variant])}>
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <AlertTitle className="text-[14px] font-semibold text-white">{title}</AlertTitle>
          {message && (
            <AlertDescription className="text-sm text-text-0">{message}</AlertDescription>
          )}
        </div>
        {action}
      </div>
      {details && (
        <>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="self-start flex items-center gap-1 text-[12px] text-text-1 hover:text-white"
          >
            {open ? <ChevronDown className="w-3.5 h-3.5" aria-hidden /> : <ChevronRight className="w-3.5 h-3.5" aria-hidden />}
            Details
          </button>
          {open && (
            <pre className="text-[12px] font-mono text-text-1 whitespace-pre-wrap break-words bg-bg-0/40 rounded p-2">
              {details}
            </pre>
          )}
        </>
      )}
    </Alert>
  );
}
```

- [ ] **Step 10.2: Create `web/src/components/print/slicing-progress-card.tsx`**

```typescript
import { Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Button } from '@/components/ui/button';

export function SlicingProgressCard({
  title,
  statusLine,
  percent,
  onCancel,
  cancelDisabled = false,
}: {
  title: string;
  statusLine: string;
  /** 0–100; pass null for indeterminate. */
  percent: number | null;
  onCancel: () => void;
  cancelDisabled?: boolean;
}) {
  const value = percent != null ? Math.max(0, Math.min(100, percent)) : 0;
  return (
    <Card className="sticky top-2 z-10 p-4 bg-card border border-accent/40 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-white">
          <Loader2 className="w-4 h-4 text-accent animate-spin" aria-hidden />
          <span className="text-[14px] font-semibold">{title}</span>
        </div>
        <Button
          type="button"
          onClick={onCancel}
          disabled={cancelDisabled}
          variant="ghost"
          className="h-auto py-1 px-2 text-danger text-[13px] font-semibold hover:text-danger/80"
        >
          Cancel
        </Button>
      </div>
      <Progress
        value={value}
        className="h-1.5 bg-bg-1 [&>div]:bg-gradient-to-r [&>div]:from-accent-strong [&>div]:to-accent"
      />
      <div className="text-[12px] font-mono text-text-1 truncate">{statusLine || '—'}</div>
    </Card>
  );
}
```

- [ ] **Step 10.3: Create `web/src/components/print/settings-transfer-note.tsx`**

```typescript
import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { SettingsTransferInfo } from '@/lib/api/types';

/**
 * Read-only collapsed summary of the slicer's settings_transfer report.
 * Emitted alongside the SSE `result` event when the slicer applied or
 * discarded process/filament customizations.
 */
export function SettingsTransferNote({ info }: { info: SettingsTransferInfo | null }) {
  const [open, setOpen] = useState(false);
  if (!info) return null;
  const transferredCount = info.transferred?.length ?? 0;
  const filamentNotes = info.filaments ?? [];
  const hasContent = transferredCount > 0 || filamentNotes.length > 0;
  if (!hasContent) return null;

  return (
    <div className="flex flex-col gap-1 text-[12px] text-text-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="self-start flex items-center gap-1 hover:text-white"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5" aria-hidden /> : <ChevronRight className="w-3.5 h-3.5" aria-hidden />}
        Settings transferred ({transferredCount + filamentNotes.length})
      </button>
      {open && (
        <ul className="list-disc list-inside font-mono">
          {info.transferred?.map((s) => (
            <li key={`t-${s.key}`}>
              {s.key}: {s.value}
              {s.original ? ` (was ${s.original})` : ''}
            </li>
          ))}
          {filamentNotes.map((f) => (
            <li key={`f-${f.slot}`}>
              Slot {f.slot}: {f.status}
              {f.discarded && f.discarded.length > 0 ? ` — discarded ${f.discarded.join(', ')}` : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 10.4: Type-check + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0.

- [ ] **Step 10.5: Commit**

```bash
git add web/src/components/print/info-banner.tsx web/src/components/print/slicing-progress-card.tsx web/src/components/print/settings-transfer-note.tsx
git commit -m "Add InfoBanner, SlicingProgressCard, SettingsTransferNote"
```

---

## Task 11: Wire the Print route state machine

**Files:**
- Modify: `web/src/routes/print.tsx`

This is the integration task. The existing 9-line placeholder is replaced with a state-machine-driven composition. The route owns:

1. **State:** a discriminated union `PrintState` covering empty / imported / slicing / previewReady / uploading / sent.
2. **Side effects:**
   - On file drop or pick → call `parse3mf(file)`, call `getFilamentMatches(activePrinterId, info.filaments)`, transition to `imported`.
   - On Preview/Print click → call `usePrintStream().start({...preview: true|false})`, transition to `slicing`. SSE handlers mutate state through the slicing → previewReady / uploading → sent transitions.
   - On Confirm Print (from State D) → call `printFromPreview(previewId, activePrinterId)`, navigate to `/`.
   - On Cancel (slicing) → `usePrintStream().cancel()`, transition back to `imported`.
   - On Cancel (uploading) → call `cancelUpload(uploadId)`, the SSE error handler resets to `imported`.
3. **Slicer profile data:** loaded once with React Query (`['slicer-machines']`, etc.) — these don't change between requests.

The state machine is intentionally inline in this file. It's ~250 lines but reads as one coherent flow; splitting hurts comprehensibility. If it grows past 300 lines later, extract to `usePrintFlow.ts`.

- [ ] **Step 11.1: Replace `web/src/routes/print.tsx`**

```typescript
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { DropZoneCard, DropOverlay } from '@/components/print/drop-zone';
import { PlateCard } from '@/components/print/plate-card';
import { SlicingSettingsGroup, type SlicingSettings } from '@/components/print/slicing-settings-group';
import type { SettingOption } from '@/components/print/setting-row';
import { FilamentsGroup, type FilamentMapping } from '@/components/print/filaments-group';
import { InfoBanner } from '@/components/print/info-banner';
import { SlicingProgressCard } from '@/components/print/slicing-progress-card';
import { SettingsTransferNote } from '@/components/print/settings-transfer-note';
import { parse3mf } from '@/lib/api/3mf';
import {
  getSlicerMachines,
  getSlicerProcesses,
  getSlicerPlateTypes,
} from '@/lib/api/slicer-profiles';
import { getAms } from '@/lib/api/ams';
import { listPrinters } from '@/lib/api/printers';
import { getFilamentMatches } from '@/lib/api/filament-matches';
import { cancelUpload } from '@/lib/api/uploads';
import { printFromPreview } from '@/lib/api/print';
import { usePrintStream } from '@/lib/use-print-stream';
import { useDropZone } from '@/lib/use-drop-zone';
import { usePrinterContext } from '@/lib/printer-context';
import type {
  AMSTray,
  SettingsTransferInfo,
  ThreeMFInfo,
} from '@/lib/api/types';
import { cn } from '@/lib/utils';

type PrintState =
  | { kind: 'empty' }
  | {
      kind: 'imported';
      file: File;
      info: ThreeMFInfo;
      banner?: BannerData;
    }
  | {
      kind: 'slicing';
      file: File;
      info: ThreeMFInfo;
      percent: number | null;
      statusLine: string;
    }
  | {
      kind: 'previewReady';
      file: File;
      info: ThreeMFInfo;
      previewId: string;
      transfer: SettingsTransferInfo | null;
    }
  | {
      kind: 'uploading';
      file: File;
      info: ThreeMFInfo;
      uploadId: string;
      percent: number;
    }
  | { kind: 'sent' };

interface BannerData {
  variant: 'info' | 'warn' | 'success' | 'error';
  title: string;
  message?: string;
  details?: string;
}

export default function PrintRoute() {
  const { activePrinterId } = usePrinterContext();
  const navigate = useNavigate();

  const [state, setState] = useState<PrintState>({ kind: 'empty' });
  const [settings, setSettings] = useState<SlicingSettings>({
    machine: '',
    process: '',
    plateType: '',
  });
  const [selectedPlateId, setSelectedPlateId] = useState<number>(1);
  const [filamentMapping, setFilamentMapping] = useState<FilamentMapping>({});

  // Slicer catalogs — load once, don't refetch automatically.
  const machinesQuery = useQuery({
    queryKey: ['slicer', 'machines'],
    queryFn: getSlicerMachines,
    staleTime: Infinity,
  });
  const processesQuery = useQuery({
    queryKey: ['slicer', 'processes', settings.machine],
    queryFn: () => getSlicerProcesses(settings.machine || undefined),
    staleTime: Infinity,
    enabled: !!settings.machine,
  });
  const plateTypesQuery = useQuery({
    queryKey: ['slicer', 'plate-types'],
    queryFn: getSlicerPlateTypes,
    staleTime: Infinity,
  });

  // Active printer's name (for the "Target printer" subtitle).
  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: listPrinters,
    refetchInterval: 4_000,
  });
  const activePrinter = printersQuery.data?.printers.find((p) => p.id === activePrinterId);
  const activePrinterName = activePrinter?.name ?? null;

  // AMS for the active printer (for tray dropdowns).
  const amsQuery = useQuery({
    queryKey: ['ams', activePrinterId],
    queryFn: () => getAms(activePrinterId ?? undefined),
    refetchInterval: 4_000,
    enabled: !!activePrinterId,
    retry: false,
  });
  const trays: AMSTray[] = useMemo(() => {
    if (!amsQuery.data) return [];
    const list = [...amsQuery.data.trays];
    if (amsQuery.data.vt_tray) list.push(amsQuery.data.vt_tray);
    return list;
  }, [amsQuery.data]);

  // SSE consumer.
  const stream = usePrintStream();

  // Drag-and-drop is active in every state EXCEPT slicing/uploading
  // (replacing the file mid-stream would be confusing).
  const ddEnabled = state.kind !== 'slicing' && state.kind !== 'uploading';
  const onDropFile = (file: File) => void importFile(file);
  const { dragging } = useDropZone({ accept: '.3mf', onFile: onDropFile, enabled: ddEnabled });

  async function importFile(file: File) {
    try {
      const info = await parse3mf(file);
      // Default plate selection.
      const firstPlate = info.plates[0]?.id ?? 1;
      setSelectedPlateId(firstPlate);
      // Pre-populate slicing settings from the 3MF if present.
      setSettings((prev) => ({
        machine: prev.machine || info.printer.printer_settings_id || '',
        process: prev.process || info.print_profile.print_settings_id || '',
        plateType: prev.plateType,
      }));
      // Filament-tray defaults via backend matcher.
      const initialMapping: FilamentMapping = {};
      if (activePrinterId) {
        try {
          const matches = await getFilamentMatches(activePrinterId, info.filaments);
          for (const m of matches.matches) {
            initialMapping[m.index] = m.preferred_tray_slot ?? -1;
          }
        } catch {
          // Non-fatal — user can pick manually.
        }
      }
      setFilamentMapping(initialMapping);
      const banner: BannerData = info.has_gcode
        ? {
            variant: 'warn',
            title: 'This 3MF already contains G-code.',
            message: 'AMS tray selections and project filament overrides are ignored.',
          }
        : { variant: 'info', title: 'File parsed — slicing required.' };
      setState({ kind: 'imported', file, info, banner });
    } catch (err) {
      toast.error(`Failed to parse 3MF: ${(err as Error).message}`);
    }
  }

  function clearImport() {
    stream.cancel();
    setState({ kind: 'empty' });
    setFilamentMapping({});
  }

  function buildFilamentProfilesPayload(
    info: ThreeMFInfo,
  ): Record<string, { profile_setting_id: string; tray_slot: number }> {
    const out: Record<string, { profile_setting_id: string; tray_slot: number }> = {};
    if (info.has_gcode) return out;
    for (const filament of info.filaments) {
      const slot = filamentMapping[filament.index];
      if (slot == null || slot < 0) continue;
      const tray = trays.find((t) => t.slot === slot);
      const settingId = tray?.matched_filament?.setting_id ?? '';
      if (!settingId) continue;
      out[String(filament.index)] = { profile_setting_id: settingId, tray_slot: slot };
    }
    return out;
  }

  function startSlicing(file: File, info: ThreeMFInfo, preview: boolean) {
    if (!settings.machine || !settings.process) {
      toast.error('Pick a machine and process before slicing.');
      return;
    }
    setState({ kind: 'slicing', file, info, percent: null, statusLine: 'Starting…' });
    void stream.start(
      {
        file,
        printerId: activePrinterId ?? undefined,
        plateId: selectedPlateId,
        machineProfile: settings.machine,
        processProfile: settings.process,
        filamentProfiles: buildFilamentProfilesPayload(info),
        plateType: settings.plateType || undefined,
        preview,
      },
      {
        onStatus: (s) => setState((cur) => (cur.kind === 'slicing' ? { ...cur, statusLine: s.message } : cur)),
        onProgress: (p) =>
          setState((cur) =>
            cur.kind === 'slicing'
              ? {
                  ...cur,
                  percent: typeof p.percent === 'number' ? p.percent : cur.percent,
                  statusLine: typeof p.status_line === 'string' ? p.status_line : cur.statusLine,
                }
              : cur,
          ),
        onResult: (r) => {
          if (preview && r.preview_id) {
            setState({
              kind: 'previewReady',
              file,
              info,
              previewId: r.preview_id,
              transfer: r.settings_transfer ?? null,
            });
          }
          // For non-preview, the upload phase is signalled by the next `status`/`upload_progress` events.
        },
        onUploadProgress: (u) =>
          setState((cur) => {
            if (cur.kind === 'slicing') {
              // Should have transitioned via onStatus(uploading) — fall through.
              return { kind: 'uploading', file, info, uploadId: '', percent: u.percent };
            }
            if (cur.kind === 'uploading') return { ...cur, percent: u.percent };
            return cur;
          }),
        onPrintStarted: (p) => {
          toast.success(`Print started on ${activePrinterName ?? p.printer_id}`);
          setState({ kind: 'sent' });
          navigate('/');
        },
        onError: (e) => {
          setState({
            kind: 'imported',
            file,
            info,
            banner: { variant: 'error', title: 'Slicing failed', details: e.error },
          });
        },
        onDone: () => {
          // Stream closed; state should already be terminal (previewReady/sent/error).
        },
      },
    );
    // Bridge: when the SSE stream emits `status` with phase=uploading, switch to uploading state
    // — handled in onStatus above by inspecting `s.upload_id` if present.
  }

  // Listen for the upload_id from the status event so cancel works.
  useEffect(() => {
    // Hook up `onStatus` to also patch upload_id; the closure above only
    // updates statusLine. We expose this via a dedicated handler:
  }, []);

  async function confirmPrint() {
    if (state.kind !== 'previewReady') return;
    try {
      await printFromPreview(state.previewId, activePrinterId ?? undefined);
      toast.success(`Print started on ${activePrinterName ?? 'printer'}`);
      setState({ kind: 'sent' });
      navigate('/');
    } catch (err) {
      toast.error(`Print failed: ${(err as Error).message}`);
    }
  }

  function cancelSlicing() {
    if (state.kind !== 'slicing') return;
    stream.cancel();
    setState({ kind: 'imported', file: state.file, info: state.info, banner: undefined });
  }

  async function cancelUploading() {
    if (state.kind !== 'uploading') return;
    if (!state.uploadId) {
      stream.cancel();
      setState({ kind: 'imported', file: state.file, info: state.info, banner: undefined });
      return;
    }
    try {
      await cancelUpload(state.uploadId);
      // The SSE error handler will move state back to imported.
    } catch (err) {
      toast.error(`Cancel failed: ${(err as Error).message}`);
    }
  }

  // Build options for the select rows.
  const machineOptions: SettingOption[] = useMemo(() => {
    const base = (machinesQuery.data ?? []).map((m) => ({ value: m.setting_id, label: m.name }));
    if (state.kind !== 'empty' && state.kind !== 'sent') {
      const fileMachine = state.info.printer.printer_settings_id;
      if (fileMachine && !base.some((o) => o.value === fileMachine)) {
        base.unshift({ value: fileMachine, label: fileMachine, fromFileMismatch: true });
      }
    }
    return base;
  }, [machinesQuery.data, state]);

  const processOptions: SettingOption[] = useMemo(() => {
    return (processesQuery.data ?? []).map((p) => ({ value: p.setting_id, label: p.name }));
  }, [processesQuery.data]);

  const plateTypeOptions: SettingOption[] = useMemo(() => {
    return (plateTypesQuery.data ?? []).map((p) => ({ value: p.value, label: p.label }));
  }, [plateTypesQuery.data]);

  // --- Render ---

  return (
    <div className="flex flex-col gap-6">
      <DropOverlay visible={dragging} />

      <header>
        <h1 className="text-[28px] font-extrabold tracking-tight text-white">Print</h1>
      </header>

      {state.kind === 'empty' && (
        <DropZoneCard onFile={onDropFile} targetPrinterName={activePrinterName} />
      )}

      {(state.kind === 'slicing' || state.kind === 'uploading') && (
        <SlicingProgressCard
          title={state.kind === 'slicing' ? 'Slicing…' : 'Uploading to printer…'}
          statusLine={state.kind === 'slicing' ? state.statusLine : `${state.percent}%`}
          percent={state.kind === 'slicing' ? state.percent : state.percent}
          onCancel={state.kind === 'slicing' ? cancelSlicing : cancelUploading}
        />
      )}

      {(state.kind === 'imported' || state.kind === 'previewReady') && (
        <div className={cn('flex flex-col gap-5')}>
          <PlateCard
            filename={state.file.name}
            info={state.info}
            selectedPlateId={selectedPlateId}
            onClear={clearImport}
          />
          <SlicingSettingsGroup
            settings={settings}
            onChange={setSettings}
            machineOptions={machineOptions}
            processOptions={processOptions}
            plateTypeOptions={plateTypeOptions}
            disabled={state.kind === 'previewReady'}
          />
          <FilamentsGroup
            projectFilaments={state.info.filaments}
            trays={trays}
            mapping={filamentMapping}
            onChange={setFilamentMapping}
            disabled={state.info.has_gcode || state.kind === 'previewReady'}
          />
          {state.kind === 'imported' && state.banner && (
            <InfoBanner
              variant={state.banner.variant}
              title={state.banner.title}
              message={state.banner.message}
              details={state.banner.details}
            />
          )}
          {state.kind === 'previewReady' && (
            <>
              <InfoBanner
                variant="success"
                title="Preview ready"
                message="Review the sliced file, then confirm the print."
              />
              <SettingsTransferNote info={state.transfer} />
            </>
          )}
          <ActionButtons
            kind={state.kind}
            previewId={state.kind === 'previewReady' ? state.previewId : null}
            onPreview={() => startSlicing(state.file, state.info, true)}
            onPrint={() => startSlicing(state.file, state.info, false)}
            onReslice={() => startSlicing(state.file, state.info, true)}
            onConfirmPrint={confirmPrint}
          />
        </div>
      )}
    </div>
  );
}

function ActionButtons({
  kind,
  previewId,
  onPreview,
  onPrint,
  onReslice,
  onConfirmPrint,
}: {
  kind: 'imported' | 'previewReady';
  previewId: string | null;
  onPreview: () => void;
  onPrint: () => void;
  onReslice: () => void;
  onConfirmPrint: () => void;
}) {
  if (kind === 'imported') {
    return (
      <div className="grid grid-cols-2 gap-2.5">
        <Button
          type="button"
          onClick={onPreview}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          ◉ Preview
        </Button>
        <Button
          type="button"
          onClick={onPrint}
          className="rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11 text-[14px] font-semibold"
        >
          ⎙ Print
        </Button>
      </div>
    );
  }
  // previewReady — three buttons: Re-slice | Download 3MF | Confirm Print
  return (
    <div className="grid grid-cols-3 gap-2.5">
      <Button
        type="button"
        onClick={onReslice}
        className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
      >
        <RotateCcw className="w-4 h-4 mr-1.5" aria-hidden /> Re-slice
      </Button>
      <a
        href={`/api/print?preview_id=${encodeURIComponent(previewId ?? '')}&slice_only=true`}
        download
        className="inline-flex items-center justify-center rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
      >
        Download 3MF
      </a>
      <Button
        type="button"
        onClick={onConfirmPrint}
        className="rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11 text-[14px] font-semibold"
      >
        Confirm Print
      </Button>
    </div>
  );
}
```

- [ ] **Step 11.2: Patch `onStatus` to capture `upload_id` for cancellation**

Inside `startSlicing`, the `onStatus` handler currently only updates `statusLine`. Replace it with this expanded version:

```typescript
        onStatus: (s) =>
          setState((cur) => {
            if (cur.kind === 'slicing') {
              if (s.upload_id) {
                // Backend signalled the transition into the FTP upload phase.
                return {
                  kind: 'uploading',
                  file,
                  info,
                  uploadId: s.upload_id,
                  percent: 0,
                };
              }
              return { ...cur, statusLine: s.message };
            }
            if (cur.kind === 'uploading' && s.upload_id && !cur.uploadId) {
              return { ...cur, uploadId: s.upload_id };
            }
            return cur;
          }),
```

Also delete the now-dead `useEffect` placeholder block at the bottom of the component:

```typescript
  // Listen for the upload_id from the status event so cancel works.
  useEffect(() => {
    // Hook up `onStatus` to also patch upload_id; the closure above only
    // updates statusLine. We expose this via a dedicated handler:
  }, []);
```

— remove that block and remove `useEffect` from the React import if no other usage remains. (The current implementation has none.)

- [ ] **Step 11.3: Type-check + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0. The bundle will grow noticeably — Print is the largest route by far.

- [ ] **Step 11.4: Commit**

```bash
git add web/src/routes/print.tsx
git commit -m "Wire Print route state machine: empty/imported/slice/preview/upload"
```

Subject is exactly 64 chars — over. Use `Wire Print route state machine across six observable states` (58 chars).

---

## Task 12: Manual smoke-test in the dev server

**Files:** none modified.

- [ ] **Step 12.1: Confirm the FastAPI backend has `ORCASLICER_API_URL` set**

Run: `grep ORCASLICER_API_URL .env || echo "Not set — slicing will fail; set ORCASLICER_API_URL=http://10.0.1.9:8070 (or your slicer host) and restart"`

If unset, add it to `.env` and restart `python -m app`.

- [ ] **Step 12.2: Build the bundle and reload `/beta/print`**

```bash
cd web && npm run build
```

Open `http://localhost:4844/beta/print`. The header tab should highlight `Print`.

- [ ] **Step 12.3: Walk the six states**

DevTools → Console + Network. Walk through:

1. **State A (Empty):** subtitle reads `Target printer: <your printer>`. Drop zone card shows "Drop a .3mf file here". Click "Choose file…" → file picker. Cancel. Drag a .3mf file from Finder over the page → tinted overlay appears center-screen with "Drop to import". Drop → state transitions to B.
2. **State B (Imported):** PlateCard with thumbnail (or "No preview" placeholder), filename, meta line, Clear button. Slicing settings card with Machine / Process / Plate type rows — Machine pre-selected from the 3MF if it matches, else first option. Filaments card with one row per project filament. Each filament row shows its color dot, type (e.g. PLA), name, and tray dropdown. The dropdown should default to the matched tray from `/api/filament-matches`. The blue info banner reads "File parsed — slicing required." Two buttons: `◉ Preview` (neutral) and `⎙ Print` (gradient).
3. **State B (gcode 3MF):** Drop a 3MF that already contains G-code → amber banner "This 3MF already contains G-code. AMS tray selections and project filament overrides are ignored." Filament rows show greyed/disabled.
4. **State C (Slicing):** Click `◉ Preview`. Sticky blue-bordered card slides in at top: spinner + "Slicing…" + Cancel link, gradient progress bar, mono status line fed from SSE `progress` events. Network tab shows `POST /api/print-stream`. Click Cancel → returns to State B with previous form intact.
5. **State D (Preview ready):** Wait for slicing to complete. Banner flips to green "Preview ready". Buttons swap to `Download 3MF` (neutral) + `Confirm Print` (gradient). Settings transferred summary appears below the banner if the slicer applied or discarded customizations. Click Download → browser saves a `.3mf` file (the sliced output via `slice_only`). Click Confirm Print → calls `POST /api/print` with `preview_id`, toast "Print started on …", navigates back to `/beta` (Dashboard).
6. **State E (Upload, non-preview path):** Back to /beta/print. Drop a fresh 3MF, confirm settings, click `⎙ Print` (NOT preview). Sticky card title becomes "Uploading to printer…" once the slicer finishes; progress bar reflects FTP upload percentage. Click Cancel → calls `POST /api/uploads/{id}/cancel`, returns to State B.
7. **State F (Sent):** End-to-end successful print → toast + navigate to Dashboard. The Dashboard's Hero immediately shows the new job within ~4s (next poll).
8. **Console clean:** no React warnings about missing keys, hydration, unmounted setState. No 4xx/5xx errors except the one you intentionally triggered with Cancel.

If any step fails, **do not proceed to Task 13** — fix the relevant component task first.

---

## Task 13: README + final audit

**Files:**
- Modify: `README.md`

- [ ] **Step 13.1: Update `README.md`**

Find the Phase 3 line under the `Frontend` section. Append below it:

```markdown
- **Phase 4:** `/beta/print` is now the full Print flow — drag-and-drop a `.3mf` anywhere on the page, choose machine/process/plate, map project filaments to AMS trays, then preview-then-confirm or print directly. Slicing and FTP upload progress stream over SSE with cancel support.
```

- [ ] **Step 13.2: Commit**

```bash
git add README.md
git commit -m "README: document Phase 4 Print flow at /beta/print"
```

- [ ] **Step 13.3: Final audit checklist**

- [ ] `cd web && npm run lint` exits 0.
- [ ] `cd web && npm run build` exits 0.
- [ ] `.venv/bin/pytest` exits 0 (Phase 4 doesn't add backend tests but must not break existing ones).
- [ ] `git status` is clean.
- [ ] All 13 tasks above committed (`git log --oneline` shows ~14 new commits since the start of Phase 4).
- [ ] Old Jinja UI at `/` renders unchanged (`curl -s http://localhost:4844/ | grep '<title>'` still returns the Jinja title).
- [ ] No file in `app/` was modified (`git diff <phase4-base>..HEAD -- app/` is empty).

If all items check out, Phase 4 is complete. Phase 5 (Settings redesign) is the next plan; Phase 6 is the cutover that finally moves `/beta/*` → `/*` and deletes the Jinja templates.
