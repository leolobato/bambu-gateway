# Web UI Redesign — Phase 2: Dashboard (Read-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder Dashboard at `/beta` with a read-only printer dashboard that renders the printer picker, hero card, three stat chips, and the AMS section, sourcing data from the existing `/api/printers` and `/api/ams` endpoints. No control actions yet — pause/resume/cancel/speed and AMS drying are deferred to Phase 3.

**Architecture:** Vanilla React Query for the two existing read-only endpoints (4-second poll, matches the old Jinja UI cadence). A small typed API layer in `web/src/lib/api/` defines the response shapes (mirroring `app/models.py` Pydantic models) and the `fetchJson` helper. A new `PrinterContext` (React Context wrapping the active-printer-id selection from `localStorage`) is consumed by Dashboard and exposed via a hook so Phase 4's Print page can later reuse it. UI components live in `web/src/components/dashboard/` (page-specific) and `web/src/components/` (reusable: `<PrinterPicker/>`, `<StatChip/>`, `<TrayRow/>`, `<StateBadge/>`).

**Tech Stack:** Already installed in Phase 1 — React 18, TypeScript 5, Vite 5, Tailwind CSS 3, shadcn/ui (Button, Sonner), `@tanstack/react-query` v5, `react-router-dom` v6, `lucide-react` 0.460. New shadcn primitives this phase installs: `card`, `badge`, `progress`, `skeleton`, `separator`, `tooltip`, `command`, `popover`. No backend changes.

**Spec reference:** `docs/superpowers/specs/2026-04-23-webui-redesign-design.md` — sections "App shell" (already done), "Dashboard" (this phase, minus the controls listed in the "Action buttons" subsection — those are Phase 3), and "Responsive behavior".

**Known gap (deliberately deferred):** The current `/api/ams` endpoint only returns the **default** printer's AMS — there is no `?printer_id=` parameter. When the picker selects a non-default printer, the AMS section will hide itself and show a small `text-2` note. A backend follow-up (`GET /api/ams?printer_id=…`) is the correct fix and will be scoped during Phase 3 prep, per the spec's policy ("Backend API changes … if a gap appears … scoped as a discrete follow-up").

---

## File structure

**Created in this plan:**

```
web/
└── src/
    ├── lib/
    │   ├── api/
    │   │   ├── client.ts           # fetchJson<T>() helper, throws ApiError
    │   │   ├── printers.ts         # GET /api/printers, GET /api/printers/{id}
    │   │   ├── ams.ts              # GET /api/ams
    │   │   └── types.ts            # TypeScript mirrors of app/models.py
    │   ├── printer-context.tsx     # PrinterProvider + usePrinter() + useActivePrinterId()
    │   ├── filament-color.ts       # normalizeTrayColor("RRGGBBAA" | "" → "#RRGGBB" | null)
    │   └── format.ts               # formatRemaining(min), formatTemperature(c, target)
    ├── components/
    │   ├── ui/
    │   │   ├── card.tsx            # shadcn primitive
    │   │   ├── badge.tsx           # shadcn primitive
    │   │   ├── progress.tsx        # shadcn primitive
    │   │   ├── skeleton.tsx        # shadcn primitive
    │   │   ├── separator.tsx       # shadcn primitive
    │   │   ├── tooltip.tsx         # shadcn primitive
    │   │   ├── command.tsx         # shadcn primitive
    │   │   └── popover.tsx         # shadcn primitive
    │   ├── printer-picker.tsx      # segmented pill (≤3) / Command popover (≥4)
    │   ├── stat-chip.tsx           # eyebrow + bold value with variants
    │   ├── tray-row.tsx            # shared row primitive (Dashboard AMS, later Print/Settings)
    │   ├── state-badge.tsx         # colored pill mapping PrinterState → variant
    │   └── dashboard/
    │       ├── hero-card.tsx       # state badge, %, meta line, progress, idle/offline/error variants
    │       ├── stat-chips-row.tsx  # 3-up grid of <StatChip/> for nozzle, bed, speed
    │       └── ams-section.tsx     # AMS units + External Spool + tray rows
```

**Modified in this plan:**

- `web/src/main.tsx` — wrap `<App/>` with `<PrinterProvider/>` (or do this inside `App.tsx`; this plan puts it in `App.tsx` so `main.tsx` stays minimal — see Task 3).
- `web/src/App.tsx` — wrap `<RouterProvider/>` in `<PrinterProvider/>` so the Dashboard route can read/write the active printer id.
- `web/src/routes/dashboard.tsx` — replace placeholder with the real composition.
- `web/components.json` — unchanged (shadcn CLI uses it).
- `web/package.json` / `package-lock.json` — only via `npx shadcn@latest add …` and any peer-dep installs the CLI prompts for.

**Untouched in this plan:**

- All `app/*` Python files. Spec mandates no backend changes.
- `web/src/components/app-shell.tsx`, `web/tailwind.config.ts`, `web/vite.config.ts`, `web/src/index.css`.
- Phase 1's `Button`/`Sonner` shadcn primitives.
- Old Jinja UI at `/` and `/settings`.

## Prerequisites

- Phase 1 must be on `main` (commit `ae5d56d` or later — the `/beta` mount and Dockerfile multi-stage build).
- Local FastAPI dev server running on `http://localhost:4844` against at least one configured printer (so `/api/printers` and `/api/ams` return live data). Without a real printer the dashboard will render "Offline" — that's a valid test path but most of this plan assumes at least one printer reports status.
- `cd web && npm install` already done. `node -v` ≥ 20.

---

## Task 1: Add typed API layer and fetch client

**Files:**
- Create: `web/src/lib/api/client.ts`, `web/src/lib/api/types.ts`, `web/src/lib/api/printers.ts`, `web/src/lib/api/ams.ts`

- [ ] **Step 1.1: Create `web/src/lib/api/types.ts`**

