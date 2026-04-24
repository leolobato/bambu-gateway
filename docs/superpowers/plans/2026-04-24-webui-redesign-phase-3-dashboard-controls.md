# Web UI Redesign — Phase 3: Dashboard Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add interactive control surfaces to the Phase 2 read-only Dashboard at `/beta`: Pause/Resume + Cancel pair (or "Open Print" when idle), a `<Select/>`-driven Speed chip with optimistic updates, and a tray-detail `<Sheet/>` that exposes filament metadata plus AMS drying start/stop. Also lifts the Phase 2 limitation where `/api/ams` was wired to the default printer only — the backend gains a `?printer_id=` query param so the picker fully drives AMS too.

**Architecture:** A small backend addition (one query param on `/api/ams` plus a pytest test) unblocks per-printer AMS. On the frontend, all command endpoints are wrapped in a typed `printer-commands.ts` module that returns `Promise<void>` and throws `ApiError`. Each control is a thin React component that uses `useMutation` from React Query. Speed uses the optimistic-update pattern (`onMutate` patches the `['printers']` cache, `onError` rolls back, `onSettled` invalidates). Pause/Resume/Cancel use simple invalidate-on-success. The tray sheet lifts selection state to `<AmsSection/>` so a single `<Sheet/>` instance is reused across rows. Drying form state is plain `useState` (only two inputs; `react-hook-form` is overkill here).

**Tech Stack:** All Phase 1/2 deps stay. New shadcn primitives this phase installs: `select`, `sheet`, `alert-dialog`. New dependency from shadcn: `@radix-ui/react-select`. (`@radix-ui/react-dialog` is already installed from Phase 2; `<Sheet/>` and `<AlertDialog/>` both build on it.) Backend gains nothing new — it uses FastAPI's existing `Query` import from `fastapi`.

**Spec reference:** `docs/superpowers/specs/2026-04-23-webui-redesign-design.md` — sections "Action buttons" (Pause/Resume/Cancel/Open Print), "Stat chips" (Speed select rule), "AMS section" (tray sheet + drying), and the "Incremental ship plan" step 3.

**Backend gap that this phase closes:** Phase 2 noted that `/api/ams` returned only the default printer's AMS. Task 0 of this plan adds `?printer_id=` and Tasks 1 update the frontend to use it. After this phase, the AMS section renders for whichever printer the picker has selected.

---

## File structure

**Created in this plan:**

```
web/
└── src/
    ├── lib/
    │   ├── api/
    │   │   └── printer-commands.ts    # pausePrint, resumePrint, cancelPrint, setPrinterSpeed, startDrying, stopDrying
    │   └── use-media-query.ts         # useMediaQuery(query) hook (shared by TraySheet)
    └── components/
        └── dashboard/
            ├── control-buttons.tsx     # Pause/Resume + Cancel | Open Print | nothing, with mutations
            ├── speed-select.tsx        # <Select/> trigger with optimistic mutation
            └── tray-sheet.tsx          # <Sheet/> with filament summary + drying form
tests/
└── test_ams_endpoint.py                # pytest for the /api/ams ?printer_id= behavior
```

**Modified in this plan:**

- `app/main.py` — `get_ams()` accepts `printer_id: str | None = Query(default=None)`; resolves via existing `_resolve_printer_id` helper when provided, falls back to `default_printer_id()` otherwise (preserves backward compatibility).
- `web/src/lib/api/ams.ts` — `getAms()` accepts an optional `printerId: string` and appends `?printer_id=…` when set.
- `web/src/routes/dashboard.tsx` — query key includes the active printer id; `getAms(active.id)` is called; `<ControlButtons/>` is inserted between `<StatChipsRow/>` and `<AmsSection/>`.
- `web/src/components/dashboard/stat-chips-row.tsx` — Speed `<StatChip/>` (read-only) is replaced by `<SpeedSelect printer={printer}/>`.
- `web/src/components/dashboard/ams-section.tsx` — drops the "AMS only available for default printer" warning (no longer applicable); each `<TrayRow/>` now wires `onClick` to open the new sheet; renders a single `<TraySheet/>` instance keyed by selection state.

**Untouched in this plan:**

- `web/src/components/{state-badge,printer-picker,stat-chip,tray-row,app-shell}.tsx` and `web/src/components/dashboard/hero-card.tsx` — already match Phase 3 needs.
- `web/src/lib/{api/{client,types,printers},filament-color,format,printer-context,utils}.ts` and `web/src/components/ui/*` — already complete.
- All Python files except `app/main.py` and the new test file. The MQTT/printer service control methods already exist (`pause_print`, `resume_print`, `cancel_print`, `set_print_speed`, `start_drying`, `stop_drying`).
- Old Jinja UI at `/` and `/settings` — Phase 6 cutover removes those.

## Prerequisites

- Phase 2 must be on `main` (commit `8d6f66e` or later — the README Phase 2 note).
- Local FastAPI at `http://localhost:4844` against at least one printer with at least one AMS unit (so drying controls are testable). If you don't have a real AMS-equipped printer the drying tasks can still be implemented and type-checked, but Step 9.x manual verification will be partial.
- `cd web && npm install` already done. Backend deps already installed (`pip install -r requirements.txt`).

---

## Task 0: Backend — accept `?printer_id=` on `/api/ams`

**Files:**
- Modify: `app/main.py:779-806` (the `get_ams()` route)
- Create: `tests/test_ams_endpoint.py`

This is a small additive change. The existing route stays backward-compatible: when no query param is provided, it falls back to `default_printer_id()` exactly as today. When provided, it resolves via the same helper used by all `_run_printer_command` callers.

- [ ] **Step 0.1: Write the failing test**

Create `tests/test_ams_endpoint.py`:

