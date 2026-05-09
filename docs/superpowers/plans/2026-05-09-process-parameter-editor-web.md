# Process Parameter Editor — Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the iOS process-parameter editor to the React + Vite + TypeScript + Tailwind + shadcn/ui dark-only SPA, exposing a Modified-settings card on the print page and a right-side All-settings sheet over the gateway's `/api/slicer/options/process[/layout]` catalogue, with per-3MF override state submitted as the existing `process_overrides` form field.

**Architecture:** Long-lived catalogue + layout + per-profile baseline cached via TanStack Query (manual `version` invalidation). Per-3MF overrides + baseline + sheet open state on `usePrintContext`. A pure `effectiveValue(key, overrides, modifications, baseline, catalogue)` resolver drives every row's display value and revert target. Inline-expand editors auto-commit on change/blur; per-row Revert removes the key from the override map.

**Tech Stack:** React 18, Vite 5, TypeScript 5.6, Tailwind CSS 3, shadcn/ui (Radix primitives), TanStack Query v5, Lucide icons, Sonner toasts, React Router v6. New runtime deps: shadcn `switch`, `slider`, `toggle-group` primitives. New dev deps: `vitest`, `@testing-library/react`, `@testing-library/user-event`, `jsdom`.

**Spec:** `docs/superpowers/specs/2026-05-09-process-parameter-editor-web-design.md`.

---

## File structure (cross-task summary)

**New:**

```
web/src/
  components/print/
    process-parameters-card.tsx          ← Task 9: Modified-view card on print.tsx
    process-all-sheet.tsx                ← Tasks 10, 11: right-side Sheet root + drill-down
    process-page-detail.tsx              ← Task 11: page detail view inside the Sheet
    process-option-row.tsx               ← Task 8: shared row + inline-expand editor
    process-option-editor/
      bool-editor.tsx                    ← Task 7
      number-editor.tsx                  ← Task 7: int / float / percent / float-or-percent
      enum-editor.tsx                    ← Task 7
      string-editor.tsx                  ← Task 7
      slider-editor.tsx                  ← Task 7: guiType=slider
      color-editor.tsx                   ← Task 7: guiType=color
      readonly-vector.tsx                ← Task 7: coPoint* / coBools / coNone
  components/ui/
    switch.tsx                           ← Task 7 (shadcn add)
    slider.tsx                           ← Task 7 (shadcn add)
    toggle-group.tsx                     ← Task 7 (shadcn add)
  lib/api/
    process-options.ts                   ← Task 4: fetch catalogue, layout, profile baseline
  lib/process/
    types.ts                             ← Task 2
    effective-value.ts                   ← Task 3: pure resolver + revertTarget
  test/
    setup.ts                             ← Task 1: jsdom + RTL globals
```

**Modified:**

```
web/
  package.json                           ← Tasks 1, 7 (devDeps + scripts)
  vitest.config.ts                       ← Task 1 (NEW config file)
web/src/
  routes/print.tsx                       ← Task 12: embed ProcessParametersCard
                                            between slicing-settings-group and filaments-group
  lib/print-context.tsx                  ← Task 5: +processOverrides, +processBaseline,
                                            +setProcessOverride, +revertProcessOverride,
                                            +resetAllProcessOverrides,
                                            +processSheetOpen / setProcessSheetOpen
  lib/api/types.ts                       ← Task 2: extend ThreeMFInfo with process_modifications;
                                            add process_overrides_applied to settings_transfer
  lib/api/print.ts                       ← Task 6: +processOverrides on print + preview submission builders
  lib/api/slice-jobs.ts                  ← Task 6: +processOverrides on submitSliceJob
```

---

## Task 1: Set up Vitest + React Testing Library + JSDOM

The web project currently has no test runner — only `tsc --noEmit` as a lint target. The spec calls for unit tests on the resolver and component tests on the row / card / sheet, so we need a runner.

**Files:**
- Modify: `web/package.json` (devDependencies + scripts)
- Create: `web/vitest.config.ts`
- Create: `web/src/test/setup.ts`

**- [ ] Step 1: Install dev dependencies**

Run from the `web/` directory:

```bash
cd web && npm install --save-dev vitest @vitest/ui @testing-library/react @testing-library/user-event @testing-library/jest-dom jsdom
```

**- [ ] Step 2: Add test scripts to `web/package.json`**

In `web/package.json`, add to the `scripts` block:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "lint": "tsc --noEmit",
    "test": "vitest run",
    "test:watch": "vitest"
  }
}
```

**- [ ] Step 3: Create `web/vitest.config.ts`**

```ts
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
```

**- [ ] Step 4: Create `web/src/test/setup.ts`**

```ts
import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
});
```

**- [ ] Step 5: Add a smoke test to verify the runner works**

Create `web/src/test/smoke.test.ts`:

```ts
import { describe, it, expect } from 'vitest';

describe('vitest smoke', () => {
  it('runs', () => {
    expect(2 + 2).toBe(4);
  });
});
```

**- [ ] Step 6: Run the smoke test**

```bash
cd web && npm run test
```

Expected: `1 passed`. Once green, delete `web/src/test/smoke.test.ts` (the setup file stays).

**- [ ] Step 7: Update `tsconfig.app.json` so test files type-check**

If `web/tsconfig.app.json` doesn't already include `src/**/*.test.ts(x)` and `src/test/**/*`, add them to its `include` array. Run `cd web && npm run lint` — expect no errors.

**- [ ] Step 8: Commit**

```bash
cd ..
git add web/package.json web/package-lock.json web/vitest.config.ts web/src/test/setup.ts web/tsconfig.app.json
rm web/src/test/smoke.test.ts
git commit -m "Set up Vitest + RTL for the web project"
```

---

## Task 2: TypeScript types + extend `ThreeMFInfo` / `SettingsTransferInfo`

**Files:**
- Create: `web/src/lib/process/types.ts`
- Modify: `web/src/lib/api/types.ts`

**- [ ] Step 1: Create `web/src/lib/process/types.ts`**

```ts
export type ProcessOptionType =
  | 'coBool' | 'coBools'
  | 'coInt'  | 'coInts'
  | 'coFloat' | 'coFloats'
  | 'coPercent' | 'coPercents'
  | 'coFloatOrPercent' | 'coFloatsOrPercents'
  | 'coString' | 'coStrings'
  | 'coEnum'
  | 'coPoint' | 'coPoints' | 'coPoint3'
  | 'coNone';

export type ProcessOptionGuiType =
  | '' | 'color' | 'slider' | 'i_enum_open' | 'f_enum_open'
  | 'select_open' | 'legend' | 'one_string';

export interface ProcessOption {
  key: string;
  label: string;
  category: string;
  tooltip: string;
  type: ProcessOptionType;
  sidetext: string;
  default: string;
  min: number | null;
  max: number | null;
  enumValues: string[] | null;
  enumLabels: string[] | null;
  mode: 'simple' | 'advanced' | 'develop';
  guiType: ProcessOptionGuiType;
  nullable: boolean;
  readonly: boolean;
}

export interface ProcessOptionsCatalogue {
  version: string;
  options: Record<string, ProcessOption>;
}

export interface ProcessLayout {
  version: string;
  /** Accepted but not consulted client-side (allowlist deprecated upstream). */
  allowlistRevision: string;
  pages: ProcessPage[];
}

export interface ProcessPage {
  label: string;
  optgroups: ProcessOptgroup[];
}

export interface ProcessOptgroup {
  label: string;
  /** Option keys; metadata via the catalogue. */
  options: string[];
}

export interface ProcessModifications {
  processSettingId: string;
  modifiedKeys: string[];
  values: Record<string, string>;
}

export interface ProcessOverrideApplied {
  key: string;
  value: string;
  previous: string;
}
```

**- [ ] Step 2: Extend `ThreeMFInfo` in `web/src/lib/api/types.ts`**

Find the `export interface ThreeMFInfo { ... }` block (currently at line 164). At the top of `web/src/lib/api/types.ts`, add an import:

```ts
import type { ProcessModifications, ProcessOverrideApplied } from '@/lib/process/types';
```

Then add the new field at the end of the `ThreeMFInfo` body:

```ts
export interface ThreeMFInfo {
  plates: PlateInfo[];
  filaments: FilamentInfo[];
  print_profile: PrintProfileInfo;
  printer: PrinterInfo;
  has_gcode: boolean;
  bed_type: string;
  /** Server-derived diff vs. the system process preset. Optional — older gateways omit it. */
  process_modifications?: ProcessModifications | null;
}
```

**- [ ] Step 3: Extend `SettingsTransferInfo`**

Find the `export interface SettingsTransferInfo { ... }` block (currently at line 308). Add the new field at the end:

```ts
export interface SettingsTransferInfo {
  status: string;
  transferred: TransferredSetting[];
  filaments: FilamentTransferEntry[];
  /** Per-key result of `process_overrides` resolution, present when overrides were submitted. */
  process_overrides_applied?: ProcessOverrideApplied[];
}
```

**- [ ] Step 4: Type-check**

```bash
cd web && npm run lint
```

Expected: no errors.

**- [ ] Step 5: Commit**

```bash
cd ..
git add web/src/lib/process/types.ts web/src/lib/api/types.ts
git commit -m "Add process-editor TypeScript model and extend ThreeMFInfo"
```

---

## Task 3: Pure effective-value resolver

The resolver is the heart of the row's display logic and the revert target. Pure, table-testable, no React.

**Files:**
- Create: `web/src/lib/process/effective-value.ts`
- Create: `web/src/lib/process/effective-value.test.ts`

**- [ ] Step 1: Write the failing test**

Create `web/src/lib/process/effective-value.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { effectiveValue, revertTarget } from './effective-value';
import type { ProcessOptionsCatalogue, ProcessModifications } from './types';