This file mirrors the Pydantic models from `app/models.py`. Keep field names identical — no camelCase conversion. The whole point is that a `fetch().json()` cast lands cleanly.

```typescript
// Mirrors app/models.py — keep field names in sync with the backend.
// No camelCase conversion: we cast JSON straight onto these types.

export type PrinterState =
  | 'offline'
  | 'idle'
  | 'preparing'
  | 'printing'
  | 'paused'
  | 'finished'
  | 'cancelled'
  | 'error';

export type SpeedLevel = 1 | 2 | 3 | 4; // silent / standard / sport / ludicrous

export type AMSTypeName = 'standard' | 'lite' | 'pro' | 'ht';

export interface TemperatureInfo {
  nozzle_temp: number;
  nozzle_target: number;
  bed_temp: number;
  bed_target: number;
}

export interface PrintJob {
  file_name: string;
  progress: number;
  remaining_minutes: number;
  current_layer: number;
  total_layers: number;
}

export interface HMSCode {
  attr: string;
  code: string;
}

export interface ChamberLightInfo {
  supported: boolean;
  on: boolean | null;
}

export interface CameraInfo {
  ip: string;
  access_code: string;
  transport: string;
  chamber_light: ChamberLightInfo | null;
}

export interface PrinterStatus {
  id: string;
  name: string;
  machine_model: string;
  online: boolean;
  state: PrinterState;
  stg_cur: number;
  stage_name: string | null;
  stage_category: string | null;
  speed_level: number;          // 0 = unknown; otherwise SpeedLevel
  active_tray: number | null;
  temperatures: TemperatureInfo;
  job: PrintJob | null;
  hms_codes: HMSCode[];
  print_error: number;
  error_message: string | null;
  camera: CameraInfo | null;
}

export interface PrinterListResponse {
  printers: PrinterStatus[];
}

export interface SlicerFilament {
  name: string;
  filament_id: string;
  setting_id: string;
}

export interface AMSTray {
  slot: number;
  ams_id: number;
  tray_id: number;
  tray_type: string;
  tray_color: string;            // "RRGGBBAA" or ""
  tray_sub_brands: string;
  filament_id: string;
  tray_uuid: string;
  tag_uid: string;
  nozzle_temp_min: string;
  nozzle_temp_max: string;
  bed_temp: string;
  remain: number;                // -1 if unknown
  tray_weight: string;
  matched_filament: SlicerFilament | null;
}

export interface AMSUnit {
  id: number;
  humidity: number;              // -1 if unknown
  temperature: number;
  tray_count: number;
  hw_version: string;
  ams_type: AMSTypeName | null;
  supports_drying: boolean;
  max_drying_temp: number;
  dry_time_remaining: number;
}

export interface AMSResponse {
  printer_id: string;
  trays: AMSTray[];
  units: AMSUnit[];
  vt_tray: AMSTray | null;
}
```

- [ ] **Step 1.2: Create `web/src/lib/api/client.ts`**

```typescript
export class ApiError extends Error {
  constructor(public status: number, public detail: string, message?: string) {
    super(message ?? `API ${status}: ${detail}`);
    this.name = 'ApiError';
  }
}

export async function fetchJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // body wasn't JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}
```

- [ ] **Step 1.3: Create `web/src/lib/api/printers.ts`**

```typescript
import { fetchJson } from './client';
import type { PrinterListResponse } from './types';

export async function listPrinters(): Promise<PrinterListResponse> {
  return fetchJson<PrinterListResponse>('/api/printers');
}
```

- [ ] **Step 1.4: Create `web/src/lib/api/ams.ts`**

```typescript
import { fetchJson } from './client';
import type { AMSResponse } from './types';

// NOTE: backend currently has no ?printer_id= param — this returns the
// *default* printer's AMS only. The Dashboard hides AMS when the active
// printer differs from `response.printer_id`. A backend follow-up will
// add the param.
export async function getAms(): Promise<AMSResponse> {
  return fetchJson<AMSResponse>('/api/ams');
}
```

- [ ] **Step 1.5: Type-check**

Run: `cd web && npm run lint`
Expected: exits 0 with no output (the `lint` script is `tsc --noEmit`).

- [ ] **Step 1.6: Commit**

```bash
git add web/src/lib/api/
git commit -m "Add typed API layer for /api/printers and /api/ams"
```

---

## Task 2: Add filament-color and format helpers

**Files:**
- Create: `web/src/lib/filament-color.ts`, `web/src/lib/format.ts`

- [ ] **Step 2.1: Create `web/src/lib/filament-color.ts`**

The printer reports tray colors as `"RRGGBBAA"` strings (no `#`, alpha included). Empty / `"00000000"` mean "no filament" — return `null` so the UI can render an empty swatch outline. Strip the alpha channel: shadcn `<Sheet/>` and color dots assume `#RRGGBB`.