```python
"""Tests for GET /api/ams ?printer_id= routing."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import main as app_main


@pytest.fixture
def client(monkeypatch):
    """Build a TestClient with a stubbed printer_service that knows two printers."""
    class _StubAms:
        def __init__(self, pid: str) -> None:
            self.pid = pid

    class _StubService:
        def __init__(self) -> None:
            self._known = {"DEFAULT01", "OTHER02"}

        def default_printer_id(self) -> str:
            return "DEFAULT01"

        async def get_ams_info_async(self, pid: str):
            if pid not in self._known:
                return None
            # (trays, units, vt_tray) — empty is fine for routing test.
            return [], [], None

        def get_client(self, pid: str):
            # _resolve_printer_id calls this to verify existence
            return object() if pid in self._known else None

    service = _StubService()
    monkeypatch.setattr(app_main, "printer_service", service)

    # Skip the slicer-profile fetch — it would try to hit OrcaSlicer over HTTP.
    async def _no_filaments(_pid):
        return [], ""

    monkeypatch.setattr(app_main, "_get_machine_slicer_filaments", _no_filaments)

    return TestClient(app_main.app)


def test_getAms_noQuery_usesDefaultPrinter(client):
    """Without ?printer_id=, the route resolves the default printer."""
    response = client.get("/api/ams")
    assert response.status_code == 200
    assert response.json()["printer_id"] == "DEFAULT01"


def test_getAms_explicitPrinterId_usesThatPrinter(client):
    """With ?printer_id=OTHER02, the response reports that printer."""
    response = client.get("/api/ams?printer_id=OTHER02")
    assert response.status_code == 200
    assert response.json()["printer_id"] == "OTHER02"


def test_getAms_unknownPrinterId_returns404(client):
    """Bogus ?printer_id= must 404 instead of silently falling back."""
    response = client.get("/api/ams?printer_id=NOPE99")
    assert response.status_code == 404
```

- [ ] **Step 0.2: Run the test to verify it fails**

Run: `pytest tests/test_ams_endpoint.py -v`
Expected: 2 of 3 tests fail. The "default" case passes because that's today's behavior. The "explicit printer id" and "unknown printer id" cases fail — the current route ignores the query string entirely.

- [ ] **Step 0.3: Modify `app/main.py` to accept the query param**

Open `app/main.py` and find the `get_ams()` route (~line 779). It currently reads:

```python
@app.get("/api/ams", response_model=AMSResponse)
async def get_ams():
    pid = printer_service.default_printer_id()
    if pid is None:
        raise HTTPException(status_code=404, detail="No printers configured")
```

Replace it with:

```python
@app.get("/api/ams", response_model=AMSResponse)
async def get_ams(printer_id: str | None = Query(default=None)):
    if printer_id:
        pid = _resolve_printer_id(printer_id)  # raises 404 on unknown
    else:
        pid = printer_service.default_printer_id()
        if pid is None:
            raise HTTPException(status_code=404, detail="No printers configured")
```

`Query` is from `fastapi` — check the import line at the top of `app/main.py` (around line 1-30) and add `Query` to the existing `from fastapi import …` group if it's not already there. Verify with: `grep "^from fastapi" app/main.py`.

`_resolve_printer_id` is the helper at `app/main.py:435` that the existing `_run_printer_command` uses; reusing it keeps "unknown printer" behavior consistent across the API.

- [ ] **Step 0.4: Run the tests to verify they pass**

Run: `pytest tests/test_ams_endpoint.py -v`
Expected: 3 passed.

Then run the full suite to confirm nothing else broke:

Run: `pytest`
Expected: all existing tests still pass.

- [ ] **Step 0.5: Commit**

```bash
git add app/main.py tests/test_ams_endpoint.py
git commit -m "Allow GET /api/ams?printer_id= to target non-default printer"
```

---

## Task 1: Frontend — wire AMS query to the active printer

**Files:**
- Modify: `web/src/lib/api/ams.ts`
- Modify: `web/src/routes/dashboard.tsx`
- Modify: `web/src/components/dashboard/ams-section.tsx`

- [ ] **Step 1.1: Update `web/src/lib/api/ams.ts` to accept `printerId`**

Replace the file contents with:

```typescript
import { fetchJson } from './client';
import type { AMSResponse } from './types';

/**
 * Fetch AMS state. When `printerId` is omitted the backend falls back to the
 * default-configured printer (preserves Phase 2 behavior); when supplied it
 * targets exactly that printer or 404s on an unknown id.
 */
export async function getAms(printerId?: string): Promise<AMSResponse> {
  const path = printerId
    ? `/api/ams?printer_id=${encodeURIComponent(printerId)}`
    : '/api/ams';
  return fetchJson<AMSResponse>(path);
}
```

- [ ] **Step 1.2: Update the Dashboard route's AMS query**

Open `web/src/routes/dashboard.tsx`. Find the `amsQuery = useQuery({…})` block (currently around lines 23–29). Replace it with:

```typescript
  // Active id is null on first paint until the picker defaults; once known the
  // query key includes it so switching printers refetches without staleness.
  const amsTargetId = activePrinterId ?? printersQuery.data?.printers[0]?.id ?? null;
  const amsQuery = useQuery({
    queryKey: ['ams', amsTargetId],
    queryFn: () => getAms(amsTargetId ?? undefined),
    refetchInterval: 4_000,
    enabled: !!amsTargetId,
    retry: false,
  });
```

Then find the `<AmsSection .../>` JSX (currently around lines 65–69) and remove the `activePrinterId={active.id}` prop — it's no longer needed because the section no longer needs to gate by id. The new call site is:

```typescript
      {amsQuery.data && (
        <AmsSection
          ams={amsQuery.data}
          activeTrayId={active.active_tray}
        />
      )}
```

- [ ] **Step 1.3: Drop the printer-id mismatch warning from `<AmsSection/>`**

Open `web/src/components/dashboard/ams-section.tsx`. Update the props interface and the early returns:

Replace:

```typescript
export function AmsSection({
  activePrinterId,
  ams,
  activeTrayId,
}: {
  activePrinterId: string;
  ams: AMSResponse;
  activeTrayId: number | null;
}) {
  if (ams.printer_id !== activePrinterId) {
    return (
      <p className="text-xs text-text-2">
        AMS data is only available for the default printer (backend follow-up planned).
      </p>
    );
  }

  if (ams.units.length === 0 && !ams.vt_tray) return null;
```