const catalogue: ProcessOptionsCatalogue = {
  version: 'v1',
  options: {
    layer_height: {
      key: 'layer_height', label: 'Layer height', category: 'quality',
      tooltip: '', type: 'coFloat', sidetext: 'mm',
      default: '0.20', min: 0.05, max: 0.75,
      enumValues: null, enumLabels: null,
      mode: 'simple', guiType: '', nullable: false, readonly: false,
    },
    sparse_infill_density: {
      key: 'sparse_infill_density', label: 'Sparse infill density', category: 'strength',
      tooltip: '', type: 'coPercent', sidetext: '%',
      default: '15%', min: 0, max: 100,
      enumValues: null, enumLabels: null,
      mode: 'simple', guiType: '', nullable: false, readonly: false,
    },
  },
};

const modifications: ProcessModifications = {
  processSettingId: '0.16mm Standard @P1P',
  modifiedKeys: ['layer_height'],
  values: { layer_height: '0.16' },
};

const baseline: Record<string, string> = {
  layer_height: '0.20',
  sparse_infill_density: '15%',
  top_shell_layers: '4',
};

describe('effectiveValue', () => {
  it('prefers user override over everything else', () => {
    expect(effectiveValue('layer_height', { layer_height: '0.12' }, modifications, baseline, catalogue))
      .toBe('0.12');
  });

  it('falls back to 3MF modification when no override', () => {
    expect(effectiveValue('layer_height', {}, modifications, baseline, catalogue)).toBe('0.16');
  });

  it('falls back to baseline when key not modified by file', () => {
    expect(effectiveValue('sparse_infill_density', {}, modifications, baseline, catalogue)).toBe('15%');
  });

  it('falls back to catalogue default when baseline missing', () => {
    expect(effectiveValue('layer_height', {}, null, {}, catalogue)).toBe('0.20');
  });

  it('returns null when key is unknown everywhere', () => {
    expect(effectiveValue('mystery_key', {}, null, {}, catalogue)).toBeNull();
  });

  it('treats null modifications as absent', () => {
    expect(effectiveValue('sparse_infill_density', {}, null, baseline, catalogue)).toBe('15%');
  });
});

describe('revertTarget', () => {
  it('uses the 3MF value when modified by file', () => {
    expect(revertTarget('layer_height', modifications, baseline, catalogue)).toBe('0.16');
  });

  it('uses the baseline when not modified by file', () => {
    expect(revertTarget('sparse_infill_density', modifications, baseline, catalogue)).toBe('15%');
  });

  it('falls back to catalogue default', () => {
    expect(revertTarget('layer_height', null, {}, catalogue)).toBe('0.20');
  });

  it('ignores user overrides — revert is what we revert *to*', () => {
    // Even though the user had picked something, the revert target is still the 3MF/default value.
    expect(revertTarget('layer_height', modifications, baseline, catalogue)).toBe('0.16');
  });
});
```

**- [ ] Step 2: Run the test to verify it fails**

```bash
cd web && npm run test -- effective-value
```

Expected: failure with `Cannot find module './effective-value'`.

**- [ ] Step 3: Write the implementation**

Create `web/src/lib/process/effective-value.ts`:

```ts
import type { ProcessModifications, ProcessOptionsCatalogue } from './types';

/**
 * Resolves the value to display for `key`, walking the four-rung fallback:
 * user override → 3MF modification → resolved baseline → catalogue default.
 * Returns null when the key is unknown to all four sources.
 */
export function effectiveValue(
  key: string,
  overrides: Record<string, string>,
  modifications: ProcessModifications | null,
  baseline: Record<string, string>,
  catalogue: ProcessOptionsCatalogue | null,
): string | null {
  if (key in overrides) return overrides[key];
  if (modifications && key in modifications.values) return modifications.values[key];
  if (key in baseline) return baseline[key];
  return catalogue?.options[key]?.default ?? null;
}

/**
 * The value a Revert button restores to — same chain as `effectiveValue`,
 * skipping the user override rung.
 */
export function revertTarget(
  key: string,
  modifications: ProcessModifications | null,
  baseline: Record<string, string>,
  catalogue: ProcessOptionsCatalogue | null,
): string | null {
  if (modifications && key in modifications.values) return modifications.values[key];
  if (key in baseline) return baseline[key];
  return catalogue?.options[key]?.default ?? null;
}
```

**- [ ] Step 4: Run the test to verify it passes**

```bash
cd web && npm run test -- effective-value
```

Expected: 10 passed.

**- [ ] Step 5: Commit**

```bash
cd ..
git add web/src/lib/process/effective-value.ts web/src/lib/process/effective-value.test.ts
git commit -m "Add effective-value resolver for process overrides"
```

---

## Task 4: Process-options API client + TanStack Query hooks

Three GET endpoints, plus a manual `version` invalidation that swaps the cache wholesale when a fresher payload arrives.

**Files:**
- Create: `web/src/lib/api/process-options.ts`

The gateway returns these shapes (defined in `app/models.py`, exposed by routes added in the gateway plan):

- `GET /api/slicer/options/process` → `{ version: string, options: Record<string, ProcessOption> }`
- `GET /api/slicer/options/process/layout` → `{ version: string, allowlist_revision: string, pages: [{ label, optgroups: [{ label, options }] }] }`
- `GET /api/slicer/processes/{id}` → `{ /* flat string→string map */ }`

The slicer ships them with snake_case field names; the API doc says fields like `enum_values`, `enum_labels`, `gui_type` are surfaced verbatim, so we'll camelCase them at the boundary inside this module.

**- [ ] Step 1: Inspect what the existing `client.ts` exposes**

Run:

```bash
sed -n '1,40p' web/src/lib/api/client.ts
```

Note the base path / fetch helper used by sibling modules (e.g. `printers.ts`, `slicer-profiles.ts`). The new module follows the same pattern.

**- [ ] Step 2: Create `web/src/lib/api/process-options.ts`**

```ts
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { apiFetch } from './client'; // confirm export name in step 1; rename if needed
import type {
  ProcessLayout,
  ProcessOption,
  ProcessOptionsCatalogue,
} from '@/lib/process/types';

/* ------------------------------------------------------------------ */
/* Wire shapes (snake_case from the slicer) and adapters to camelCase. */
/* ------------------------------------------------------------------ */

interface RawProcessOption {
  key: string;
  label: string;
  category: string;
  tooltip: string;
  type: ProcessOption['type'];
  sidetext: string;
  default: string;
  min: number | null;
  max: number | null;
  enum_values: string[] | null;
  enum_labels: string[] | null;
  mode: ProcessOption['mode'];
  gui_type: ProcessOption['guiType'];
  nullable: boolean;
  readonly: boolean;
}

interface RawCatalogue {
  version: string;
  options: Record<string, RawProcessOption>;
}

interface RawLayout {
  version: string;
  allowlist_revision: string;
  pages: { label: string; optgroups: { label: string; options: string[] }[] }[];
}

function adaptOption(raw: RawProcessOption): ProcessOption {
  return {
    key: raw.key,
    label: raw.label,
    category: raw.category,
    tooltip: raw.tooltip,
    type: raw.type,
    sidetext: raw.sidetext,
    default: raw.default,
    min: raw.min,
    max: raw.max,
    enumValues: raw.enum_values,
    enumLabels: raw.enum_labels,
    mode: raw.mode,
    guiType: raw.gui_type,
    nullable: raw.nullable,
    readonly: raw.readonly,
  };
}

function adaptCatalogue(raw: RawCatalogue): ProcessOptionsCatalogue {
  const options: Record<string, ProcessOption> = {};
  for (const [k, v] of Object.entries(raw.options)) options[k] = adaptOption(v);
  return { version: raw.version, options };
}

function adaptLayout(raw: RawLayout): ProcessLayout {
  return {
    version: raw.version,
    allowlistRevision: raw.allowlist_revision,
    pages: raw.pages.map((p) => ({
      label: p.label,
      optgroups: p.optgroups.map((g) => ({ label: g.label, options: g.options })),
    })),
  };
}

/* ------------------------------------------------------------------ */
/* Fetchers                                                            */
/* ------------------------------------------------------------------ */

export async function fetchProcessOptions(): Promise<ProcessOptionsCatalogue> {
  const raw = await apiFetch<RawCatalogue>('/api/slicer/options/process');
  return adaptCatalogue(raw);
}

export async function fetchProcessLayout(): Promise<ProcessLayout> {
  const raw = await apiFetch<RawLayout>('/api/slicer/options/process/layout');
  return adaptLayout(raw);
}

export async function fetchProcessProfile(settingId: string): Promise<Record<string, string>> {
  return apiFetch<Record<string, string>>(
    `/api/slicer/processes/${encodeURIComponent(settingId)}`,
  );
}

/* ------------------------------------------------------------------ */
/* Hooks (TanStack Query)                                              */
/* ------------------------------------------------------------------ */

const RETRYABLE_503_CODES = new Set(['options_not_loaded', 'options_layout_not_loaded']);

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 1) return false;
  // apiFetch throws Error with optional `.code` from the JSON body's "code" field.
  const code = (error as { code?: string } | undefined)?.code;
  return !!code && RETRYABLE_503_CODES.has(code);
}