```typescript
/**
 * Bambu MQTT reports tray_color as 8-hex "RRGGBBAA" (alpha included) or "".
 * Empty / fully-transparent / "00000000" means no filament loaded.
 * Returns "#RRGGBB" (alpha stripped) or null when there's no usable color.
 */
export function normalizeTrayColor(raw: string): string | null {
  if (!raw) return null;
  const hex = raw.trim().replace(/^#/, '');
  if (hex.length < 6) return null;
  if (/^0+$/.test(hex)) return null; // all zeros = empty slot
  // Validate hex chars
  if (!/^[0-9a-fA-F]+$/.test(hex)) return null;
  return `#${hex.slice(0, 6).toUpperCase()}`;
}
```

- [ ] **Step 2.2: Create `web/src/lib/format.ts`**

```typescript
/** "1h 23m" / "23m" / "—" for ≤0 or NaN. */
export function formatRemaining(minutes: number | null | undefined): string {
  if (minutes == null || !Number.isFinite(minutes) || minutes <= 0) return '—';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

/**
 * "24°/0°" — current with target as a `text-2` suffix.
 * The component is responsible for splitting current/suffix so it can style
 * the target portion separately. This helper just rounds.
 */
export function formatTemp(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return `${Math.round(value)}°`;
}
```

- [ ] **Step 2.3: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 2.4: Commit**

```bash
git add web/src/lib/filament-color.ts web/src/lib/format.ts
git commit -m "Add filament-color and time/temperature formatting helpers"
```

---

## Task 3: Add `PrinterContext` (active printer id, persisted in localStorage)

**Files:**
- Create: `web/src/lib/printer-context.tsx`
- Modify: `web/src/App.tsx`

- [ ] **Step 3.1: Create `web/src/lib/printer-context.tsx`**

```typescript
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

const STORAGE_KEY = 'bg.active-printer-id';

type Ctx = {
  /** Active printer id, or null until the user has chosen one. */
  activePrinterId: string | null;
  setActivePrinterId: (id: string | null) => void;
};

const PrinterContext = createContext<Ctx | undefined>(undefined);

export function PrinterProvider({ children }: { children: React.ReactNode }) {
  // Lazy initializer reads localStorage exactly once on mount.
  const [activePrinterId, setActivePrinterIdState] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  });

  const setActivePrinterId = useCallback((id: string | null) => {
    setActivePrinterIdState(id);
    try {
      if (id == null) window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // Ignore quota / disabled-storage errors — selection just won't persist.
    }
  }, []);

  // Sync across browser tabs so opening Dashboard in two tabs stays consistent.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === STORAGE_KEY) setActivePrinterIdState(e.newValue);
    }
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const value = useMemo<Ctx>(
    () => ({ activePrinterId, setActivePrinterId }),
    [activePrinterId, setActivePrinterId],
  );

  return <PrinterContext.Provider value={value}>{children}</PrinterContext.Provider>;
}

export function usePrinterContext(): Ctx {
  const ctx = useContext(PrinterContext);
  if (ctx === undefined) {
    throw new Error('usePrinterContext must be used within <PrinterProvider/>');
  }
  return ctx;
}
```

- [ ] **Step 3.2: Wrap router with `<PrinterProvider/>` in `App.tsx`**

Edit `web/src/App.tsx`. Add the import and wrap `<RouterProvider/>`:

```typescript
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from '@/components/app-shell';
import DashboardRoute from '@/routes/dashboard';
import PrintRoute from '@/routes/print';
import SettingsRoute from '@/routes/settings';
import { Toaster } from '@/components/ui/sonner';
import { PrinterProvider } from '@/lib/printer-context';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 4_000,
      refetchOnWindowFocus: true,
    },
  },
});

const router = createBrowserRouter(
  [
    {
      path: '/',
      element: <AppShell />,
      children: [
        { index: true, element: <DashboardRoute /> },
        { path: 'print', element: <PrintRoute /> },
        { path: 'settings', element: <SettingsRoute /> },
      ],
    },
  ],
  { basename: '/beta' },
);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <PrinterProvider>
        <RouterProvider router={router} />
        <Toaster />
      </PrinterProvider>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 3.3: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 3.4: Commit**

```bash
git add web/src/lib/printer-context.tsx web/src/App.tsx
git commit -m "Add PrinterContext for active printer selection persisted in localStorage"
```

---

## Task 4: Install shadcn primitives needed by Phase 2