With:

```typescript
export function AmsSection({
  ams,
  activeTrayId,
}: {
  ams: AMSResponse;
  activeTrayId: number | null;
}) {
  if (ams.units.length === 0 && !ams.vt_tray) return null;
```

- [ ] **Step 1.4: Type-check + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0. The bundle should still build (slightly smaller — one branch removed).

- [ ] **Step 1.5: Commit**

```bash
git add web/src/lib/api/ams.ts web/src/routes/dashboard.tsx web/src/components/dashboard/ams-section.tsx
git commit -m "Drive /api/ams off the active printer instead of the default"
```

---

## Task 2: Install shadcn primitives needed for Phase 3

**Files:**
- Create (via shadcn CLI): `web/src/components/ui/select.tsx`, `web/src/components/ui/sheet.tsx`, `web/src/components/ui/alert-dialog.tsx`
- Modify: `web/package.json`, `web/package-lock.json` (CLI installs `@radix-ui/react-select` plus any other peer deps it detects; `@radix-ui/react-dialog` and `@radix-ui/react-alert-dialog` may also be added)

- [ ] **Step 2.1: Add the three primitives in one CLI run**

Run from the repo root:

```bash
cd web && npx shadcn@latest add select sheet alert-dialog --yes --overwrite
```

Expected: the CLI prints each component as it's added, runs `npm install` to pull peer deps, and writes the three `.tsx` files into `web/src/components/ui/`.

If the CLI prompts about React 19 conflicts, accept defaults — Phase 1 pinned React 18.3 and shadcn supports it.

- [ ] **Step 2.2: Verify the primitives compile and build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0.

- [ ] **Step 2.3: Commit**

```bash
git add web/components.json web/package.json web/package-lock.json web/src/components/ui/
git commit -m "Install shadcn select/sheet/alert-dialog for Phase 3"
```

---

## Task 3: Add typed command-mutation helpers

**Files:**
- Create: `web/src/lib/api/printer-commands.ts`

These wrap the existing `POST /api/printers/{id}/{pause,resume,cancel,speed}` and `POST /api/printers/{id}/ams/{ams_id}/{start-drying,stop-drying}` endpoints. Each function returns `Promise<void>` (the response body is `{status, printer_id, command}` but no caller currently needs it) and throws `ApiError` on non-2xx via `fetchJson`.

- [ ] **Step 3.1: Create `web/src/lib/api/printer-commands.ts`**

```typescript
import { fetchJson } from './client';
import type { SpeedLevel } from './types';

export async function pausePrint(printerId: string): Promise<void> {
  await fetchJson<unknown>(`/api/printers/${encodeURIComponent(printerId)}/pause`, {
    method: 'POST',
  });
}

export async function resumePrint(printerId: string): Promise<void> {
  await fetchJson<unknown>(`/api/printers/${encodeURIComponent(printerId)}/resume`, {
    method: 'POST',
  });
}

export async function cancelPrint(printerId: string): Promise<void> {
  await fetchJson<unknown>(`/api/printers/${encodeURIComponent(printerId)}/cancel`, {
    method: 'POST',
  });
}

export async function setPrinterSpeed(
  printerId: string,
  level: SpeedLevel,
): Promise<void> {
  await fetchJson<unknown>(`/api/printers/${encodeURIComponent(printerId)}/speed`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ level }),
  });
}

export interface StartDryingParams {
  /** Drying temperature in °C. Backend defaults to 55. */
  temperature: number;
  /** Drying duration in minutes. Backend defaults to 480. */
  durationMinutes: number;
}

export async function startDrying(
  printerId: string,
  amsId: number,
  params: StartDryingParams,
): Promise<void> {
  await fetchJson<unknown>(
    `/api/printers/${encodeURIComponent(printerId)}/ams/${amsId}/start-drying`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        temperature: params.temperature,
        duration_minutes: params.durationMinutes,
      }),
    },
  );
}

export async function stopDrying(printerId: string, amsId: number): Promise<void> {
  await fetchJson<unknown>(
    `/api/printers/${encodeURIComponent(printerId)}/ams/${amsId}/stop-drying`,
    { method: 'POST' },
  );
}
```

- [ ] **Step 3.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 3.3: Commit**

```bash
git add web/src/lib/api/printer-commands.ts
git commit -m "Add typed mutation helpers for printer control endpoints"
```

---

## Task 4: Add the `useMediaQuery` hook

**Files:**
- Create: `web/src/lib/use-media-query.ts`

Used by `<TraySheet/>` (Task 6) to choose `side="right"` on tablet/desktop and `side="bottom"` on phone, per the spec.

- [ ] **Step 4.1: Create `web/src/lib/use-media-query.ts`**

```typescript
import { useEffect, useState } from 'react';

/**
 * Subscribe to a CSS media query and return its current match state.
 * Server-safe: returns `false` until the first effect runs.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mql = window.matchMedia(query);
    setMatches(mql.matches);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, [query]);

  return matches;
}
```

- [ ] **Step 4.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 4.3: Commit**

```bash
git add web/src/lib/use-media-query.ts
git commit -m "Add useMediaQuery hook for breakpoint-aware components"
```

---

## Task 5: Build `<ControlButtons/>`

**Files:**
- Create: `web/src/components/dashboard/control-buttons.tsx`

Three render modes from the spec:

| Condition | Render |
|---|---|
| `state` ∈ `printing` / `preparing` / `paused` | Pause-or-Resume + Cancel pair |
| `state` ∈ `idle` / `finished` / `cancelled` | Single "Open Print" button |
| `!online` or `state === 'error'` | Render nothing (HeroCard already conveys the state) |

Pause-vs-Resume label flips on `state === 'paused'`. Cancel uses an `<AlertDialog/>` confirmation. All mutations invalidate `['printers']` on success and `toast.error` on failure.

- [ ] **Step 5.1: Create `web/src/components/dashboard/control-buttons.tsx`**