export function useProcessOptions(): UseQueryResult<ProcessOptionsCatalogue> {
  return useQuery({
    queryKey: ['process-options', 'catalogue'],
    queryFn: fetchProcessOptions,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    retry: shouldRetry,
    retryDelay: 1500,
  });
}

export function useProcessLayout(): UseQueryResult<ProcessLayout> {
  return useQuery({
    queryKey: ['process-options', 'layout'],
    queryFn: fetchProcessLayout,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    retry: shouldRetry,
    retryDelay: 1500,
  });
}

export function useProcessProfile(
  settingId: string | undefined,
): UseQueryResult<Record<string, string>> {
  return useQuery({
    queryKey: ['process-options', 'profile', settingId ?? ''],
    queryFn: () => fetchProcessProfile(settingId!),
    enabled: !!settingId,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
  });
}
```

> **Note on `apiFetch`:** if the existing helper has a different name (e.g. `fetchJson`), rename the imports + calls. If it doesn't surface `error.code` for 5xx responses, extend it minimally to read `code` from the JSON body so `shouldRetry` works. Keep the change scoped to a single property addition.

**- [ ] Step 3: Type-check**

```bash
cd web && npm run lint
```

Expected: no errors.

**- [ ] Step 4: Commit**

```bash
cd ..
git add web/src/lib/api/process-options.ts
# also add web/src/lib/api/client.ts if you extended it for error.code
git commit -m "Add process-options API client and TanStack Query hooks"
```

---

## Task 5: Extend `PrintProvider` with override / baseline / sheet state

Mirrors the iOS `AppViewModel` per-3MF state. Lifecycle hooks live where their triggers do (3MF parse and process-profile change in `print.tsx`); the provider only stores state and the action callbacks.

**Files:**
- Modify: `web/src/lib/print-context.tsx`

**- [ ] Step 1: Read the current `print-context.tsx` end-to-end**

```bash
cat web/src/lib/print-context.tsx
```

Note the `Ctx` type and the `PrintProvider` `useMemo` value.

**- [ ] Step 2: Extend the `Ctx` type**

Add the new fields to the `type Ctx = { ... }` block:

```ts
type Ctx = {
  state: PrintState;
  setState: Dispatch<SetStateAction<PrintState>>;
  settings: SlicingSettings;
  setSettings: Dispatch<SetStateAction<SlicingSettings>>;
  selectedPlateId: number;
  setSelectedPlateId: Dispatch<SetStateAction<number>>;
  filamentMapping: FilamentMapping;
  setFilamentMapping: Dispatch<SetStateAction<FilamentMapping>>;
  sliceAbortRef: MutableRefObject<AbortController | null>;

  /** User-edited overrides, keyed by option key, libslic3r-stringified values. */
  processOverrides: Record<string, string>;
  setProcessOverride(key: string, value: string): void;
  revertProcessOverride(key: string): void;
  resetAllProcessOverrides(): void;

  /** Resolved system baseline for the active process profile, fetched on 3MF import. */
  processBaseline: Record<string, string>;
  setProcessBaseline: Dispatch<SetStateAction<Record<string, string>>>;

  /** All-settings sheet open/close. */
  processSheetOpen: boolean;
  setProcessSheetOpen: Dispatch<SetStateAction<boolean>>;
};
```

**- [ ] Step 3: Wire the new state inside `PrintProvider`**

Inside `PrintProvider`, after the existing `useState` calls, add:

```ts
const [processOverrides, setProcessOverrides] = useState<Record<string, string>>({});
const [processBaseline, setProcessBaseline] = useState<Record<string, string>>({});
const [processSheetOpen, setProcessSheetOpen] = useState(false);

const setProcessOverride = useCallback((key: string, value: string) => {
  setProcessOverrides((prev) => ({ ...prev, [key]: value }));
}, []);

const revertProcessOverride = useCallback((key: string) => {
  setProcessOverrides((prev) => {
    if (!(key in prev)) return prev;
    const next = { ...prev };
    delete next[key];
    return next;
  });
}, []);

const resetAllProcessOverrides = useCallback(() => {
  setProcessOverrides({});
}, []);
```

Add `useCallback` to the React imports at the top of the file. Then extend the `useMemo` value to include the new fields:

```ts
const value = useMemo<Ctx>(
  () => ({
    state, setState,
    settings, setSettings,
    selectedPlateId, setSelectedPlateId,
    filamentMapping, setFilamentMapping,
    sliceAbortRef,
    processOverrides, setProcessOverride, revertProcessOverride, resetAllProcessOverrides,
    processBaseline, setProcessBaseline,
    processSheetOpen, setProcessSheetOpen,
  }),
  [
    state, settings, selectedPlateId, filamentMapping,
    processOverrides, setProcessOverride, revertProcessOverride, resetAllProcessOverrides,
    processBaseline, processSheetOpen,
  ],
);
```

**- [ ] Step 4: Type-check**

```bash
cd web && npm run lint
```

Expected: no errors.

**- [ ] Step 5: Commit**

```bash
cd ..
git add web/src/lib/print-context.tsx
git commit -m "Extend PrintProvider with process overrides and All-sheet state"
```

---

## Task 6: Submission builders carry `process_overrides` + drop-notice toast

Two builders to update — `lib/api/print.ts` (used by `/api/print` direct submit and `/api/print-preview`) and `lib/api/slice-jobs.ts` (used by `submitSliceJob`). Both serialise `processOverrides` as a single JSON form field. After a successful slice/print response, compare `Object.keys(processOverrides)` against `settings_transfer.process_overrides_applied` and surface a Sonner toast for the dropped subset.

**Files:**
- Modify: `web/src/lib/api/print.ts`
- Modify: `web/src/lib/api/slice-jobs.ts`

**- [ ] Step 1: Inspect both files for current builder shapes**

```bash
grep -nE "FormData|append|filament_profiles|process_overrides" web/src/lib/api/print.ts web/src/lib/api/slice-jobs.ts
```

Note where each builder calls `fd.append('filament_profiles', ...)` — that's the exact pattern to mirror.

**- [ ] Step 2: Update each call site**

For every `printFile` / `previewFile` / `submitSliceJob` (or equivalent) builder in those two files, add an optional `processOverrides?: Record<string, string>` parameter, and inside the builder body, after the existing `fd.append('filament_profiles', ...)` call, append:

```ts
if (processOverrides && Object.keys(processOverrides).length > 0) {
  fd.append('process_overrides', JSON.stringify(processOverrides));
}
```

If a builder takes a single `args: { ... }` object (look for the existing `filament_profiles` field on that object), add `processOverrides?: Record<string, string>;` to its type and read it the same way.

**- [ ] Step 3: Add the drop-notice helper**

Create `web/src/lib/process/drop-notice.ts`:

```ts
import { toast } from 'sonner';
import type { ProcessOverrideApplied } from './types';

/**
 * Toast a non-blocking notice when the slicer dropped a subset of submitted overrides.
 * Silent when nothing was sent or every key was applied.
 */
export function notifyDroppedOverrides(
  sent: Record<string, string>,
  applied: ProcessOverrideApplied[] | undefined,
): void {
  const sentKeys = Object.keys(sent);
  if (sentKeys.length === 0) return;
  const appliedKeys = new Set((applied ?? []).map((o) => o.key));
  const dropped = sentKeys.filter((k) => !appliedKeys.has(k));
  if (dropped.length === 0) return;
  toast.message(
    `${appliedKeys.size} setting(s) sent, ${dropped.length} ignored: ${dropped.join(', ')}`,
  );
}
```

**- [ ] Step 4: Wire the drop notice into the success paths**

In `print.tsx` (or wherever the slice-job poller / preview-result handler currently lives), after a successful slice, call:

```ts
import { notifyDroppedOverrides } from '@/lib/process/drop-notice';

// inside the success handler that has `response.settings_transfer`:
notifyDroppedOverrides(processOverrides, response.settings_transfer?.process_overrides_applied);
```

> Locate the existing `settings_transfer` access (`grep -n settings_transfer web/src/routes/print.tsx web/src/lib/api/*.ts`). Add the call once per terminal success branch (preview ready, slice job ready, direct print sent). Don't duplicate — extract a tiny helper if there are multiple sites.

**- [ ] Step 5: Verify the call sites pass `processOverrides`**

In `print.tsx`, find every site that calls one of these builders and pass `processOverrides` from `usePrintContext`:

```ts
const { processOverrides } = usePrintContext();
// ...
await printFile({ /* existing args */, processOverrides });
```

Repeat for the other builders.

**- [ ] Step 6: Type-check**

```bash
cd web && npm run lint
```

Expected: no errors.

**- [ ] Step 7: Commit**

```bash
cd ..
git add web/src/lib/api/print.ts web/src/lib/api/slice-jobs.ts web/src/lib/process/drop-notice.ts web/src/routes/print.tsx
git commit -m "Submit process_overrides and toast on dropped overrides"
```

---

## Task 7: shadcn primitives + per-type editor widgets

Install three new shadcn primitives, then build seven small editor components keyed by `(type, guiType)`. Each editor receives the option metadata and the current draft value, calls back on commit.

**Files:**
- Create: `web/src/components/ui/switch.tsx` (via shadcn add)
- Create: `web/src/components/ui/slider.tsx` (via shadcn add)
- Create: `web/src/components/ui/toggle-group.tsx` (via shadcn add)
- Create: `web/src/components/print/process-option-editor/bool-editor.tsx`
- Create: `web/src/components/print/process-option-editor/number-editor.tsx`
- Create: `web/src/components/print/process-option-editor/enum-editor.tsx`
- Create: `web/src/components/print/process-option-editor/string-editor.tsx`
- Create: `web/src/components/print/process-option-editor/slider-editor.tsx`
- Create: `web/src/components/print/process-option-editor/color-editor.tsx`
- Create: `web/src/components/print/process-option-editor/readonly-vector.tsx`

**- [ ] Step 1: Install shadcn primitives**

```bash
cd web && npx shadcn-ui@latest add switch slider toggle-group
```

This drops the three components into `web/src/components/ui/` and installs `@radix-ui/react-switch`, `@radix-ui/react-slider`, `@radix-ui/react-toggle-group`.

**- [ ] Step 2: Define a shared editor prop interface**

Create `web/src/components/print/process-option-editor/types.ts`:

```ts
import type { ProcessOption } from '@/lib/process/types';