**Files:**
- Create (via shadcn CLI): `web/src/components/ui/card.tsx`, `badge.tsx`, `progress.tsx`, `skeleton.tsx`, `separator.tsx`, `tooltip.tsx`, `command.tsx`, `popover.tsx`
- Modify: `web/package.json`, `web/package-lock.json` (peer deps the CLI installs: `@radix-ui/react-progress`, `@radix-ui/react-separator`, `@radix-ui/react-tooltip`, `@radix-ui/react-popover`, `@radix-ui/react-dialog` — `command` depends on it via `cmdk`'s wrapper, `cmdk`)

- [ ] **Step 4.1: Add the eight primitives in one CLI run**

Run from the repo root:

```bash
cd web && npx shadcn@latest add card badge progress skeleton separator tooltip command popover --yes --overwrite
```

The CLI will:
1. Print each component it's adding.
2. Detect the missing peer deps and run `npm install` for them.
3. Write each component into `web/src/components/ui/`.

Expected new files: `web/src/components/ui/{card,badge,progress,skeleton,separator,tooltip,command,popover}.tsx`.
Expected new dependencies in `web/package.json`: at least `cmdk`, `@radix-ui/react-popover`, `@radix-ui/react-progress`, `@radix-ui/react-separator`, `@radix-ui/react-tooltip`, `@radix-ui/react-dialog`.

If the CLI prompts about React 19 / dependency conflicts, accept the defaults — Phase 1 already pinned React 18.3 and shadcn supports it.

- [ ] **Step 4.2: Verify the primitives compile**

Run: `cd web && npm run lint`
Expected: exit 0. If shadcn drops in any reference to a `cn` import that doesn't match `@/lib/utils`, fix the import path — but `components.json` already has `"utils": "@/lib/utils"`, so this should not happen.

- [ ] **Step 4.3: Verify production build still succeeds**

Run: `cd web && npm run build`
Expected: `vite build` reports the new modules in the bundle output, exits 0, and writes `app/static/dist/`.

- [ ] **Step 4.4: Commit**

```bash
git add web/components.json web/package.json web/package-lock.json web/src/components/ui/
git commit -m "Install shadcn card/badge/progress/skeleton/separator/tooltip/command/popover"
```

---

## Task 5: Build `<StateBadge/>`

**Files:**
- Create: `web/src/components/state-badge.tsx`

The state colors come from the spec ("Hero card" subsection):

| state | bg / text token | label |
|---|---|---|
| `printing` | `bg-accent/15 text-accent` | "Printing" |
| `paused` | `bg-info/15 text-info` | "Paused" |
| `preparing` | `bg-warm/15 text-warm` | "Preparing" |
| `error` | `bg-danger/15 text-danger` | "Error" |
| `idle` | `bg-success/15 text-success` | "Idle" |
| `finished` | `bg-success/15 text-success` | "Finished" |
| `cancelled` | `bg-text-2/15 text-text-1` | "Cancelled" |
| `offline` | `bg-text-2/15 text-text-2` | "Offline" |

- [ ] **Step 5.1: Create `web/src/components/state-badge.tsx`**

```typescript
import type { PrinterState } from '@/lib/api/types';
import { cn } from '@/lib/utils';

const VARIANTS: Record<PrinterState, { className: string; label: string }> = {
  printing:  { className: 'bg-accent/15 text-accent',     label: 'Printing'  },
  paused:    { className: 'bg-info/15 text-info',         label: 'Paused'    },
  preparing: { className: 'bg-warm/15 text-warm',         label: 'Preparing' },
  error:     { className: 'bg-danger/15 text-danger',     label: 'Error'     },
  idle:      { className: 'bg-success/15 text-success',   label: 'Idle'      },
  finished:  { className: 'bg-success/15 text-success',   label: 'Finished'  },
  cancelled: { className: 'bg-text-2/15 text-text-1',     label: 'Cancelled' },
  offline:   { className: 'bg-text-2/15 text-text-2',     label: 'Offline'   },
};

export function StateBadge({ state }: { state: PrinterState }) {
  const { className, label } = VARIANTS[state];
  return (
    <span
      className={cn(
        'inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-semibold uppercase tracking-wider',
        className,
      )}
    >
      {label}
    </span>
  );
}
```

- [ ] **Step 5.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 5.3: Commit**

```bash
git add web/src/components/state-badge.tsx
git commit -m "Add StateBadge mapping PrinterState to colored pill"
```

---

## Task 6: Build `<PrinterPicker/>`

**Files:**
- Create: `web/src/components/printer-picker.tsx`

The spec specifies two modes (≤3 printers = segmented pill, ≥4 = single pill + `<Command/>`). Both modes share the same dot-color logic and `onChange` contract.

Status-dot color rules (from spec "Printer picker"):
- `online && printing` → `accent`
- `online && idle` (or `finished` / `cancelled`) → `success`
- `online && paused` → `warm`
- `online && error` → `danger`
- `!online` → `text-2`

- [ ] **Step 6.1: Create `web/src/components/printer-picker.tsx`**

```typescript
import { useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import type { PrinterStatus } from '@/lib/api/types';
import { cn } from '@/lib/utils';

function dotColorClass(p: PrinterStatus): string {
  if (!p.online) return 'bg-text-2';
  switch (p.state) {
    case 'printing':
    case 'preparing':
      return 'bg-accent';
    case 'paused':
      return 'bg-warm';
    case 'error':
      return 'bg-danger';
    default:
      return 'bg-success';
  }
}

export function PrinterPicker({
  printers,
  activeId,
  onChange,
}: {
  printers: PrinterStatus[];
  activeId: string | null;
  onChange: (id: string) => void;
}) {
  if (printers.length === 0) return null;
  if (printers.length <= 3) return <SegmentedPicker printers={printers} activeId={activeId} onChange={onChange} />;
  return <CommandPicker printers={printers} activeId={activeId} onChange={onChange} />;
}

function SegmentedPicker({
  printers,
  activeId,
  onChange,
}: {
  printers: PrinterStatus[];
  activeId: string | null;
  onChange: (id: string) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Active printer"
      className="flex gap-1 overflow-x-auto bg-bg-1 border border-border rounded-full p-[3px]"
    >
      {printers.map((p) => {
        const active = p.id === activeId;
        return (
          <button
            key={p.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(p.id)}
            className={cn(
              'flex items-center gap-2 px-3.5 py-2 rounded-full text-[13px] font-medium whitespace-nowrap transition-colors duration-fast',
              active ? 'bg-surface-3 text-white' : 'text-text-1 hover:text-white',
            )}
          >
            <span className={cn('w-1.5 h-1.5 rounded-full', dotColorClass(p))} aria-hidden />
            {p.name}
          </button>
        );
      })}
    </div>
  );
}

function CommandPicker({
  printers,
  activeId,
  onChange,
}: {
  printers: PrinterStatus[];
  activeId: string | null;
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const active = printers.find((p) => p.id === activeId) ?? printers[0];

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Active printer: ${active.name}. Click to change.`}
          className="flex items-center gap-2 px-3.5 py-2 rounded-full bg-bg-1 border border-border text-[13px] font-medium text-white"
        >
          <span className={cn('w-1.5 h-1.5 rounded-full', dotColorClass(active))} aria-hidden />
          {active.name}
          <ChevronDown className="w-3.5 h-3.5 text-text-1" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[280px] p-0 bg-bg-1 border-border">
        <Command className="bg-transparent">
          <CommandInput placeholder="Search printers…" />
          <CommandList>
            <CommandEmpty>No printers found.</CommandEmpty>
            <CommandGroup>
              {printers.map((p) => {
                const isActive = p.id === active.id;
                return (
                  <CommandItem
                    key={p.id}
                    value={`${p.name} ${p.id}`}
                    onSelect={() => {
                      onChange(p.id);
                      setOpen(false);
                    }}
                    className="flex items-center gap-2"
                  >
                    <span className={cn('w-1.5 h-1.5 rounded-full', dotColorClass(p))} aria-hidden />
                    <span className="flex-1">{p.name}</span>
                    {isActive && <Check className="w-4 h-4 text-accent" aria-hidden />}
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
```

- [ ] **Step 6.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 6.3: Commit**

```bash
git add web/src/components/printer-picker.tsx
git commit -m "Add PrinterPicker (segmented pill ≤3 / Command popover ≥4)"
```

---

## Task 7: Build `<StatChip/>`

**Files:**
- Create: `web/src/components/stat-chip.tsx`

- [ ] **Step 7.1: Create `web/src/components/stat-chip.tsx`**

```typescript
import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

export type StatChipVariant = 'warm' | 'accent' | 'neutral';

const VALUE_COLOR: Record<StatChipVariant, string> = {
  warm: 'text-warm-hot',
  accent: 'text-accent',
  neutral: 'text-white',
};

export function StatChip({
  label,
  value,
  unit,
  variant = 'neutral',
  chevron = false,
  onClick,
}: {
  label: string;
  /** Pre-formatted main value (e.g. "24°", "Sport"). Rendered in tabular-nums. */
  value: string;
  /** Smaller suffix in `text-2` next to the value (e.g. "/0°"). */
  unit?: string;
  variant?: StatChipVariant;
  chevron?: boolean;
  onClick?: () => void;
}) {
  const Component = onClick ? 'button' : 'div';
  return (
    <Component
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className={cn(
        'flex flex-col gap-1.5 p-3 rounded-xl bg-card text-left transition-colors duration-fast',
        onClick && 'hover:bg-surface-2',
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
          {label}
        </span>
        {chevron && <ChevronRight className="w-3.5 h-3.5 text-text-2" aria-hidden />}
      </div>
      <div className={cn('flex items-baseline gap-1 font-mono tabular-nums', VALUE_COLOR[variant])}>
        <span className="text-[22px] font-bold leading-none">{value}</span>
        {unit && <span className="text-[13px] font-normal text-text-2">{unit}</span>}
      </div>
    </Component>
  );
}
```

- [ ] **Step 7.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 7.3: Commit**

```bash
git add web/src/components/stat-chip.tsx
git commit -m "Add StatChip with warm/accent/neutral variants"
```

---

## Task 8: Build `<TrayRow/>`

**Files:**
- Create: `web/src/components/tray-row.tsx`

This is the shared row primitive. AMS uses it in Phase 2; Print and Settings reuse it in later phases. Keep the API minimal: `colorDot`, `title`, `subtitle?`, `body?`, `right`, `onClick?`.

- [ ] **Step 8.1: Create `web/src/components/tray-row.tsx`**

```typescript
import { cn } from '@/lib/utils';

export function TrayRow({
  colorDot,
  title,
  subtitle,
  body,
  right,
  highlighted = false,
  onClick,
}: {
  /** Hex color string ("#RRGGBB") or null → outlined empty swatch. */
  colorDot: string | null;
  title: React.ReactNode;
  /** Mono eyebrow line (e.g. "PLA · GFA00"). */
  subtitle?: React.ReactNode;
  /** Body line (filament name, "Empty", etc.). */
  body?: React.ReactNode;
  /** Right-aligned content (chevron, badge, etc.). */
  right?: React.ReactNode;
  /** "In Use" highlight: 1px accent border + accent ring. */
  highlighted?: boolean;
  onClick?: () => void;
}) {
  const Component = onClick ? 'button' : 'div';
  return (
    <Component
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className={cn(
        'flex items-center gap-3 w-full p-3 rounded-2xl bg-card text-left transition-colors duration-fast',
        onClick && 'hover:bg-surface-2',
        highlighted && 'border border-accent shadow-[0_0_0_3px_rgba(96,165,250,0.18)]',
        !highlighted && 'border border-transparent',
      )}
    >
      <span
        className={cn(
          'shrink-0 w-7 h-7 rounded-full',
          colorDot == null && 'border border-dashed border-text-2',
        )}
        style={colorDot ? { backgroundColor: colorDot } : undefined}
        aria-hidden
      />
      <div className="flex-1 min-w-0 flex flex-col gap-0.5">
        <div className="text-[14px] font-semibold text-white truncate">{title}</div>
        {subtitle && (
          <div className="text-[11px] font-mono text-text-1 truncate">{subtitle}</div>
        )}
        {body && <div className="text-[13px] text-text-0 truncate">{body}</div>}
      </div>
      {right && <div className="shrink-0 flex items-center text-text-1">{right}</div>}
    </Component>
  );
}
```

- [ ] **Step 8.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 8.3: Commit**

```bash
git add web/src/components/tray-row.tsx
git commit -m "Add TrayRow shared list-row primitive"
```

---

## Task 9: Build `<HeroCard/>`

**Files:**
- Create: `web/src/components/dashboard/hero-card.tsx`

Per the spec, the hero card has five visual variants:

| Condition | Variant |
|---|---|
| `!online` | **Offline** — dim to 60%, "Offline · Check connection", link to Settings |
| `state === 'error'` | **Error** — red badge + separate red banner with `error_message` |
| `state` ∈ `printing` / `preparing` / `paused` and `job` exists | **Active** — % + meta line + progress |
| `state === 'idle'` (or `finished` / `cancelled`) | **Idle** — "Ready" + last finished file in meta line if `job` exists |
| anything else with no `job` | **Idle** fallback |

- [ ] **Step 9.1: Create `web/src/components/dashboard/hero-card.tsx`**

```typescript
import { CheckCircle2 } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { StateBadge } from '@/components/state-badge';
import { formatRemaining } from '@/lib/format';
import type { PrinterStatus } from '@/lib/api/types';
import { cn } from '@/lib/utils';

export function HeroCard({ printer }: { printer: PrinterStatus }) {
  if (!printer.online) return <OfflineHero name={printer.name} />;
  if (printer.state === 'error') return <ErrorHero printer={printer} />;
  if (
    (printer.state === 'printing' || printer.state === 'preparing' || printer.state === 'paused') &&
    printer.job
  ) {
    return <ActiveHero printer={printer} />;
  }
  return <IdleHero printer={printer} />;
}

function ActiveHero({ printer }: { printer: PrinterStatus }) {
  const job = printer.job!;
  const progress = Math.max(0, Math.min(100, job.progress));
  return (
    <Card className="p-5 bg-card border-border flex flex-col gap-4">
      <StateBadge state={printer.state} />
      <div className="text-[40px] sm:text-[48px] font-extrabold tracking-[-0.03em] font-mono tabular-nums text-white leading-none">
        {progress}%
      </div>
      <MetaLine job={job} stage={printer.stage_name} />
      <Progress
        value={progress}
        className="h-1.5 bg-bg-1 [&>[data-state=indeterminate]]:hidden [&>div]:bg-gradient-to-r [&>div]:from-accent-strong [&>div]:to-accent"
      />
    </Card>
  );
}

function MetaLine({ job, stage }: { job: PrinterStatus['job']; stage: string | null }) {
  if (!job) return null;
  const parts: React.ReactNode[] = [
    <span key="file" className="text-text-0 truncate" title={job.file_name}>
      {job.file_name || '—'}
    </span>,
  ];
  if (stage) {
    parts.push(<span key="stage" className="text-text-0">{stage}</span>);
  } else if (job.total_layers > 0) {
    parts.push(
      <span key="layer" className="text-text-0 font-mono tabular-nums">
        Layer {job.current_layer}/{job.total_layers}
      </span>,
    );
  }
  parts.push(
    <span key="rem" className="text-text-0 font-mono tabular-nums">
      {formatRemaining(job.remaining_minutes)} left
    </span>,
  );

  return (
    <div className="flex flex-wrap items-center gap-x-2 text-sm text-text-1">
      {parts.map((node, i) => (
        <span key={i} className="flex items-center gap-2">
          {i > 0 && <span aria-hidden>·</span>}
          {node}
        </span>
      ))}
    </div>
  );
}

function IdleHero({ printer }: { printer: PrinterStatus }) {
  const lastFile = printer.job?.file_name || '';
  return (
    <Card className="p-5 bg-card border-border flex flex-col gap-4">
      <StateBadge state={printer.state} />
      <div className="flex items-center gap-3 text-white">
        <CheckCircle2 className="w-7 h-7 text-success" aria-hidden />
        <div className="text-[28px] font-extrabold tracking-tight">Ready</div>
      </div>
      {lastFile && (
        <div className="text-sm text-text-1 truncate" title={lastFile}>
          Last: <span className="text-text-0">{lastFile}</span>
        </div>
      )}
    </Card>
  );
}

function OfflineHero({ name }: { name: string }) {
  return (
    <Card className="p-5 bg-card border-border opacity-60 flex flex-col gap-3">
      <StateBadge state="offline" />
      <div className="text-[22px] font-bold text-white">Offline · Check connection</div>
      <div className="text-sm text-text-1">
        <a href="/beta/settings" className="text-accent hover:underline">
          Open Settings to verify {name}'s IP and access code →
        </a>
      </div>
    </Card>
  );
}

function ErrorHero({ printer }: { printer: PrinterStatus }) {
  const msg = printer.error_message || 'The printer reported an error.';
  return (
    <div className="flex flex-col gap-3">
      <Card className="p-5 bg-card border-border flex flex-col gap-3">
        <StateBadge state="error" />
        <div className="text-[22px] font-bold text-white">Error</div>
      </Card>
      <Card
        className={cn(
          'p-4 bg-card border border-danger/40 flex items-start gap-3',
        )}
      >
        <div className="w-1 self-stretch rounded-full bg-danger" aria-hidden />
        <div className="flex-1 text-sm text-text-0 break-words">{msg}</div>
      </Card>
    </div>
  );
}
```

- [ ] **Step 9.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 9.3: Commit**

```bash
git add web/src/components/dashboard/hero-card.tsx
git commit -m "Add HeroCard with active / idle / offline / error variants"
```

---

## Task 10: Build `<StatChipsRow/>`

**Files:**
- Create: `web/src/components/dashboard/stat-chips-row.tsx`

The Speed chip in this phase is **read-only** — it renders the current speed level as text. Phase 3 will swap it for a `<Select/>` trigger.

Speed-level labels (from `app/models.py` `SpeedLevel`):
- `1` → Silent
- `2` → Standard
- `3` → Sport
- `4` → Ludicrous
- `0` (unknown) → "—"

- [ ] **Step 10.1: Create `web/src/components/dashboard/stat-chips-row.tsx`**

```typescript
import { StatChip } from '@/components/stat-chip';
import { formatTemp } from '@/lib/format';
import type { PrinterStatus } from '@/lib/api/types';

const SPEED_LABELS: Record<number, string> = {
  1: 'Silent',
  2: 'Standard',
  3: 'Sport',
  4: 'Ludicrous',
};

export function StatChipsRow({ printer }: { printer: PrinterStatus }) {
  const t = printer.temperatures;
  const speedLabel = SPEED_LABELS[printer.speed_level] ?? '—';

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
      <StatChip label="Speed" value={speedLabel} variant="accent" />
    </div>
  );
}
```

- [ ] **Step 10.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 10.3: Commit**

```bash
git add web/src/components/dashboard/stat-chips-row.tsx
git commit -m "Add StatChipsRow (nozzle / bed / speed) for Dashboard"
```

---

## Task 11: Build `<AmsSection/>`

**Files:**
- Create: `web/src/components/dashboard/ams-section.tsx`

Behaviors per spec:

- One header per AMS unit (`AMS 1`, `AMS 2`, …) with humidity pill on the right (skip the pill when `humidity === -1`, e.g. AMS Lite without sensor) and a chevron that collapses the unit (state in `localStorage` per unit: `bg.ams-unit-{id}.collapsed = "1" | "0"`).
- For each unit's trays (filtered from `trays[]` by `tray.ams_id === unit.id`), render a `<TrayRow/>`. Tray title `"Tray N"` where `N = tray.slot + 1`. Subtitle line `<TYPE> · <FILAMENT_ID>` in mono (e.g. `"PLA · GFA00"`). Body line: `matched_filament.name` if present, else `tray_sub_brands` if present, else italic `text-2 "Empty"` for empty slots.
- An "In Use" badge on the right + `highlighted` border when `printer.active_tray !== null && printer.active_tray === tray.tray_id`.
- A separate "External Spool" header below all AMS units when `vt_tray` exists.
- Hide the entire section when the active printer's id ≠ `ams.printer_id` (backend gap noted in the plan header). Show one-line note `"AMS data only available for the default printer (backend follow-up planned)."` in `text-2`.

- [ ] **Step 11.1: Create `web/src/components/dashboard/ams-section.tsx`**

```typescript
import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { TrayRow } from '@/components/tray-row';
import { Badge } from '@/components/ui/badge';
import { normalizeTrayColor } from '@/lib/filament-color';
import type { AMSResponse, AMSTray, AMSUnit } from '@/lib/api/types';
import { cn } from '@/lib/utils';

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

  return (
    <section className="flex flex-col gap-4">
      {ams.units.map((unit) => (
        <AmsUnitGroup
          key={unit.id}
          unit={unit}
          trays={ams.trays.filter((t) => t.ams_id === unit.id)}
          activeTrayId={activeTrayId}
        />
      ))}
      {ams.vt_tray && (
        <ExternalSpoolGroup tray={ams.vt_tray} activeTrayId={activeTrayId} />
      )}
    </section>
  );
}

function AmsUnitGroup({
  unit,
  trays,
  activeTrayId,
}: {
  unit: AMSUnit;
  trays: AMSTray[];
  activeTrayId: number | null;
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
            <AmsTrayRow key={`${tray.ams_id}-${tray.slot}`} tray={tray} activeTrayId={activeTrayId} />
          ))}
        </div>
      )}
    </div>
  );
}