```typescript
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Pause, Play, X } from 'lucide-react';
import { toast } from 'sonner';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { cancelPrint, pausePrint, resumePrint } from '@/lib/api/printer-commands';
import type { PrinterStatus } from '@/lib/api/types';
import { cn } from '@/lib/utils';

const ACTIVE_STATES = new Set<PrinterStatus['state']>(['printing', 'preparing', 'paused']);
const IDLE_STATES = new Set<PrinterStatus['state']>(['idle', 'finished', 'cancelled']);

export function ControlButtons({ printer }: { printer: PrinterStatus }) {
  if (!printer.online) return null;
  if (printer.state === 'error') return null;
  if (ACTIVE_STATES.has(printer.state)) return <ActiveControls printer={printer} />;
  if (IDLE_STATES.has(printer.state)) return <OpenPrintButton />;
  return null;
}

function ActiveControls({ printer }: { printer: PrinterStatus }) {
  const queryClient = useQueryClient();
  const isPaused = printer.state === 'paused';

  const pauseResume = useMutation({
    mutationFn: () => (isPaused ? resumePrint(printer.id) : pausePrint(printer.id)),
    onSuccess: () => {
      toast.success(isPaused ? 'Resuming…' : 'Pausing…');
      queryClient.invalidateQueries({ queryKey: ['printers'] });
    },
    onError: (err: Error) => {
      toast.error(`${isPaused ? 'Resume' : 'Pause'} failed: ${err.message}`);
    },
  });

  const [confirmOpen, setConfirmOpen] = useState(false);
  const cancel = useMutation({
    mutationFn: () => cancelPrint(printer.id),
    onSuccess: () => {
      toast.success('Cancelling…');
      setConfirmOpen(false);
      queryClient.invalidateQueries({ queryKey: ['printers'] });
    },
    onError: (err: Error) => {
      toast.error(`Cancel failed: ${err.message}`);
    },
  });

  return (
    <>
      <div className="grid grid-cols-2 gap-2.5">
        <Button
          type="button"
          onClick={() => pauseResume.mutate()}
          disabled={pauseResume.isPending}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          {isPaused ? (
            <>
              <Play className="w-4 h-4 mr-1.5" aria-hidden /> Resume
            </>
          ) : (
            <>
              <Pause className="w-4 h-4 mr-1.5" aria-hidden /> Pause
            </>
          )}
        </Button>
        <Button
          type="button"
          onClick={() => setConfirmOpen(true)}
          disabled={cancel.isPending}
          className={cn(
            'rounded-full h-11 text-[14px] font-semibold border',
            'bg-danger/10 hover:bg-danger/20 text-danger border-danger/40',
          )}
        >
          <X className="w-4 h-4 mr-1.5" aria-hidden /> Cancel
        </Button>
      </div>
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel this print?</AlertDialogTitle>
            <AlertDialogDescription>
              The printer will stop immediately. This can't be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={cancel.isPending}>Keep printing</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                // Stop the dialog from auto-closing — we close on success/error in the mutation.
                e.preventDefault();
                cancel.mutate();
              }}
              disabled={cancel.isPending}
              className="bg-danger text-white hover:bg-danger/90"
            >
              {cancel.isPending ? 'Cancelling…' : 'Cancel print'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function OpenPrintButton() {
  // The active printer is already in PrinterContext (set by the picker on
  // Dashboard); navigating to /print is enough — Phase 4 reads the same context.
  const navigate = useNavigate();
  return (
    <Button
      type="button"
      onClick={() => navigate('/print')}
      className="w-full rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11 text-[14px] font-semibold"
    >
      Open Print
    </Button>
  );
}
```

- [ ] **Step 5.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 5.3: Commit**

```bash
git add web/src/components/dashboard/control-buttons.tsx
git commit -m "Add ControlButtons (pause/resume + cancel | open print)"
```

---

## Task 6: Build `<SpeedSelect/>`

**Files:**
- Create: `web/src/components/dashboard/speed-select.tsx`
- Modify: `web/src/components/dashboard/stat-chips-row.tsx`

`<SpeedSelect/>` is a shadcn `<Select/>` styled to match the surrounding `<StatChip/>` chips visually (same eyebrow + value layout). The mutation pattern is **optimistic**: `onMutate` patches the `['printers']` cache so the UI updates immediately; `onError` rolls back; `onSettled` invalidates so the next poll reconciles.

- [ ] **Step 6.1: Create `web/src/components/dashboard/speed-select.tsx`**

```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ChevronDown } from 'lucide-react';
import { toast } from 'sonner';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select';
import { setPrinterSpeed } from '@/lib/api/printer-commands';
import type { PrinterListResponse, PrinterStatus, SpeedLevel } from '@/lib/api/types';
import { cn } from '@/lib/utils';

const SPEED_OPTIONS: { value: SpeedLevel; label: string }[] = [
  { value: 1, label: 'Silent' },
  { value: 2, label: 'Standard' },
  { value: 3, label: 'Sport' },
  { value: 4, label: 'Ludicrous' },
];

const VALID_LEVELS: ReadonlySet<number> = new Set([1, 2, 3, 4]);

function isSpeedLevel(v: number): v is SpeedLevel {
  return VALID_LEVELS.has(v);
}

export function SpeedSelect({ printer }: { printer: PrinterStatus }) {
  const queryClient = useQueryClient();
  const current = isSpeedLevel(printer.speed_level) ? printer.speed_level : null;
  const currentLabel =
    SPEED_OPTIONS.find((o) => o.value === current)?.label ?? '—';

  const mutation = useMutation({
    mutationFn: (level: SpeedLevel) => setPrinterSpeed(printer.id, level),
    onMutate: async (level) => {
      await queryClient.cancelQueries({ queryKey: ['printers'] });
      const prev = queryClient.getQueryData<PrinterListResponse>(['printers']);
      if (prev) {
        queryClient.setQueryData<PrinterListResponse>(['printers'], {
          printers: prev.printers.map((p) =>
            p.id === printer.id ? { ...p, speed_level: level } : p,
          ),
        });
      }
      return { prev };
    },
    onError: (err: Error, _level, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(['printers'], ctx.prev);
      toast.error(`Speed change failed: ${err.message}`);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
    },
  });

  const offline = !printer.online;
  const value = current != null ? String(current) : '';

  return (
    <div
      className={cn(
        'flex flex-col gap-1.5 p-3 rounded-xl bg-card text-left',
        offline && 'opacity-60',
      )}
    >
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
        Speed
      </span>
      <Select
        value={value}
        onValueChange={(v) => {
          const level = Number(v);
          if (isSpeedLevel(level)) mutation.mutate(level);
        }}
        disabled={offline || mutation.isPending}
      >
        <SelectTrigger
          aria-label="Print speed"
          className={cn(
            'h-auto p-0 border-0 bg-transparent text-accent font-mono tabular-nums',
            'focus:ring-0 focus:ring-offset-0',
          )}
        >
          <span className="flex items-baseline gap-1">
            <span className="text-[22px] font-bold leading-none">{currentLabel}</span>
            <ChevronDown className="w-3.5 h-3.5 text-text-2 self-center" aria-hidden />
          </span>
        </SelectTrigger>
        <SelectContent>
          {SPEED_OPTIONS.map((opt) => (
            <SelectItem key={opt.value} value={String(opt.value)}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
```