export interface EditorProps {
  option: ProcessOption;
  value: string;
  /** Called when the user has committed a new value. The parent decides whether to write to overrides. */
  onCommit(next: string): void;
  /** Called whenever the draft changes (used for slider's local state). Optional. */
  onDraftChange?(next: string): void;
  /** Set when the editor's current draft is known invalid (e.g. out of range). Suppresses commit. */
  onValidityChange?(valid: boolean): void;
}
```

**- [ ] Step 3: Bool editor**

Create `web/src/components/print/process-option-editor/bool-editor.tsx`:

```ts
import { Switch } from '@/components/ui/switch';
import type { EditorProps } from './types';

/** `coBool` — emits "1" / "0". */
export function BoolEditor({ value, onCommit }: EditorProps) {
  const checked = value === '1' || value.toLowerCase() === 'true';
  return (
    <div className="flex items-center justify-end">
      <Switch
        checked={checked}
        onCheckedChange={(next) => onCommit(next ? '1' : '0')}
        aria-label="Toggle value"
      />
    </div>
  );
}
```

**- [ ] Step 4: Number editor (int / float / percent / float-or-percent)**

Create `web/src/components/print/process-option-editor/number-editor.tsx`:

```ts
import { useEffect, useRef, useState } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import {
  ToggleGroup,
  ToggleGroupItem,
} from '@/components/ui/toggle-group';
import { Minus, Plus } from 'lucide-react';
import type { EditorProps } from './types';

const PERCENT_TYPES = new Set(['coPercent', 'coPercents']);
const INT_TYPES = new Set(['coInt', 'coInts']);
const MIXED_TYPES = new Set(['coFloatOrPercent', 'coFloatsOrPercents']);

function parseDraft(raw: string): { num: string; isPercent: boolean } {
  const trimmed = raw.trim();
  if (trimmed.endsWith('%')) {
    return { num: trimmed.slice(0, -1).trim(), isPercent: true };
  }
  return { num: trimmed, isPercent: false };
}

function clamp(num: number, min: number | null, max: number | null): number {
  if (min !== null && num < min) return min;
  if (max !== null && num > max) return max;
  return num;
}

export function NumberEditor({ option, value, onCommit, onValidityChange }: EditorProps) {
  const initial = parseDraft(value);
  const isPercentLockedSuffix = PERCENT_TYPES.has(option.type);
  const isMixed = MIXED_TYPES.has(option.type);
  const isInt = INT_TYPES.has(option.type);

  const [draft, setDraft] = useState(initial.num);
  const [unit, setUnit] = useState<'mm' | '%'>(
    isPercentLockedSuffix || initial.isPercent ? '%' : 'mm',
  );
  const lastCommitted = useRef(value);

  useEffect(() => {
    // External value changed (e.g. revert): resync.
    if (value !== lastCommitted.current) {
      const parsed = parseDraft(value);
      setDraft(parsed.num);
      if (!isPercentLockedSuffix) setUnit(parsed.isPercent ? '%' : 'mm');
      lastCommitted.current = value;
    }
  }, [value, isPercentLockedSuffix]);

  function commit(): void {
    const num = isInt ? parseInt(draft, 10) : parseFloat(draft);
    if (Number.isNaN(num)) {
      onValidityChange?.(false);
      return;
    }
    const clamped = clamp(num, option.min, option.max);
    onValidityChange?.(true);
    const formatted = isInt ? String(clamped) : String(clamped);
    const final = isPercentLockedSuffix || (isMixed && unit === '%')
      ? `${formatted}%`
      : formatted;
    setDraft(formatted);
    lastCommitted.current = final;
    onCommit(final);
  }

  function step(direction: 1 | -1) {
    const num = parseFloat(draft);
    if (Number.isNaN(num)) return;
    const next = clamp(num + direction, option.min, option.max);
    setDraft(String(next));
    const final = isPercentLockedSuffix || (isMixed && unit === '%') ? `${next}%` : String(next);
    lastCommitted.current = final;
    onCommit(final);
  }

  return (
    <div className="flex items-center gap-2">
      {isInt && (
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Decrement"
          onClick={() => step(-1)}
        >
          <Minus className="size-4" />
        </Button>
      )}
      <Input
        type="number"
        inputMode={isInt ? 'numeric' : 'decimal'}
        step={isInt ? '1' : 'any'}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            commit();
            (e.target as HTMLInputElement).blur();
          }
        }}
        className="w-32 text-right tabular-nums"
        aria-label={option.label}
      />
      {isInt && (
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Increment"
          onClick={() => step(1)}
        >
          <Plus className="size-4" />
        </Button>
      )}
      {isMixed && (
        <ToggleGroup
          type="single"
          value={unit}
          onValueChange={(v) => v && setUnit(v as 'mm' | '%')}
          aria-label="Unit"
        >
          <ToggleGroupItem value="mm">{option.sidetext || 'mm'}</ToggleGroupItem>
          <ToggleGroupItem value="%">%</ToggleGroupItem>
        </ToggleGroup>
      )}
      {!isMixed && (
        <span className="text-xs text-muted-foreground tabular-nums">
          {isPercentLockedSuffix ? '%' : option.sidetext}
        </span>
      )}
    </div>
  );
}
```

**- [ ] Step 5: Enum editor**

Create `web/src/components/print/process-option-editor/enum-editor.tsx`:

```ts
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { EditorProps } from './types';