function ExternalSpoolGroup({
  tray,
  activeTrayId,
}: {
  tray: AMSTray;
  activeTrayId: number | null;
}) {
  return (
    <div className="flex flex-col gap-2">
      <header className="flex items-center justify-between">
        <span className="text-base font-semibold text-white">External Spool</span>
      </header>
      <AmsTrayRow tray={tray} activeTrayId={activeTrayId} />
    </div>
  );
}

function AmsTrayRow({
  tray,
  activeTrayId,
}: {
  tray: AMSTray;
  activeTrayId: number | null;
}) {
  const color = normalizeTrayColor(tray.tray_color);
  const isEmpty = color == null && !tray.tray_type;
  const inUse = activeTrayId != null && activeTrayId === tray.tray_id;

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
    />
  );
}
```

- [ ] **Step 11.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 11.3: Commit**

```bash
git add web/src/components/dashboard/ams-section.tsx
git commit -m "Add AMS section with per-unit collapse, In Use highlight, External Spool"
```

---

## Task 12: Wire the Dashboard route

**Files:**
- Modify: `web/src/routes/dashboard.tsx`

This task pulls everything together: it queries `/api/printers` and `/api/ams`, syncs the active printer id with the picker, and composes the page.

Key behaviors:

- React Query polling every 4000ms (matches the old Jinja UI cadence and `App.tsx`'s `staleTime: 4_000`). Use `refetchInterval: 4_000`.
- On first load, if `activePrinterId` is `null` or doesn't match any printer in the response, default to the first printer in the list and persist via `setActivePrinterId(...)`.
- Three load states: initial loading (skeleton), error (red banner), empty (no printers configured → CTA to Settings).
- Once loaded: render the title row, picker, hero, stat chips, and AMS section (in that order).

- [ ] **Step 12.1: Replace `web/src/routes/dashboard.tsx` with the full composition**

```typescript
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Skeleton } from '@/components/ui/skeleton';
import { Card } from '@/components/ui/card';
import { PrinterPicker } from '@/components/printer-picker';
import { HeroCard } from '@/components/dashboard/hero-card';
import { StatChipsRow } from '@/components/dashboard/stat-chips-row';
import { AmsSection } from '@/components/dashboard/ams-section';
import { listPrinters } from '@/lib/api/printers';
import { getAms } from '@/lib/api/ams';
import { usePrinterContext } from '@/lib/printer-context';