- [ ] **Step 6.2: Update `<StatChipsRow/>` to use `<SpeedSelect/>`**

Open `web/src/components/dashboard/stat-chips-row.tsx`. Replace the entire file with:

```typescript
import { StatChip } from '@/components/stat-chip';
import { SpeedSelect } from '@/components/dashboard/speed-select';
import { formatTemp } from '@/lib/format';
import type { PrinterStatus } from '@/lib/api/types';

export function StatChipsRow({ printer }: { printer: PrinterStatus }) {
  const t = printer.temperatures;

  return (
    <div className="grid grid-cols-3 gap-2.5">
      <StatChip
        label="Nozzle"
        value={formatTemp(t.nozzle_temp)}
        unit={`/${formatTemp(t.nozzle_target)}`}
        variant="warm"
      />
      <StatChip
        label="Bed"
        value={formatTemp(t.bed_temp)}
        unit={`/${formatTemp(t.bed_target)}`}
        variant="warm"
      />
      <SpeedSelect printer={printer} />
    </div>
  );
}
```

(The old `SPEED_LABELS` constant is removed from this file because `<SpeedSelect/>` owns it now.)

- [ ] **Step 6.3: Type-check + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0.

- [ ] **Step 6.4: Commit**

```bash
git add web/src/components/dashboard/speed-select.tsx web/src/components/dashboard/stat-chips-row.tsx
git commit -m "Add SpeedSelect with optimistic mutation, swap into StatChipsRow"
```

---

## Task 7: Build `<TraySheet/>`

**Files:**
- Create: `web/src/components/dashboard/tray-sheet.tsx`

The sheet renders for one (tray, unit) pair. It shows:

1. **Filament summary** at the top: color swatch, filament name (matched profile or sub-brand or "Unknown"), type (`tray.tray_type`), filament id, loaded grams (`tray.tray_weight` if non-empty).
2. **Drying section** — only if `unit.supports_drying === true`:
   - If `unit.dry_time_remaining > 0`: show "Drying — `formatRemaining(dry_time_remaining)` remaining" + a "Stop drying" button.
   - Otherwise: show two inputs (Temperature °C, Duration minutes) + "Start drying" button. Temperature defaults to `unit.max_drying_temp`, capped by it; Duration defaults to 480 (8 hours).
3. **No-drying note** when `unit.supports_drying === false`: small `text-2` line "This AMS doesn't support drying."

The external spool case: pass `unit = null`. The sheet still shows the filament summary but the entire drying section is replaced with the no-drying note (the external spool has no AMS unit and can never dry).

- [ ] **Step 7.1: Create `web/src/components/dashboard/tray-sheet.tsx`**