export function EnumEditor({ option, value, onCommit }: EditorProps) {
  const values = option.enumValues ?? [];
  const labels = option.enumLabels ?? values;
  return (
    <Select value={value} onValueChange={onCommit}>
      <SelectTrigger className="w-56" aria-label={option.label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {values.map((v, i) => (
          <SelectItem key={v} value={v}>
            {labels[i] ?? v}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
```

**- [ ] Step 6: String editor**

Create `web/src/components/print/process-option-editor/string-editor.tsx`:

```ts
import { useEffect, useRef, useState } from 'react';
import { Input } from '@/components/ui/input';
import type { EditorProps } from './types';

export function StringEditor({ option, value, onCommit }: EditorProps) {
  const [draft, setDraft] = useState(value);
  const lastCommitted = useRef(value);

  useEffect(() => {
    if (value !== lastCommitted.current) {
      setDraft(value);
      lastCommitted.current = value;
    }
  }, [value]);

  function commit() {
    if (draft !== lastCommitted.current) {
      lastCommitted.current = draft;
      onCommit(draft);
    }
  }

  return (
    <Input
      type="text"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          commit();
          (e.target as HTMLInputElement).blur();
        }
      }}
      aria-label={option.label}
    />
  );
}
```

**- [ ] Step 7: Slider editor**

Create `web/src/components/print/process-option-editor/slider-editor.tsx`:

```ts
import { useEffect, useState } from 'react';
import { Slider } from '@/components/ui/slider';
import { Input } from '@/components/ui/input';
import type { EditorProps } from './types';

export function SliderEditor({ option, value, onCommit }: EditorProps) {
  const min = option.min ?? 0;
  const max = option.max ?? 100;
  const initial = parseFloat(value);
  const [draft, setDraft] = useState<number>(Number.isFinite(initial) ? initial : min);

  useEffect(() => {
    const next = parseFloat(value);
    if (Number.isFinite(next)) setDraft(next);
  }, [value]);

  return (
    <div className="flex items-center gap-3 w-full">
      <Slider
        min={min}
        max={max}
        step={option.type === 'coInt' || option.type === 'coInts' ? 1 : 0.01}
        value={[draft]}
        onValueChange={(vals) => setDraft(vals[0])}
        onValueCommit={(vals) => onCommit(String(vals[0]))}
        aria-label={option.label}
        className="flex-1"
      />
      <Input
        type="number"
        inputMode="decimal"
        value={draft}
        onChange={(e) => {
          const next = parseFloat(e.target.value);
          if (Number.isFinite(next)) setDraft(next);
        }}
        onBlur={() => onCommit(String(draft))}
        className="w-24 tabular-nums text-right"
        aria-label={`${option.label} value`}
      />
      {option.sidetext && (
        <span className="text-xs text-muted-foreground">{option.sidetext}</span>
      )}
    </div>
  );
}
```

**- [ ] Step 8: Color editor**

Create `web/src/components/print/process-option-editor/color-editor.tsx`:

```ts
import type { EditorProps } from './types';

export function ColorEditor({ option, value, onCommit }: EditorProps) {
  return (
    <div className="flex items-center gap-2">
      <input
        type="color"
        value={value || '#000000'}
        onChange={(e) => onCommit(e.target.value.toLowerCase())}
        aria-label={option.label}
        className="size-10 rounded border border-input bg-background"
      />
      <code className="text-xs text-muted-foreground">{value}</code>
    </div>
  );
}
```

**- [ ] Step 9: Read-only vector editor**

Create `web/src/components/print/process-option-editor/readonly-vector.tsx`:

```ts
import { Alert, AlertDescription } from '@/components/ui/alert';
import type { EditorProps } from './types';

export function ReadonlyVectorEditor({ value }: EditorProps) {
  return (
    <div className="space-y-2">
      <code className="text-xs">{value || '—'}</code>
      <Alert>
        <AlertDescription className="text-xs">
          Editing this option type is not yet supported.
        </AlertDescription>
      </Alert>
    </div>
  );
}
```

**- [ ] Step 10: Type-check**

```bash
cd web && npm run lint
```

Expected: no errors.

**- [ ] Step 11: Commit**

```bash
cd ..
git add web/src/components/ui/switch.tsx web/src/components/ui/slider.tsx web/src/components/ui/toggle-group.tsx \
        web/src/components/print/process-option-editor/ \
        web/package.json web/package-lock.json
git commit -m "Add per-type editor widgets for process options"
```

---

## Task 8: `ProcessOptionRow` (collapsed + inline-expand)

The shared row component. Renders the collapsed three-column layout, the status dot, the chevron, and on expansion the tooltip / widget / range hint / validation / footer. Picks the right editor by `(type, guiType)`.

**Files:**
- Create: `web/src/components/print/process-option-row.tsx`
- Create: `web/src/components/print/process-option-row.test.tsx`

**- [ ] Step 1: Pick-editor helper**

At the top of `web/src/components/print/process-option-row.tsx`, add a small helper:

```ts
import type { ProcessOption } from '@/lib/process/types';
import { BoolEditor } from './process-option-editor/bool-editor';
import { NumberEditor } from './process-option-editor/number-editor';
import { EnumEditor } from './process-option-editor/enum-editor';
import { StringEditor } from './process-option-editor/string-editor';
import { SliderEditor } from './process-option-editor/slider-editor';
import { ColorEditor } from './process-option-editor/color-editor';
import { ReadonlyVectorEditor } from './process-option-editor/readonly-vector';
import type { EditorProps } from './process-option-editor/types';

const READONLY_TYPES = new Set(['coPoint', 'coPoints', 'coPoint3', 'coBools', 'coNone']);

function pickEditor(option: ProcessOption): React.ComponentType<EditorProps> {
  if (READONLY_TYPES.has(option.type)) return ReadonlyVectorEditor;
  if (option.guiType === 'color') return ColorEditor;
  if (option.guiType === 'slider' && option.min !== null && option.max !== null) return SliderEditor;
  if (option.guiType === 'one_string') return StringEditor;
  switch (option.type) {
    case 'coBool': return BoolEditor;
    case 'coInt': case 'coInts':
    case 'coFloat': case 'coFloats':
    case 'coPercent': case 'coPercents':
    case 'coFloatOrPercent': case 'coFloatsOrPercents':
      return NumberEditor;
    case 'coEnum': return EnumEditor;
    case 'coString': case 'coStrings': return StringEditor;
    default: return ReadonlyVectorEditor;
  }
}
```

**- [ ] Step 2: Implement the row body**

Continue `web/src/components/print/process-option-row.tsx`:

```tsx
import { useState } from 'react';
import { ChevronRight, RotateCcw, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { ProcessOption } from '@/lib/process/types';

interface RowProps {
  option: ProcessOption;
  /** Effective value for display (resolver output). */
  value: string;
  /** What Revert restores to. */
  revertTo: string;
  /** True if the user has overridden this key. */
  isUserEdited: boolean;
  /** True if the file modified this key (independent of user edit). */
  isFileModified: boolean;
  /** Show tooltip caption under the label (All view only). */
  showTooltipCaption: boolean;
  isExpanded: boolean;
  onToggleExpand(): void;
  onCommit(next: string): void;
  onRevert(): void;
}

export function ProcessOptionRow(props: RowProps) {
  const {
    option, value, revertTo,
    isUserEdited, isFileModified, showTooltipCaption,
    isExpanded, onToggleExpand, onCommit, onRevert,
  } = props;

  const [valid, setValid] = useState(true);
  const [tooltipExpanded, setTooltipExpanded] = useState(false);

  const Editor = pickEditor(option);
  const dotClass = isUserEdited
    ? 'bg-orange-500'
    : isFileModified
      ? 'bg-sky-500'
      : '';
  const ariaDescription = isUserEdited
    ? 'edited by you'
    : isFileModified
      ? 'modified by file'
      : '';
  const isReadonly = READONLY_TYPES.has(option.type);

  return (
    <div className="border-b border-border/40 last:border-0">
      <button
        type="button"
        data-state={isExpanded ? 'open' : 'closed'}
        aria-expanded={isExpanded}
        aria-label={`${option.label}, ${value} ${option.sidetext}`}
        aria-description={ariaDescription || undefined}
        onClick={onToggleExpand}
        className={cn(
          'group flex w-full items-center gap-3 px-3 py-2.5 min-h-11 text-left',
          'hover:bg-accent/30 active:bg-accent/50',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          'transition-colors duration-fast',
        )}
      >
        <span
          aria-hidden="true"
          className={cn('flex w-3 items-center justify-center shrink-0')}
        >
          {dotClass && <span className={cn('size-2 rounded-full', dotClass)} />}
        </span>

        <span className="flex-1 min-w-0">
          <span className="block text-sm">{option.label}</span>
          {showTooltipCaption && option.tooltip && (
            <span className="block text-xs text-muted-foreground line-clamp-1">
              {option.tooltip}
            </span>
          )}
        </span>

        <span className="flex items-center gap-1 shrink-0">
          <span className="text-sm font-medium tabular-nums">{value}</span>
          {option.sidetext && (
            <span className="text-xs text-muted-foreground">{option.sidetext}</span>
          )}
          {!isReadonly && (
            <ChevronRight
              className={cn(
                'size-3 text-muted-foreground transition-transform duration-fast',
                isExpanded && 'rotate-90',
              )}
            />
          )}
        </span>
      </button>

      {/* CSS-grid expand trick — 0fr → 1fr animates row height. */}
      <div
        className={cn(
          'grid transition-[grid-template-rows] duration-base ease-standard motion-reduce:transition-none',
          isExpanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
        )}
      >
        <div className="overflow-hidden">
          {isExpanded && (
            <div className="px-3 py-3 pl-6 flex flex-col gap-2">
              {option.tooltip && (
                <p
                  className={cn(
                    'text-sm text-muted-foreground',
                    !tooltipExpanded && 'line-clamp-2',
                  )}
                >
                  {option.tooltip}
                </p>
              )}
              {option.tooltip && option.tooltip.length > 140 && (
                <Button
                  variant="link"
                  size="sm"
                  className="h-auto p-0 self-start"
                  onClick={() => setTooltipExpanded((v) => !v)}
                >
                  {tooltipExpanded ? 'Less' : 'More'}
                </Button>
              )}

              <Editor
                option={option}
                value={value}
                onCommit={onCommit}
                onValidityChange={setValid}
              />

              {(option.min !== null || option.max !== null) && (
                <p className="text-xs text-muted-foreground">
                  Range {option.min ?? '−∞'}–{option.max ?? '+∞'} {option.sidetext}
                </p>
              )}

              {!valid && (
                <p
                  role="alert"
                  className="flex items-center gap-1 text-xs text-destructive"
                >
                  <AlertTriangle className="size-3.5" />
                  Enter a valid {option.type.replace(/^co/, '').toLowerCase()} value
                </p>
              )}

              <div className="flex items-center justify-between pt-1">
                <p className="text-xs text-muted-foreground">
                  {isFileModified ? 'From file' : 'Default'}: <span className="tabular-nums">{revertTo}</span> {option.sidetext}
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onRevert}
                  disabled={!isUserEdited}
                >
                  <RotateCcw className="size-3.5 mr-1" />
                  Revert
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

**- [ ] Step 3: Write a focused component test**

Create `web/src/components/print/process-option-row.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ProcessOptionRow } from './process-option-row';
import type { ProcessOption } from '@/lib/process/types';

const layerHeight: ProcessOption = {
  key: 'layer_height', label: 'Layer height', category: 'quality',
  tooltip: 'Distance between the layers, controls vertical resolution.',
  type: 'coFloat', sidetext: 'mm',
  default: '0.20', min: 0.05, max: 0.75,
  enumValues: null, enumLabels: null,
  mode: 'simple', guiType: '', nullable: false, readonly: false,
};

const enableSupport: ProcessOption = {
  ...layerHeight,
  key: 'enable_support', label: 'Enable support', tooltip: '',
  type: 'coBool', sidetext: '', default: '0',
  min: null, max: null,
};

describe('ProcessOptionRow', () => {
  it('renders the label, value, and sidetext', () => {
    render(
      <ProcessOptionRow
        option={layerHeight}
        value="0.16"
        revertTo="0.20"
        isUserEdited={false}
        isFileModified
        showTooltipCaption={false}
        isExpanded={false}
        onToggleExpand={() => {}}
        onCommit={() => {}}
        onRevert={() => {}}
      />,
    );
    expect(screen.getByText('Layer height')).toBeInTheDocument();
    expect(screen.getByText('0.16')).toBeInTheDocument();
    expect(screen.getByText('mm')).toBeInTheDocument();
  });

  it('shows the orange dot when user-edited', () => {
    const { container } = render(
      <ProcessOptionRow
        option={layerHeight}
        value="0.12"
        revertTo="0.16"
        isUserEdited
        isFileModified
        showTooltipCaption={false}
        isExpanded={false}
        onToggleExpand={() => {}}
        onCommit={() => {}}
        onRevert={() => {}}
      />,
    );
    expect(container.querySelector('.bg-orange-500')).toBeInTheDocument();
    expect(container.querySelector('.bg-sky-500')).not.toBeInTheDocument();
  });

  it('toggles expand on click', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(
      <ProcessOptionRow
        option={enableSupport}
        value="0"
        revertTo="0"
        isUserEdited={false}
        isFileModified={false}
        showTooltipCaption={false}
        isExpanded={false}
        onToggleExpand={onToggle}
        onCommit={() => {}}
        onRevert={() => {}}
      />,
    );
    await user.click(screen.getByRole('button', { name: /Enable support/ }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('reveals the editor when expanded', () => {
    render(
      <ProcessOptionRow
        option={enableSupport}
        value="1"
        revertTo="0"
        isUserEdited
        isFileModified={false}
        showTooltipCaption={false}
        isExpanded
        onToggleExpand={() => {}}
        onCommit={() => {}}
        onRevert={() => {}}
      />,
    );
    expect(screen.getByRole('switch')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Revert/ })).toBeInTheDocument();
  });

  it('disables Revert when not user-edited', () => {
    render(
      <ProcessOptionRow
        option={layerHeight}
        value="0.16"
        revertTo="0.20"
        isUserEdited={false}
        isFileModified
        showTooltipCaption={false}
        isExpanded
        onToggleExpand={() => {}}
        onCommit={() => {}}
        onRevert={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: /Revert/ })).toBeDisabled();
  });
});
```

**- [ ] Step 4: Run the row tests**

```bash
cd web && npm run test -- process-option-row
```

Expected: 5 passed.

**- [ ] Step 5: Commit**

```bash
cd ..
git add web/src/components/print/process-option-row.tsx web/src/components/print/process-option-row.test.tsx
git commit -m "Add ProcessOptionRow with inline-expand editor"
```

---

## Task 9: `ProcessParametersCard` (Modified card)

The print-page card. Lists rows for `info.process_modifications.modifiedKeys` (or empty state). Header opens the All sheet via `setProcessSheetOpen(true)`. Loading and error branches.

**Files:**
- Create: `web/src/components/print/process-parameters-card.tsx`
- Create: `web/src/components/print/process-parameters-card.test.tsx`

**- [ ] Step 1: Implement the card**

Create `web/src/components/print/process-parameters-card.tsx`:

```tsx
import { useMemo, useState } from 'react';
import {
  Settings2, ChevronRight, SlidersHorizontal,
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ProcessOptionRow } from './process-option-row';
import {
  useProcessOptions,
  useProcessLayout,
} from '@/lib/api/process-options';
import { effectiveValue, revertTarget } from '@/lib/process/effective-value';
import { usePrintContext } from '@/lib/print-context';
import type { ProcessModifications } from '@/lib/process/types';

interface Props {
  modifications: ProcessModifications | null;
}

export function ProcessParametersCard({ modifications }: Props) {
  const {
    processOverrides, setProcessOverride, revertProcessOverride,
    processBaseline, setProcessSheetOpen,
  } = usePrintContext();

  const optionsQuery = useProcessOptions();
  const layoutQuery = useProcessLayout();
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const isLoading = optionsQuery.isLoading || layoutQuery.isLoading;
  const loadError = optionsQuery.error || layoutQuery.error;
  const catalogue = optionsQuery.data ?? null;

  // Modified rows = file-modified ∪ user-edited keys (per spec — user edits should appear here too).
  const rowKeys = useMemo(() => {
    const fromFile = modifications?.modifiedKeys ?? [];
    const fromUser = Object.keys(processOverrides);
    const merged: string[] = [...fromFile];
    for (const k of fromUser) if (!merged.includes(k)) merged.push(k);
    return merged;
  }, [modifications, processOverrides]);

  const modifiedCount = rowKeys.length;

  return (
    <Card className="p-4">
      <button
        type="button"
        onClick={() => setProcessSheetOpen(true)}
        className="flex w-full items-center gap-2 mb-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        aria-label="Open process settings"
      >
        <Settings2 className="size-3.5 text-accent" />
        <span className="text-base font-semibold">Process settings</span>
        <span className="ml-auto flex items-center gap-2">
          {modifiedCount > 0 && (
            <Badge variant="secondary">{modifiedCount} modified</Badge>
          )}
          <ChevronRight className="size-3 text-muted-foreground" />
        </span>
      </button>

      {isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : loadError ? (
        <Alert variant="destructive">
          <AlertDescription className="flex items-center justify-between gap-2">
            <span>Couldn't load process settings — Retry</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void optionsQuery.refetch();
                void layoutQuery.refetch();
              }}
            >
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : modifiedCount === 0 ? (
        <div className="flex flex-col items-center gap-2 py-4 text-center">
          <SlidersHorizontal className="size-7 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            No customizations from default profile
          </p>
        </div>
      ) : (
        <div className="flex flex-col">
          {rowKeys.map((key) => {
            const option = catalogue?.options[key];
            if (!option) {
              // Catalogue missing this key — render a degraded read-only row.
              return (
                <div key={key} className="px-3 py-2.5 text-sm font-mono">
                  {key}: {modifications?.values[key] ?? processOverrides[key] ?? '?'}
                </div>
              );
            }
            const value = effectiveValue(key, processOverrides, modifications, processBaseline, catalogue) ?? '';
            const revertTo = revertTarget(key, modifications, processBaseline, catalogue) ?? '';
            const isUserEdited = key in processOverrides;
            const isFileModified = !!modifications?.values && key in modifications.values;
            return (
              <ProcessOptionRow
                key={key}
                option={option}
                value={value}
                revertTo={revertTo}
                isUserEdited={isUserEdited}
                isFileModified={isFileModified}
                showTooltipCaption={false}
                isExpanded={expandedKey === key}
                onToggleExpand={() =>
                  setExpandedKey((prev) => (prev === key ? null : key))
                }
                onCommit={(next) => setProcessOverride(key, next)}
                onRevert={() => revertProcessOverride(key)}
              />
            );
          })}
        </div>
      )}

      <Button
        variant="secondary"
        className="mt-3 w-full"
        onClick={() => setProcessSheetOpen(true)}
      >
        Show all settings
        <ChevronRight className="size-3.5 ml-1" />
      </Button>
    </Card>
  );
}
```

**- [ ] Step 2: Write component tests**

Create `web/src/components/print/process-parameters-card.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ProcessParametersCard } from './process-parameters-card';
import { PrintProvider } from '@/lib/print-context';
import type { ProcessModifications } from '@/lib/process/types';

function withProviders(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return (
    <QueryClientProvider client={qc}>
      <PrintProvider>{ui}</PrintProvider>
    </QueryClientProvider>
  );
}

describe('ProcessParametersCard', () => {
  it('renders the empty state when no modifications', () => {
    const mods: ProcessModifications = {
      processSettingId: 'P1P 0.20', modifiedKeys: [], values: {},
    };
    render(withProviders(<ProcessParametersCard modifications={mods} />));
    expect(
      screen.getByText('No customizations from default profile'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Show all settings/ }),
    ).toBeInTheDocument();
  });

  it('shows the modified badge when keys are present', () => {
    const mods: ProcessModifications = {
      processSettingId: 'P1P 0.20',
      modifiedKeys: ['layer_height', 'sparse_infill_density'],
      values: { layer_height: '0.16', sparse_infill_density: '20%' },
    };
    render(withProviders(<ProcessParametersCard modifications={mods} />));
    expect(screen.getByText('2 modified')).toBeInTheDocument();
  });
});
```

> Note: these tests intentionally don't mock the API — `useProcessOptions` will land in a "loading" state (no fetch), which is fine for the structural assertions. Pull-up to a tested fetch can land in Task 12's Playwright happy path.

**- [ ] Step 3: Run the tests**

```bash
cd web && npm run test -- process-parameters-card
```

Expected: 2 passed (the loading skeleton replaces the empty state for the empty case — adjust the test to wait for the skeleton or to seed the QueryClient with empty catalogue/layout data; choose the seeding approach to keep the test deterministic).

If the test fails because of the loading state, seed the cache before render:

```ts
qc.setQueryData(['process-options', 'catalogue'], { version: 'v1', options: {} });
qc.setQueryData(['process-options', 'layout'], { version: 'v1', allowlistRevision: 'r1', pages: [] });
```

**- [ ] Step 4: Commit**

```bash
cd ..
git add web/src/components/print/process-parameters-card.tsx web/src/components/print/process-parameters-card.test.tsx
git commit -m "Add ProcessParametersCard for the print page"
```

---

## Task 10: `ProcessAllSheet` — page list mode + search

Right-side Sheet root + the page-list view + global search across all options. Drill-down (Task 11) and Reset-all confirm (also Task 11) come next.

**Files:**
- Create: `web/src/components/print/process-all-sheet.tsx`

**- [ ] Step 1: Skeleton + page list + search**

Create `web/src/components/print/process-all-sheet.tsx`:

```tsx
import { useMemo, useState } from 'react';
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetClose,
} from '@/components/ui/sheet';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ChevronRight, Search, RotateCcw } from 'lucide-react';
import { ProcessOptionRow } from './process-option-row';
import { ProcessPageDetail } from './process-page-detail';
import {
  useProcessOptions,
  useProcessLayout,
} from '@/lib/api/process-options';
import { effectiveValue, revertTarget } from '@/lib/process/effective-value';
import { usePrintContext } from '@/lib/print-context';
import type {
  ProcessLayout, ProcessOptionsCatalogue, ProcessPage, ProcessModifications,
} from '@/lib/process/types';
import { cn } from '@/lib/utils';

interface Props {
  modifications: ProcessModifications | null;
}

export function ProcessAllSheet({ modifications }: Props) {
  const {
    processSheetOpen, setProcessSheetOpen,
    processOverrides, setProcessOverride, revertProcessOverride,
    resetAllProcessOverrides, processBaseline,
  } = usePrintContext();

  const optionsQuery = useProcessOptions();
  const layoutQuery = useProcessLayout();
  const isLoading = optionsQuery.isLoading || layoutQuery.isLoading;
  const loadError = optionsQuery.error || layoutQuery.error;
  const catalogue = optionsQuery.data ?? null;
  const layout = layoutQuery.data ?? null;

  const [selectedPage, setSelectedPage] = useState<ProcessPage | null>(null);
  const [search, setSearch] = useState('');
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  // Reset drill-down when the sheet closes so it always reopens to the page list.
  function handleOpenChange(open: boolean) {
    setProcessSheetOpen(open);
    if (!open) {
      setSelectedPage(null);
      setSearch('');
      setExpandedKey(null);
    }
  }

  return (
    <Sheet open={processSheetOpen} onOpenChange={handleOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-[640px] lg:max-w-[720px] flex flex-col p-0"
      >
        <SheetHeader className="px-4 py-3 border-b border-border/40 flex-row items-center gap-2">
          {selectedPage && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setSelectedPage(null)}
              aria-label="Back to all pages"
            >
              ‹
            </Button>
          )}
          <SheetTitle className="text-base font-semibold flex-1 truncate">
            {selectedPage
              ? `Process settings / ${selectedPage.label}`
              : 'Process settings'}
          </SheetTitle>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => {
              if (window.confirm('Reset all process settings?')) {
                resetAllProcessOverrides();
              }
            }}
            disabled={Object.keys(processOverrides).length === 0}
            aria-label="Reset all"
          >
            <RotateCcw className="size-4" />
          </Button>
          <SheetClose asChild>
            <Button variant="ghost" size="icon" aria-label="Close">
              ✕
            </Button>
          </SheetClose>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="flex flex-col gap-2 p-4">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : loadError ? (
            <Alert variant="destructive" className="m-4">
              <AlertDescription className="flex items-center justify-between gap-2">
                <span>Couldn't load process settings — Retry</span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    void optionsQuery.refetch();
                    void layoutQuery.refetch();
                  }}
                >
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          ) : !catalogue || !layout ? null : selectedPage ? (
            <ProcessPageDetail
              page={selectedPage}
              catalogue={catalogue}
              modifications={modifications}
              processOverrides={processOverrides}
              processBaseline={processBaseline}
              expandedKey={expandedKey}
              onToggleExpand={(k) => setExpandedKey((prev) => (prev === k ? null : k))}
              onCommit={setProcessOverride}
              onRevert={revertProcessOverride}
            />
          ) : (
            <PageList
              layout={layout}
              catalogue={catalogue}
              modifications={modifications}
              processOverrides={processOverrides}
              processBaseline={processBaseline}
              search={search}
              setSearch={setSearch}
              expandedKey={expandedKey}
              onToggleExpand={(k) => setExpandedKey((prev) => (prev === k ? null : k))}
              onCommit={setProcessOverride}
              onRevert={revertProcessOverride}
              onPickPage={setSelectedPage}
            />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

/* ------------------------------------------------------------------ */

interface PageListProps {
  layout: ProcessLayout;
  catalogue: ProcessOptionsCatalogue;
  modifications: ProcessModifications | null;
  processOverrides: Record<string, string>;
  processBaseline: Record<string, string>;
  search: string;
  setSearch(s: string): void;
  expandedKey: string | null;
  onToggleExpand(k: string): void;
  onCommit(k: string, v: string): void;
  onRevert(k: string): void;
  onPickPage(p: ProcessPage): void;
}

function PageList(props: PageListProps) {
  const {
    layout, catalogue, modifications, processOverrides, processBaseline,
    search, setSearch, expandedKey, onToggleExpand, onCommit, onRevert, onPickPage,
  } = props;

  const editedPerPage = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const page of layout.pages) {
      let n = 0;
      for (const group of page.optgroups)
        for (const k of group.options)
          if (k in processOverrides) n++;
      counts[page.label] = n;
    }
    return counts;
  }, [layout, processOverrides]);

  const optionCountPerPage = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const page of layout.pages) {
      counts[page.label] = page.optgroups.reduce((sum, g) => sum + g.options.length, 0);
    }
    return counts;
  }, [layout]);

  const searchResults = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return null;
    const matches: Array<{ key: string; pageLabel: string }> = [];
    for (const page of layout.pages) {
      for (const group of page.optgroups) {
        for (const key of group.options) {
          const opt = catalogue.options[key];
          if (!opt) continue;
          if (
            opt.label.toLowerCase().includes(q) ||
            key.toLowerCase().includes(q)
          ) {
            matches.push({ key, pageLabel: page.label });
          }
        }
      }
    }
    return matches;
  }, [search, layout, catalogue]);

  return (
    <>
      <div className="px-4 py-3 border-b border-border/40">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search settings"
            className="pl-9"
            aria-label="Search settings"
          />
        </div>
      </div>

      {searchResults ? (
        <div className="divide-y divide-border/40">
          {searchResults.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">
              No matches for "{search}".
            </p>
          ) : (
            searchResults.map(({ key, pageLabel }) => {
              const opt = catalogue.options[key];
              if (!opt) return null;
              const value =
                effectiveValue(key, processOverrides, modifications, processBaseline, catalogue) ?? '';
              const revertTo =
                revertTarget(key, modifications, processBaseline, catalogue) ?? '';
              return (
                <div key={key} className="relative">
                  <ProcessOptionRow
                    option={opt}
                    value={value}
                    revertTo={revertTo}
                    isUserEdited={key in processOverrides}
                    isFileModified={!!modifications?.values && key in modifications.values}
                    showTooltipCaption
                    isExpanded={expandedKey === key}
                    onToggleExpand={() => onToggleExpand(key)}
                    onCommit={(v) => onCommit(key, v)}
                    onRevert={() => onRevert(key)}
                  />
                  <span className="pointer-events-none absolute right-10 top-3 text-xs text-muted-foreground">
                    {pageLabel}
                  </span>
                </div>
              );
            })
          )}
        </div>
      ) : (
        <div className="divide-y divide-border/40">
          {layout.pages.map((page) => {
            const edited = editedPerPage[page.label] ?? 0;
            const total = optionCountPerPage[page.label] ?? 0;
            return (
              <button
                key={page.label}
                type="button"
                className={cn(
                  'flex w-full items-center gap-3 px-4 py-3 min-h-12 text-left',
                  'hover:bg-accent/30 active:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                )}
                onClick={() => onPickPage(page)}
              >
                <span className="flex-1 text-sm">{page.label}</span>
                <span className="text-xs text-muted-foreground">
                  {total} options
                  {edited > 0 && (
                    <>
                      {' · '}
                      <span className="text-orange-500 font-semibold">{edited} edited</span>
                    </>
                  )}
                </span>
                <ChevronRight className="size-3 text-muted-foreground" />
              </button>
            );
          })}
        </div>
      )}
    </>
  );
}
```

**- [ ] Step 2: Type-check**

```bash
cd web && npm run lint
```

Expected: errors only about the missing `ProcessPageDetail` (added in Task 11). That's fine for now.

**- [ ] Step 3: Commit**

```bash
cd ..
git add web/src/components/print/process-all-sheet.tsx
git commit -m "Add ProcessAllSheet page-list mode with global search"
```

---

## Task 11: `ProcessPageDetail` (drill-down) + Reset-all confirmation

The page-detail view rendered when `selectedPage !== null`, plus an `AlertDialog`-based confirmation in place of the `window.confirm` shim from Task 10.

**Files:**
- Create: `web/src/components/print/process-page-detail.tsx`
- Modify: `web/src/components/print/process-all-sheet.tsx`

**- [ ] Step 1: Implement `ProcessPageDetail`**

Create `web/src/components/print/process-page-detail.tsx`:

```tsx
import { ProcessOptionRow } from './process-option-row';
import { effectiveValue, revertTarget } from '@/lib/process/effective-value';
import type {
  ProcessOptionsCatalogue, ProcessPage, ProcessModifications,
} from '@/lib/process/types';

interface Props {
  page: ProcessPage;
  catalogue: ProcessOptionsCatalogue;
  modifications: ProcessModifications | null;
  processOverrides: Record<string, string>;
  processBaseline: Record<string, string>;
  expandedKey: string | null;
  onToggleExpand(k: string): void;
  onCommit(k: string, v: string): void;
  onRevert(k: string): void;
}

export function ProcessPageDetail(props: Props) {
  const {
    page, catalogue, modifications, processOverrides, processBaseline,
    expandedKey, onToggleExpand, onCommit, onRevert,
  } = props;

  return (
    <div className="px-4 py-3">
      {page.optgroups.map((group) => (
        <section key={group.label} className="mb-4">
          <h3 className="text-xs font-semibold tracking-wide uppercase text-muted-foreground pb-2 pt-4">
            {group.label}
          </h3>
          <div className="rounded-lg border border-border/40 overflow-hidden">
            {group.options.map((key) => {
              const opt = catalogue.options[key];
              if (!opt) return null;
              const value =
                effectiveValue(key, processOverrides, modifications, processBaseline, catalogue) ?? '';
              const revertTo =
                revertTarget(key, modifications, processBaseline, catalogue) ?? '';
              return (
                <ProcessOptionRow
                  key={key}
                  option={opt}
                  value={value}
                  revertTo={revertTo}
                  isUserEdited={key in processOverrides}
                  isFileModified={!!modifications?.values && key in modifications.values}
                  showTooltipCaption
                  isExpanded={expandedKey === key}
                  onToggleExpand={() => onToggleExpand(key)}
                  onCommit={(v) => onCommit(key, v)}
                  onRevert={() => onRevert(key)}
                />
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
```

**- [ ] Step 2: Replace `window.confirm` with `AlertDialog`**

In `web/src/components/print/process-all-sheet.tsx`, add an import:

```ts
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader,
  AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
```

Replace the existing reset-all `<Button>` with the AlertDialog wrapper:

```tsx
<AlertDialog>
  <AlertDialogTrigger asChild>
    <Button
      variant="ghost"
      size="icon"
      disabled={Object.keys(processOverrides).length === 0}
      aria-label="Reset all"
    >
      <RotateCcw className="size-4" />
    </Button>
  </AlertDialogTrigger>
  <AlertDialogContent>
    <AlertDialogHeader>
      <AlertDialogTitle>Reset all process settings?</AlertDialogTitle>
      <AlertDialogDescription>
        Every override you've made for this 3MF will be cleared. The 3MF's
        original modifications stay.
      </AlertDialogDescription>
    </AlertDialogHeader>
    <AlertDialogFooter>
      <AlertDialogCancel>Cancel</AlertDialogCancel>
      <AlertDialogAction onClick={resetAllProcessOverrides}>Reset</AlertDialogAction>
    </AlertDialogFooter>
  </AlertDialogContent>
</AlertDialog>
```

**- [ ] Step 3: Type-check**

```bash
cd web && npm run lint
```

Expected: no errors.

**- [ ] Step 4: Commit**

```bash
cd ..
git add web/src/components/print/process-all-sheet.tsx web/src/components/print/process-page-detail.tsx
git commit -m "Add ProcessPageDetail drill-down and Reset-all AlertDialog"
```

---

## Task 12: Wire into `print.tsx` + manual verification

The card slots between `<SlicingSettingsGroup>` and `<FilamentsGroup>`. The sheet renders alongside as a sibling. Lifecycle wiring (3MF parse → reset overrides + fetch baseline) and process-profile-change → re-fetch baseline.

**Files:**
- Modify: `web/src/routes/print.tsx`

**- [ ] Step 1: Find the existing layout structure**

```bash
grep -n "SlicingSettingsGroup\|FilamentsGroup" web/src/routes/print.tsx
```

Note the JSX block where these are rendered, and where `info` (the `ThreeMFInfo` from parse) is in scope.

**- [ ] Step 2: Embed the card and the sheet**

In `web/src/routes/print.tsx`, add imports near the other component imports:

```ts
import { ProcessParametersCard } from '@/components/print/process-parameters-card';
import { ProcessAllSheet } from '@/components/print/process-all-sheet';
```

Inside the `imported` branch JSX, find `<SlicingSettingsGroup ... />` and add the card immediately after it (before `<FilamentsGroup ... />`):

```tsx
<SlicingSettingsGroup ... />
<ProcessParametersCard modifications={info.process_modifications ?? null} />
<FilamentsGroup ... />
```

The sheet is a sibling that lives outside the card stack — it controls its own visibility via `processSheetOpen`. Place it once at the end of the imported branch:

```tsx
<ProcessAllSheet modifications={info.process_modifications ?? null} />
```

**- [ ] Step 3: Wire the lifecycle hooks**

Find the `importFile` callback (around line 215, the function that runs after `parse_3mf` succeeds and stages `setState({ kind: 'imported', ... })`). Add the following at the end of its success path, before the `setState({ kind: 'imported', ... })` call:

```ts
const {
  resetAllProcessOverrides,
  setProcessBaseline,
} = usePrintContext();

// inside importFile, after info is in hand:
resetAllProcessOverrides();
const settingId = info.process_modifications?.processSettingId;
if (settingId) {
  try {
    const baseline = await fetchProcessProfile(settingId);
    setProcessBaseline(baseline);
  } catch {
    setProcessBaseline({});
  }
} else {
  setProcessBaseline({});
}
```

> If `usePrintContext` is already destructured at the top of `PrintRoute`, just add `resetAllProcessOverrides` and `setProcessBaseline` to the existing destructure.

**- [ ] Step 4: Process-profile change baseline refetch**

Find the `onChange` handler of the process picker inside `<SlicingSettingsGroup>` (or wherever `setSettings({ ..., process: ... })` is called). Wrap it so that when `process` changes, the baseline re-resolves:

```ts
async function onProcessProfileChange(nextSettingId: string) {
  setSettings((prev) => ({ ...prev, process: nextSettingId }));
  try {
    const baseline = await fetchProcessProfile(nextSettingId);
    setProcessBaseline(baseline);
  } catch {
    /* keep previous baseline; surfacing handled by per-row tooltips */
  }
}
```

`processOverrides` is intentionally preserved across process-profile changes (sticky user intent — the spec says redundant overrides are harmless server-side).

**- [ ] Step 5: Drop notice on success paths**

Find every place the route handles a successful slice / preview / print response that includes `settings_transfer`. After each success branch, add:

```ts
import { notifyDroppedOverrides } from '@/lib/process/drop-notice';

// inside the success handler:
notifyDroppedOverrides(processOverrides, response.settings_transfer?.process_overrides_applied);
```

If the success handler is shared (e.g. a single `onSliceComplete`), do this once. If it's split, do it in each branch.

**- [ ] Step 6: Type-check + dev server smoke**

```bash
cd web && npm run lint
```

Expected: no errors.

```bash
cd web && npm run dev
```

Then open the dev URL (defaults to `http://localhost:5173`), drop a 3MF file with process modifications, and verify:

- Modified card appears between slicing settings and filaments
- Header badge shows the right count
- Click header → All sheet opens from the right
- Pick a page → drill-down works, breadcrumb updates, back button returns
- Search field filters across pages
- Click a row → inline-expand reveals the right widget
- Edit a value → row's status dot turns orange, badge count updates
- Click Revert → status dot returns to blue (3MF) or empty (default)
- Reset all → AlertDialog confirms; on Reset, overrides clear
- Drop a 3MF without modifications → Modified card shows the empty state, Show all settings still works

**- [ ] Step 7: Run all tests**

```bash
cd web && npm run test
```

Expected: every test from Tasks 3, 8, 9 passes.

**- [ ] Step 8: Type-check the gateway**

```bash
cd .. && .venv/bin/pytest tests/test_options_routes.py tests/test_print_routes_process_overrides.py
```

Expected: same passing baseline as before this branch — we didn't touch gateway code.

**- [ ] Step 9: Manual cross-cutting checks**

Verify each of the following before merging:

- **Mobile (< 640 px viewport)** — Sheet covers viewport, drill-down feels native, all touch targets ≥ 44 px.
- **Reduced motion** — toggle "Reduce motion" in OS / browser DevTools and confirm row expand and Sheet animations snap.
- **Keyboard only** — Tab through card → header → sheet → page list → page detail → row → editor → revert. Every interactive element reachable; focus ring visible.
- **3MF without `process_modifications`** (`null` from older gateway) — empty-state card; All sheet still openable as long as catalogue + layout load.
- **Catalogue / layout 503** — kill `ORCASLICER_API_URL` temporarily and confirm the card and sheet show the retryable error alert; restoring the URL + clicking Retry recovers.

**- [ ] Step 10: Commit**

```bash
git add web/src/routes/print.tsx
git commit -m "Wire process-parameter editor into the print flow"
```

---

## Self-review checklist

Before handing the plan off:

- [ ] Each spec section maps to at least one task. (Section 1 → Task 12; Section 2 → Tasks 2, 4, 6; Section 3 → Tasks 3, 4, 5; Section 4 → Task 9; Section 5 → Tasks 10, 11; Section 6 → Tasks 7, 8; Section 7 → Tasks 6, 11, 12 (manual error/edge sweep); Section 8 → Tasks 8, 9, 10, 11 (visual treatment is built into each component); Testing matrix → Task 1 setup + per-task tests + Task 12 manual checks. Playwright happy path is intentionally deferred — note this limitation in the PR if shipping.)
- [ ] All file paths are absolute under `web/src/...`.
- [ ] Every TypeScript identifier referenced in a later task is defined in an earlier task (e.g. `useProcessOptions` defined in Task 4, used in Tasks 9–11).
- [ ] No "TBD" / "TODO" placeholders.
- [ ] Tests precede implementation in Tasks 3 and 8 (the two TDD-natural ones); Tasks with stateful UI (9, 10, 11) lean on type-check + dev-server smoke + the Task 12 manual sweep instead of brittle async tests.

## Out of scope

- Playwright integration test (deferred — needs a separate "playwright setup" task; mentioned in the spec but not blocking v1).
- Server-side allowlist rendering (deprecated upstream).
- Vector / point editors (`coPoint*`, `coBools` editable variants).
- Persistence of overrides across files / restarts.
- Mode filtering (simple / advanced / develop).