export default function DashboardRoute() {
  const { activePrinterId, setActivePrinterId } = usePrinterContext();

  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: () => listPrinters(),
    refetchInterval: 4_000,
  });

  const amsQuery = useQuery({
    queryKey: ['ams'],
    queryFn: () => getAms(),
    refetchInterval: 4_000,
    // AMS endpoint 404s when no printers are configured — don't run it then.
    enabled: !!printersQuery.data && printersQuery.data.printers.length > 0,
    retry: false,
  });

  const printers = printersQuery.data?.printers ?? [];

  // Default-pick the first printer once the list arrives, or recover when the
  // saved id no longer exists in the configured list.
  useEffect(() => {
    if (printers.length === 0) return;
    const stillExists = activePrinterId && printers.some((p) => p.id === activePrinterId);
    if (!stillExists) setActivePrinterId(printers[0].id);
  }, [printers, activePrinterId, setActivePrinterId]);

  if (printersQuery.isLoading) return <DashboardLoading />;
  if (printersQuery.isError) return <DashboardError detail={(printersQuery.error as Error).message} />;
  if (printers.length === 0) return <DashboardEmpty />;

  const active = printers.find((p) => p.id === activePrinterId) ?? printers[0];

  return (
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

      {amsQuery.data && (
        <AmsSection
          activePrinterId={active.id}
          ams={amsQuery.data}
          activeTrayId={active.active_tray}
        />
      )}
    </div>
  );
}