```typescript
import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Square } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { startDrying, stopDrying } from '@/lib/api/printer-commands';
import { normalizeTrayColor } from '@/lib/filament-color';
import { formatRemaining } from '@/lib/format';
import { useMediaQuery } from '@/lib/use-media-query';
import type { AMSTray, AMSUnit } from '@/lib/api/types';
import { cn } from '@/lib/utils';

export interface TraySheetSelection {
  tray: AMSTray;
  /** Null for the external spool (no AMS unit, never dries). */
  unit: AMSUnit | null;
  /** Header label (e.g. "Tray 3" or "External Spool"). */
  label: string;
}

export function TraySheet({
  printerId,
  selection,
  onClose,
}: {
  printerId: string;
  selection: TraySheetSelection | null;
  onClose: () => void;
}) {
  const isDesktop = useMediaQuery('(min-width: 640px)');
  const open = selection !== null;

  return (
    <Sheet open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent
        side={isDesktop ? 'right' : 'bottom'}
        className={cn(
          'bg-bg-1 border-border text-text-0 flex flex-col gap-5 overflow-y-auto',
          isDesktop ? 'w-[400px] sm:max-w-[400px]' : 'h-[80dvh]',
        )}
      >
        {selection && (
          <TraySheetBody
            printerId={printerId}
            tray={selection.tray}
            unit={selection.unit}
            label={selection.label}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

function TraySheetBody({
  printerId,
  tray,
  unit,
  label,
}: {
  printerId: string;
  tray: AMSTray;
  unit: AMSUnit | null;
  label: string;
}) {
  const color = normalizeTrayColor(tray.tray_color);
  const filamentName =
    tray.matched_filament?.name ||
    tray.tray_sub_brands ||
    'Unknown filament';
  const grams = tray.tray_weight?.trim();

  return (
    <>
      <SheetHeader className="text-left">
        <SheetTitle className="text-white">{label}</SheetTitle>
        <SheetDescription className="text-text-1">
          {tray.tray_type ? `${tray.tray_type}` : '—'}
          {tray.filament_id ? ` · ${tray.filament_id}` : ''}
        </SheetDescription>
      </SheetHeader>

      <section className="flex items-center gap-3">
        <span
          className={cn(
            'shrink-0 w-10 h-10 rounded-full',
            color == null && 'border border-dashed border-text-2',
          )}
          style={color ? { backgroundColor: color } : undefined}
          aria-hidden
        />
        <div className="flex flex-col gap-0.5 min-w-0">
          <div className="text-[15px] font-semibold text-white truncate">{filamentName}</div>
          {grams && (
            <div className="text-xs text-text-1 font-mono tabular-nums">
              {grams} g loaded
            </div>
          )}
        </div>
      </section>

      {unit?.supports_drying ? (
        <DryingControls printerId={printerId} unit={unit} />
      ) : (
        <p className="text-xs text-text-2">
          {unit
            ? "This AMS doesn't support drying."
            : 'Drying is only available for AMS-housed trays.'}
        </p>
      )}
    </>
  );
}

function DryingControls({ printerId, unit }: { printerId: string; unit: AMSUnit }) {
  const queryClient = useQueryClient();
  const drying = unit.dry_time_remaining > 0;

  const start = useMutation({
    mutationFn: ({ temp, dur }: { temp: number; dur: number }) =>
      startDrying(printerId, unit.id, { temperature: temp, durationMinutes: dur }),
    onSuccess: () => {
      toast.success('Drying started');
      queryClient.invalidateQueries({ queryKey: ['ams'] });
    },
    onError: (err: Error) => toast.error(`Start drying failed: ${err.message}`),
  });

  const stop = useMutation({
    mutationFn: () => stopDrying(printerId, unit.id),
    onSuccess: () => {
      toast.success('Drying stopped');
      queryClient.invalidateQueries({ queryKey: ['ams'] });
    },
    onError: (err: Error) => toast.error(`Stop drying failed: ${err.message}`),
  });

  if (drying) {
    return (
      <section className="flex flex-col gap-3">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
          Drying
        </div>
        <div className="flex items-center gap-2 text-text-0">
          <Loader2 className="w-4 h-4 text-accent animate-spin" aria-hidden />
          <span className="text-sm">
            {formatRemaining(unit.dry_time_remaining)} remaining
          </span>
        </div>
        <Button
          type="button"
          onClick={() => stop.mutate()}
          disabled={stop.isPending}
          className="rounded-full bg-danger/10 hover:bg-danger/20 text-danger border border-danger/40"
        >
          <Square className="w-4 h-4 mr-1.5" aria-hidden />
          {stop.isPending ? 'Stopping…' : 'Stop drying'}
        </Button>
      </section>
    );
  }

  return <DryingForm unit={unit} onSubmit={(p) => start.mutate(p)} pending={start.isPending} />;
}

function DryingForm({
  unit,
  onSubmit,
  pending,
}: {
  unit: AMSUnit;
  onSubmit: (p: { temp: number; dur: number }) => void;
  pending: boolean;
}) {
  const [temp, setTemp] = useState<number>(unit.max_drying_temp);
  const [dur, setDur] = useState<number>(480);

  // If the user opens the sheet for a different unit (different max temp),
  // re-clamp the temperature input.
  useEffect(() => {
    setTemp((t) => Math.min(t, unit.max_drying_temp));
  }, [unit.max_drying_temp]);

  const tempInvalid = !Number.isFinite(temp) || temp <= 0 || temp > unit.max_drying_temp;
  const durInvalid = !Number.isFinite(dur) || dur <= 0 || dur > 24 * 60;

  return (
    <section className="flex flex-col gap-3">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
        Start drying
      </div>
      <label className="flex flex-col gap-1 text-sm text-text-1">
        Temperature (°C)
        <input
          type="number"
          inputMode="numeric"
          min={1}
          max={unit.max_drying_temp}
          value={Number.isFinite(temp) ? temp : ''}
          onChange={(e) => setTemp(Number(e.target.value))}
          className={cn(
            'h-10 px-3 rounded-md bg-card border border-border text-text-0 font-mono tabular-nums',
            'focus:outline-none focus:ring-2 focus:ring-ring',
            tempInvalid && 'border-danger',
          )}
        />
        <span className="text-[11px] text-text-2">Max for this AMS: {unit.max_drying_temp}°C</span>
      </label>
      <label className="flex flex-col gap-1 text-sm text-text-1">
        Duration (minutes)
        <input
          type="number"
          inputMode="numeric"
          min={1}
          max={24 * 60}
          value={Number.isFinite(dur) ? dur : ''}
          onChange={(e) => setDur(Number(e.target.value))}
          className={cn(
            'h-10 px-3 rounded-md bg-card border border-border text-text-0 font-mono tabular-nums',
            'focus:outline-none focus:ring-2 focus:ring-ring',
            durInvalid && 'border-danger',
          )}
        />
        <span className="text-[11px] text-text-2">Default 480 (8 h); max 1440 (24 h).</span>
      </label>
      <Button
        type="button"
        onClick={() => onSubmit({ temp, dur })}
        disabled={pending || tempInvalid || durInvalid}
        className="rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11"
      >
        {pending ? 'Starting…' : 'Start drying'}
      </Button>
    </section>
  );
}
```

- [ ] **Step 7.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

If `noUnusedLocals` flags the `_level` parameter in `SpeedSelect`'s `onError` (Task 6) — already prefixed with `_`, should be fine. Same convention applies here.

- [ ] **Step 7.3: Commit**

```bash
git add web/src/components/dashboard/tray-sheet.tsx
git commit -m "Add TraySheet with filament summary and AMS drying controls"
```

---

## Task 8: Wire `<TraySheet/>` into `<AmsSection/>`

**Files:**
- Modify: `web/src/components/dashboard/ams-section.tsx`

Lift selected-tray state into `<AmsSection/>` and render a single `<TraySheet/>` instance. Each `<AmsTrayRow/>` gets an `onClick` callback that sets the selection. The `<AmsSection/>` props gain `printerId: string` so the sheet's mutations know which printer to target.

- [ ] **Step 8.1: Replace `web/src/components/dashboard/ams-section.tsx`**

Open the file and replace its entire contents with:

```typescript
import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { TrayRow } from '@/components/tray-row';
import { Badge } from '@/components/ui/badge';
import { TraySheet, type TraySheetSelection } from '@/components/dashboard/tray-sheet';
import { normalizeTrayColor } from '@/lib/filament-color';
import type { AMSResponse, AMSTray, AMSUnit } from '@/lib/api/types';

export function AmsSection({
  printerId,
  ams,
  activeTrayId,
}: {
  printerId: string;
  ams: AMSResponse;
  activeTrayId: number | null;
}) {
  const [selection, setSelection] = useState<TraySheetSelection | null>(null);

  if (ams.units.length === 0 && !ams.vt_tray) return null;

  return (
    <>
      <section className="flex flex-col gap-4">
        {ams.units.map((unit) => (
          <AmsUnitGroup
            key={unit.id}
            unit={unit}
            trays={ams.trays.filter((t) => t.ams_id === unit.id)}
            activeTrayId={activeTrayId}
            onSelectTray={(tray) =>
              setSelection({ tray, unit, label: `Tray ${tray.slot + 1}` })
            }
          />
        ))}
        {ams.vt_tray && (
          <ExternalSpoolGroup
            tray={ams.vt_tray}
            activeTrayId={activeTrayId}
            onSelect={(tray) =>
              setSelection({ tray, unit: null, label: 'External Spool' })
            }
          />
        )}
      </section>
      <TraySheet
        printerId={printerId}
        selection={selection}
        onClose={() => setSelection(null)}
      />
    </>
  );
}

function AmsUnitGroup({
  unit,
  trays,
  activeTrayId,
  onSelectTray,
}: {
  unit: AMSUnit;
  trays: AMSTray[];
  activeTrayId: number | null;
  onSelectTray: (tray: AMSTray) => void;
}) {
  const storageKey = `bg.ams-unit-${unit.id}.collapsed`;
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(storageKey) === '1';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, collapsed ? '1' : '0');
    } catch {
      // ignore
    }
  }, [collapsed, storageKey]);

  const sorted = [...trays].sort((a, b) => a.slot - b.slot);
  const headerLabel = `AMS ${unit.id + 1}`;

  return (
    <div className="flex flex-col gap-2">
      <header className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="flex items-center gap-1.5 text-base font-semibold text-white"
          aria-expanded={!collapsed}
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4 text-text-1" aria-hidden />
          ) : (
            <ChevronDown className="w-4 h-4 text-text-1" aria-hidden />
          )}
          {headerLabel}
        </button>
        {unit.humidity >= 0 && (
          <span className="text-xs text-text-1 font-mono tabular-nums">
            {unit.humidity}% RH
          </span>
        )}
      </header>
      {!collapsed && (
        <div className="flex flex-col gap-2">
          {sorted.map((tray) => (
            <AmsTrayRow
              key={`${tray.ams_id}-${tray.slot}`}
              tray={tray}
              activeTrayId={activeTrayId}
              onSelect={() => onSelectTray(tray)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ExternalSpoolGroup({
  tray,
  activeTrayId,
  onSelect,
}: {
  tray: AMSTray;
  activeTrayId: number | null;
  onSelect: (tray: AMSTray) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <header className="flex items-center justify-between">
        <span className="text-base font-semibold text-white">External Spool</span>
      </header>
      <AmsTrayRow tray={tray} activeTrayId={activeTrayId} onSelect={() => onSelect(tray)} />
    </div>
  );
}

function AmsTrayRow({
  tray,
  activeTrayId,
  onSelect,
}: {
  tray: AMSTray;
  activeTrayId: number | null;
  onSelect: () => void;
}) {
  const color = normalizeTrayColor(tray.tray_color);
  const isEmpty = color == null && !tray.tray_type;
  // `active_tray` from PrinterStatus is the global slot index (0..N for AMS
  // bays, 254 for the external spool), matching `tray.slot` set by the
  // gateway. Comparing against `tray_id` (per-AMS 0..3) silently breaks
  // multi-AMS setups because tray 0 of every unit would falsely match.
  const inUse = activeTrayId != null && activeTrayId === tray.slot;

  const subtitleParts: string[] = [];
  if (tray.tray_type) subtitleParts.push(tray.tray_type);
  if (tray.filament_id) subtitleParts.push(tray.filament_id);

  const filamentName =
    tray.matched_filament?.name ||
    tray.tray_sub_brands ||
    null;

  return (
    <TrayRow
      colorDot={color}
      title={`Tray ${tray.slot + 1}`}
      subtitle={subtitleParts.length > 0 ? subtitleParts.join(' · ') : undefined}
      body={
        isEmpty ? (
          <span className="italic text-text-2">Empty</span>
        ) : (
          filamentName ?? <span className="italic text-text-2">Unknown filament</span>
        )
      }
      right={inUse && <Badge className="bg-accent/15 text-accent border-transparent">In Use</Badge>}
      highlighted={inUse}
      onClick={onSelect}
    />
  );
}
```

- [ ] **Step 8.2: Update Dashboard route to pass `printerId`**

Open `web/src/routes/dashboard.tsx`. Find the `<AmsSection/>` JSX and add the `printerId={active.id}` prop:

```typescript
      {amsQuery.data && (
        <AmsSection
          printerId={active.id}
          ams={amsQuery.data}
          activeTrayId={active.active_tray}
        />
      )}
```

- [ ] **Step 8.3: Type-check + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0.

- [ ] **Step 8.4: Commit**

```bash
git add web/src/components/dashboard/ams-section.tsx web/src/routes/dashboard.tsx
git commit -m "Open TraySheet on AMS row tap, lift selection to AmsSection"
```

---

## Task 9: Wire `<ControlButtons/>` into the Dashboard route

**Files:**
- Modify: `web/src/routes/dashboard.tsx`

- [ ] **Step 9.1: Insert `<ControlButtons/>` between `<StatChipsRow/>` and `<AmsSection/>`**

Open `web/src/routes/dashboard.tsx`. Add the import:

```typescript
import { ControlButtons } from '@/components/dashboard/control-buttons';
```