function DashboardLoading() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-9 w-48" />
      <Skeleton className="h-10 w-full max-w-md rounded-full" />
      <Skeleton className="h-44 w-full rounded-2xl" />
      <div className="grid grid-cols-3 gap-2.5">
        <Skeleton className="h-20 rounded-xl" />
        <Skeleton className="h-20 rounded-xl" />
        <Skeleton className="h-20 rounded-xl" />
      </div>
    </div>
  );
}

function DashboardError({ detail }: { detail: string }) {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Dashboard</h1>
      <Card className="p-4 bg-card border-danger/40 text-sm text-text-0">
        Failed to load printer status: <span className="font-mono">{detail}</span>
      </Card>
    </div>
  );
}

function DashboardEmpty() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Dashboard</h1>
      <Card className="p-6 bg-card border-border flex flex-col gap-3 items-start">
        <div className="text-base font-semibold text-white">No printers configured</div>
        <div className="text-sm text-text-1">Add a printer to start monitoring its status here.</div>
        <a
          href="/beta/settings"
          className="inline-flex items-center px-3.5 py-2 rounded-full bg-accent-strong text-white text-sm font-semibold hover:bg-accent transition-colors"
        >
          Open Settings →
        </a>
      </Card>
    </div>
  );
}
```

- [ ] **Step 12.2: Type-check**

Run: `cd web && npm run lint`
Expected: exit 0.

- [ ] **Step 12.3: Production build**

Run: `cd web && npm run build`
Expected: build succeeds, `app/static/dist/index.html` is regenerated, no TS errors.

- [ ] **Step 12.4: Commit**

```bash
git add web/src/routes/dashboard.tsx
git commit -m "Wire Dashboard route: picker + hero + stat chips + AMS"
```

---

## Task 13: Manual smoke-test in the dev server

**Files:** none modified.

This is the only manual verification step. Phase 1 already proved `/beta` serves the bundle from `app/static/dist/`; here we verify the Dashboard renders against a real backend.

- [ ] **Step 13.1: Start the FastAPI backend**

Open a terminal in the repo root with `.venv` active:

```bash
uvicorn app.main:app --reload --port 4844
```

Expected: server starts, `MQTT connected to <printer-ip>` logs appear within ~5 seconds for each configured printer.

- [ ] **Step 13.2: Start the Vite dev server**

In a second terminal:

```bash
cd web && npm run dev
```

Expected: `VITE v5.x.x ready in N ms` followed by `Local: http://localhost:5173/`.

- [ ] **Step 13.3: Open `http://localhost:5173/beta` and verify each visual region**

Open browser DevTools → Console + Network. Walk through:

1. **Page chrome** (Phase 1 — sanity): brand left, `Dashboard | Print` pill in header, `⚙ Settings` pill on right. Dashboard pill is highlighted.
2. **Title:** "Dashboard" in 28px extrabold white.
3. **Picker:** with 1 printer, you see one chip with a colored dot + the printer name; the chip background is `surface-3` (active). With 4+ printers, instead a single pill + chevron — clicking opens a `<Command/>` with search.
4. **Hero card:** state badge in the top-left (e.g. "PRINTING" in blue, "IDLE" in green). For active prints, large 48px % + meta line + thin gradient progress bar. For idle, "Ready" with green check + last filename. For offline, dimmed card with "Offline · Check connection" link to `/beta/settings`.
5. **Stat chips:** three-up grid. Nozzle/Bed values orange (`warm-hot`) with `/<target>°` in `text-2`. Speed in blue (`accent`).
6. **AMS section:** header `AMS 1` + humidity pill (e.g. `12% RH`) + chevron. Below, one `<TrayRow/>` per slot, color dot on the left, title "Tray 1", mono subtitle "PLA · GFA00", filament name in body. The currently-loaded tray (`active_tray`) has a 1px blue border + ring + an "In Use" badge on the right. Empty slots show italic "Empty". `External Spool` header below if `vt_tray` exists.
7. **Polling:** Network tab shows `/api/printers` and `/api/ams` each refire every 4 seconds while the tab is focused.
8. **Picker → state:** click a different printer in the picker. The hero/chips/AMS update on the next poll. Reload the page — selection persists.
9. **Console:** zero errors, zero warnings (other than React DevTools nags).

- [ ] **Step 13.4: Verify the production bundle also works at `/beta`**

Stop both servers. Then:

```bash
cd web && npm run build && cd .. && uvicorn app.main:app --port 4844
```

Open `http://localhost:4844/beta`. Expected: identical Dashboard rendering (no proxy involved — the FastAPI server itself serves `app/static/dist/index.html` and assets). If a network tab shows `/beta/assets/*` 404s, the `app.mount("/beta/assets", ...)` block in `app/main.py` is the place to debug.

If anything in 13.3 or 13.4 fails, **do not commit** — fix the relevant component task and re-run.

---

## Task 14: Final audit + README note

**Files:**
- Modify: `README.md` (add Phase 2 line under the existing `/beta` workflow section)

- [ ] **Step 14.1: Update `README.md`**

Open `README.md`, find the section added in commit `ae5d56d` ("README: document the new /beta frontend workflow"). Append one line at the end of that section:

```markdown
- **Phase 2 (current):** `/beta` shows a read-only Dashboard for the active printer (picker, hero, stat chips, AMS). No control buttons yet — pause/resume/cancel/speed and AMS drying ship in Phase 3.
```

(Use the exact wording above so the line shows up cleanly in `git diff` review.)

- [ ] **Step 14.2: Commit**

```bash
git add README.md
git commit -m "README: document Phase 2 read-only Dashboard at /beta"
```

- [ ] **Step 14.3: Final audit checklist**

Walk this list end-to-end before declaring Phase 2 done.

- [ ] `cd web && npm run lint` exits 0.
- [ ] `cd web && npm run build` exits 0 and writes `app/static/dist/index.html`.
- [ ] `git status` is clean (no untracked files).
- [ ] All 14 tasks above have committed (`git log --oneline` shows ~15 new commits, one per Step X.N "Commit").
- [ ] The Dashboard renders against the production bundle (Task 13.4).
- [ ] Old Jinja UI at `/` is unchanged (smoke check: `curl -s http://localhost:4844/ | grep '<title>'` still returns the Jinja title).
- [ ] No file in `app/` was modified (`git diff main..HEAD -- app/` is empty).

If all items check out, Phase 2 is complete. Phase 3 (Dashboard controls + AMS drying sheet) gets its own plan next.