Then in the main return, between `<StatChipsRow printer={active} />` and the `{amsQuery.data && …}` block, add:

```typescript
      <ControlButtons printer={active} />
```

The full JSX for the loaded state should now read:

```typescript
    <div className="flex flex-col gap-6">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-[28px] font-extrabold tracking-tight text-white">Dashboard</h1>
      </header>

      <PrinterPicker
        printers={printers}
        activeId={active.id}
        onChange={setActivePrinterId}
      />

      <HeroCard printer={active} />

      <StatChipsRow printer={active} />

      <ControlButtons printer={active} />

      {amsQuery.data && (
        <AmsSection
          printerId={active.id}
          ams={amsQuery.data}
          activeTrayId={active.active_tray}
        />
      )}
    </div>
```

- [ ] **Step 9.2: Type-check + build**

Run: `cd web && npm run lint && npm run build`
Expected: both exit 0. Note the bundle size — it'll grow by ~30 kB raw because `<Sheet/>`, `<Select/>`, and `<AlertDialog/>` add substantial Radix code.

- [ ] **Step 9.3: Commit**

```bash
git add web/src/routes/dashboard.tsx
git commit -m "Mount ControlButtons in Dashboard route below stat chips"
```

---

## Task 10: Manual smoke-test in the dev server

**Files:** none modified.

This is the only manual verification step. The exact behaviors to confirm:

- [ ] **Step 10.1: Start the FastAPI backend (if not already running)**

```bash
uvicorn app.main:app --reload --port 4844
```

Expected: `MQTT connected to <printer-ip>` log within ~5 seconds for each configured printer.

- [ ] **Step 10.2: Build the Phase 3 bundle and open `/beta`**

```bash
cd web && npm run build
```

Open `http://localhost:4844/beta`. The new bundle is served by FastAPI's existing `/beta` mount.

- [ ] **Step 10.3: Walk the control surfaces**

DevTools → Console + Network. Walk through:

1. **Picker drives AMS:** With multiple printers configured, switch between them. The AMS section should refetch and show the picked printer's trays. The Network tab should show `/api/ams?printer_id=…` URLs (not just `/api/ams`).
2. **Pause:** During an active print, click `Pause`. The button should become disabled briefly; a toast says "Pausing…"; within ~4 s the state badge flips to `PAUSED`.
3. **Resume:** With the print paused, click `Resume`. Toast "Resuming…"; state flips back to `PRINTING`.
4. **Cancel:** Click `Cancel`. The AlertDialog appears with "Cancel this print?" and "Keep printing" / "Cancel print" buttons. "Keep printing" closes the dialog with no network call. "Cancel print" disables both buttons, fires `POST /api/printers/{id}/cancel`, toast "Cancelling…", dialog closes on success, state moves to `CANCELLED` within a few seconds.
5. **Speed:** Click the Speed chip. The Select opens with Silent / Standard / Sport / Ludicrous. Picking a different option should immediately update the chip's label (optimistic), fire `POST /api/printers/{id}/speed`, and within ~4 s the next poll should confirm. Force a failure (e.g., kill MQTT briefly or set a stale printer id) — the chip should roll back to the previous label and a red toast surfaces the error.
6. **Open Print (idle):** When the printer is idle/finished, the action area shows a single "Open Print" button. Clicking routes to `/beta/print` (still the Phase 1 placeholder).
7. **Tray sheet (AMS):** On an AMS-equipped printer, tap any AMS tray row. A sheet slides in (right side on desktop ≥640px, bottom on phone). Header shows `Tray N` + type/filament-id; below, a circular swatch + filament name + grams loaded. For an AMS that supports drying (Pro / HT), the drying form is visible: temperature input pre-filled with `unit.max_drying_temp`, duration input pre-filled with `480`. Click Start Drying — toast "Drying started", sheet stays open, AMS query refetches, and on next render the sheet body switches to "Drying — Xh Ym remaining" + Stop drying button.
8. **Tray sheet (no drying):** On an AMS Lite or standard AMS, the drying section is replaced with `text-2 "This AMS doesn't support drying."`.
9. **External Spool sheet:** Tap the External Spool row. Sheet shows the filament summary; drying section reads `Drying is only available for AMS-housed trays.`.
10. **Polling continues:** All polls (`/api/printers`, `/api/ams?printer_id=…`) fire every 4 s. Console clean; no React warnings about missing keys, hydration mismatches, or stale state.

If any of the above fails, **do not commit Task 11** — fix the relevant component task and re-run.

---

## Task 11: README + final audit

**Files:**
- Modify: `README.md`

- [ ] **Step 11.1: Update `README.md`**

Find the Phase 2 line (added in commit `8d6f66e` under the `Frontend` section). Append below it:

```markdown
- **Phase 3:** `/beta` Dashboard now exposes pause/resume + cancel (with confirm), a `<Select/>`-driven Speed chip with optimistic update, and an AMS tray sheet with start/stop drying controls. The picker also drives `/api/ams` per printer.
```

- [ ] **Step 11.2: Commit**

```bash
git add README.md
git commit -m "README: document Phase 3 Dashboard controls + AMS drying"
```

- [ ] **Step 11.3: Final audit checklist**

Walk this list end-to-end before declaring Phase 3 done.

- [ ] `cd web && npm run lint` exits 0.
- [ ] `cd web && npm run build` exits 0 and writes `app/static/dist/index.html`.
- [ ] `pytest` exits 0 (the new `tests/test_ams_endpoint.py` passes alongside the existing suite).
- [ ] `git status` is clean.
- [ ] All 11 tasks above committed (`git log --oneline` shows ~12 new commits since the start of Phase 3, one per Step X.N "Commit").
- [ ] Old Jinja UI at `/` renders unchanged (`curl -s http://localhost:4844/ | grep '<title>'` still returns the Jinja title).
- [ ] Only `app/main.py` and the new test file were modified in `app/` (`git diff <phase3-base>..HEAD -- app/` is the route change + nothing else).

If all items check out, Phase 3 is complete. Phase 4 (Print flow) gets its own plan next.
