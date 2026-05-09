# Process Parameter Editor — Web UI Design

Status: design (v1)
Branch: `feat/process-editor-web-ui`
Date: 2026-05-09
Companion specs:
- iOS surface: `../../../bambu-gateway-ios/docs/superpowers/specs/2026-05-07-process-parameter-editor-design.md`
- Gateway endpoints: `./2026-05-08-process-parameter-editor-gateway-design.md`

## Goal

Port the iOS process-parameter editor to the web SPA. Same data flow, same lifecycle, same copy strings, same Modified-card + All-sheet structural split. Adapt only where SwiftUI idioms don't translate cleanly to a React + Vite + TypeScript + Tailwind + shadcn/ui dark-only stack.

The two surfaces:

- A **Modified card** on the print page (`web/src/routes/print.tsx`) summarising what the project author tweaked vs. the system process preset. Inline editing for every row; read-only fallback only for unsupported vector types.
- A right-side **All sheet** exposing the full server-curated parameter catalogue, organised into the layout's pages (Quality / Strength / Speed / Support / Multimaterial / Others) with optgroup sections.

Both surfaces feed a per-3MF override map keyed by option name; submission serialises it as the existing `process_overrides` form field on the gateway's `/api/print*` and `/api/slice-jobs` endpoints.

## Deltas from the iOS spec

The iOS spec is the structural blueprint. These are the points where the web port deviates:

- **Allowlist deprecated.** The slicer no longer ships an editor allowlist; every catalogue key is editable in v1. Drop the `allowlistedKeys: Set<String>` derivation, the lock indicator, and the read-only-locked row variant. Cache key drops `allowlist_revision`. The gateway still proxies the field through; we accept and ignore it.
- **Inline-expand editor.** iOS opens a half-height sheet from the row tap. Web rows expand in place (chevron rotates; row body grows underneath) — no nested modals, single editor open at a time per surface. Used in both the Modified card and the All sheet.
- **Auto-commit, no Save button.** Toggles, selects, sliders, and `mm`/`%` segments commit on change. Numeric and text inputs commit on blur or Enter, clamped to `[min, max]` on commit. Per-row Revert remains. iOS's explicit `Save` button has no equivalent.
- **Right-side Sheet, drill-down inside.** iOS uses `fullScreenCover` with `NavigationStack` push. Web uses shadcn `<Sheet side="right">` at `w-full sm:max-w-[640px] lg:max-w-[720px]`, with internal `selectedPage: ProcessPage | null` state for the page-list ↔ page-detail drill-down. Closes via X / Esc / scrim.
- **TanStack Query** for the long-lived catalogue/layout/profile cache; `usePrintContext` for per-3MF override and baseline state.

Visual treatment is dark-only and reuses existing Tailwind / shadcn tokens. No new colours or font weights introduced.

## Non-goals (v1)

Mirror iOS:

- Persistence of overrides across files or app restarts. State is per-3MF and in-memory only.
- Mode filtering (`simple` / `advanced` / `develop`). Show every option the layout returns; the slicer is the gate.
- Cross-field validation or conditional hiding. The slicer's slice-time validators are the backstop.
- Filament / machine editors.
- Undo/redo stack — per-row Revert plus "Reset all" are sufficient.
- Vector / point editors. `coPoint*`, `coBools`, `coNone` render read-only.
- Surfacing API `version` or `allowlist_revision` in the UI.
- Background prefetch of the option catalogue. Lazy on first card render.

## File structure

**New:**

```
web/src/
  components/print/
    process-parameters-card.tsx        ← Modified-view card on print.tsx
    process-all-sheet.tsx              ← right-side Sheet root + drill-down
    process-page-detail.tsx            ← page detail view inside the Sheet
    process-option-row.tsx             ← shared row + inline-expand editor
    process-option-editor/
      bool-editor.tsx
      number-editor.tsx                ← int / float / percent / float-or-percent
      enum-editor.tsx
      string-editor.tsx
      slider-editor.tsx                ← guiType=slider
      color-editor.tsx                 ← guiType=color
      readonly-vector.tsx              ← coPoint* / coBools / coNone
  lib/api/
    process-options.ts                 ← fetch catalogue, layout, profile baseline
  lib/process/
    types.ts                           ← ProcessOption, ProcessLayout, etc.
    effective-value.ts                 ← pure resolver + revertTarget
```

**Modified:**

```
web/src/
  routes/print.tsx                     ← embed ProcessParametersCard between
                                          slicing-settings-group and filaments-group
  lib/print-context.tsx                ← +processOverrides, +processBaseline,
                                          +setProcessOverride, +revertProcessOverride,
                                          +resetAllProcessOverrides,
                                          +processSheetOpen / setProcessSheetOpen
  lib/api/types.ts                     ← extend ThreeMFInfo with process_modifications;
                                          add process_overrides_applied to settings_transfer
  lib/api/print.ts                     ← +processOverrides on submission builders
  lib/api/slice-jobs.ts                ← +processOverrides on submitSliceJob
```

`shadcn-ui` primitives to install: `switch`, `slider`, `toggle-group`. All other primitives (card, sheet, dialog, alert-dialog, badge, button, input, select, separator, skeleton, alert, popover, tooltip, command, label) are already in the project.

## Data model

All values stringified end-to-end (matching the libslic3r contract). TypeScript mirrors the iOS Swift types one-for-one.

```ts
// lib/process/types.ts

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
  allowlistRevision: string;            // accepted; not consulted client-side
  pages: ProcessPage[];
}

export interface ProcessPage {
  label: string;                        // "Quality" | "Strength" | …
  optgroups: ProcessOptgroup[];
}

export interface ProcessOptgroup {
  label: string;
  options: string[];                    // option keys; metadata via the catalogue
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

Existing types extended:

```ts
// lib/api/types.ts
export interface ThreeMFInfo {
  // …existing fields…
  process_modifications?: ProcessModifications | null;     // older gateway returns null/missing
}

export interface SettingsTransferInfo {
  // …existing fields…
  process_overrides_applied?: ProcessOverrideApplied[];
}
```

## API client

```ts
// lib/api/process-options.ts

export function fetchProcessOptions(): Promise<ProcessOptionsCatalogue>;
// GET /api/slicer/options/process

export function fetchProcessLayout(): Promise<ProcessLayout>;
// GET /api/slicer/options/process/layout

export function fetchProcessProfile(settingId: string): Promise<Record<string, string>>;
// GET /api/slicer/processes/{id} → flat key → string map for the named profile
```

`PrintSubmission` (and the slice-job equivalent) gain `processOverrides?: Record<string, string>`. Builders serialise to a single form field `process_overrides=<JSON>`; field is omitted entirely when the dict is empty. Matches the gateway's `_parse_process_overrides_form` contract.

## State management & lifecycle

Three storage layers.

### Long-lived (TanStack Query)

| Query key | Stale time | gcTime | Notes |
|---|---|---|---|
| `['process-options', 'catalogue']` | `Infinity` | 30 min | ~150 KB. Manual invalidation on `version` change (see below). |
| `['process-options', 'layout']` | `Infinity` | 30 min | `allowlist_revision` parsed but ignored. |
| `['process-options', 'profile', settingId]` | `Infinity` | 30 min | Per-profile baseline, fetched on demand. |

`fetchProcessOptions` and `fetchProcessLayout` compare the response's `version` against the cached payload. On mismatch, `queryClient.setQueryData` replaces the cached payload wholesale. Catalogue and layout share the same `version`; if they ever diverge we trust the layout's version.

503 responses with `code=options_not_loaded` / `code=options_layout_not_loaded` retry once after 1.5 s via TanStack Query's `retry: (count, err) => isRetryableCode(err) && count < 1`. Subsequent failure surfaces a `loadError` to the consuming component.

### Per-3MF (`usePrintContext`)

```ts
type PrintContextValue = {
  // …existing fields
  processOverrides: Record<string, string>;
  processBaseline: Record<string, string>;
  setProcessOverride(key: string, value: string): void;
  revertProcessOverride(key: string): void;
  resetAllProcessOverrides(): void;

  processSheetOpen: boolean;
  setProcessSheetOpen(open: boolean): void;
};
```

Lifecycle hooks:

| Trigger | Effect |
|---|---|
| 3MF parse success | `processOverrides = {}`; kick off `fetchProcessProfile(info.process_modifications.processSettingId)`; store in `processBaseline`. |
| Drop file / switch import | both cleared; sheet closed. |
| User changes process profile in `<SlicingSettingsGroup>` | re-fetch baseline for the new `processSettingId`; `processOverrides` preserved (sticky user intent — redundant overrides are harmless server-side). |
| User clicks **Reset all** in the All sheet | `processOverrides = {}` after AlertDialog confirm. |

### Effective-value resolver (pure, unit-tested)

```ts
// lib/process/effective-value.ts

export function effectiveValue(
  key: string,
  overrides: Record<string, string>,
  modifications: ProcessModifications | null,
  baseline: Record<string, string>,
  catalogue: ProcessOptionsCatalogue | null,
): string | null {
  if (key in overrides)                              return overrides[key];
  if (modifications && key in modifications.values)  return modifications.values[key];
  if (key in baseline)                               return baseline[key];
  return catalogue?.options[key]?.default ?? null;
}

export function revertTarget(
  key: string,
  modifications: ProcessModifications | null,
  baseline: Record<string, string>,
  catalogue: ProcessOptionsCatalogue | null,
): string | null {
  if (modifications && key in modifications.values)  return modifications.values[key];
  if (key in baseline)                               return baseline[key];
  return catalogue?.options[key]?.default ?? null;
}
```

Per-row Revert calls `revertProcessOverride(key)` which deletes the key from `processOverrides`. The row re-derives via the resolver on the next render.

## UI surfaces

### `ProcessParametersCard`

Slots into `print.tsx` between `<SlicingSettingsGroup>` and `<FilamentsGroup>`. Always rendered when `state.kind === 'imported'`. Empty state replaces the row list when `processModifications.modifiedKeys` is empty (and no user overrides exist).

**Anatomy** (shadcn `Card` shell):

```
┌─ Card ─────────────────────────────────────────────────────────┐
│  ⚙  Process settings                          [3 modified ›]  │  ← header (button)
│  ─────────────────────────────────────────────────────────────  │
│  • Layer height                              0.16 mm  ›        │  ← row, collapsed
│  • Sparse infill density                     20 %     ›        │
│  • Top shell layers                          5        ›        │
│                                                                 │
│  [ Show all settings  → ]                                       │  ← tonal button
└─────────────────────────────────────────────────────────────────┘
```

Empty state:

```
┌─ Card ─────────────────────────────────────────────────────────┐
│  ⚙  Process settings                                            │
│              ⎵ No customizations from default profile           │
│  [ Show all settings  → ]                                       │
└─────────────────────────────────────────────────────────────────┘
```

- **Header**: clickable `role="button"`, full keyboard nav. Title `Process settings` + `Settings2` Lucide icon at 14 px tinted accent. Trailing: `<Badge variant="secondary">{n} modified</Badge>` (hidden when `n === 0`) + `ChevronRight` 12 px. Whole header opens the All sheet via `setProcessSheetOpen(true)`.
- **Body**: vertical stack of `<ProcessOptionRow>` instances, one per `modifiedKeys` in API order. Rows separated by `<Separator className="opacity-40" />`. Tooltip caption is omitted in the Modified card to keep density tight (shown only in the All view).
- **Footer**: `<Button variant="secondary">Show all settings →</Button>`. Same target as header click. Always present, even on the empty state — that's the only entry point to the catalogue.
- **Loading**: while catalogue / layout / baseline are still in-flight, render a 3-row `<Skeleton>` block for the body.
- **Error**: on `loadError`, body shows `<Alert variant="destructive">` with `Couldn't load process settings — Retry` and a Retry button that invalidates the relevant queries.
- **Inline-expand state**: lifted via `expandedKey: string | null` on the card. Opening a row auto-collapses any other expanded row in the same surface.

### `ProcessAllSheet`

shadcn `<Sheet side="right">` with `SheetContent className="w-full sm:max-w-[640px] lg:max-w-[720px]"`. Closes via X / Esc / scrim click. Open state lives in `usePrintContext` (`processSheetOpen`).

Two internal modes driven by local `selectedPage: ProcessPage | null` state.

**Mode A — page list** (`selectedPage === null`):

```
┌─ Sheet ─────────────────────────────────────────────────────────┐
│  Process settings                              [↺ Reset all] ✕ │
│  ───────────────────────────────────────────────────────────── │
│  🔍 Search settings…                                            │
│                                                                 │
│  Quality                  44 options · 2 edited      ›          │
│  Strength                 38 options                  ›          │
│  Speed                    31 options · 1 edited      ›          │
│  Support                  47 options                  ›          │
│  Multimaterial            22 options                  ›          │
│  Others                   29 options                  ›          │
└─────────────────────────────────────────────────────────────────┘
```

- **Header**: title `Process settings` (`text-base font-semibold`); shadcn `<SheetClose>` X (right slot); icon button `<RotateCcw>` labelled "Reset all". Disabled when `Object.keys(processOverrides).length === 0`. Click opens an `<AlertDialog>` — *"Reset all process settings?"* / `Reset` / `Cancel` — before clearing.
- **Search**: controlled `<Input>` with leading `Search` icon. Case-insensitive substring match on each option's `label` and `key`. While the query is non-empty, page rows hide and a flat result list takes their place: one `<ProcessOptionRow>` per match, no optgroup headers, with the parent page label as a `text-xs text-muted-foreground` chip on the right side ("Quality"). Tapping a result expands it inline same as anywhere else.
- **Page rows**: `min-h-12`, full keyboard nav. Layout: label (left) · trailing meta block (right). Meta is `${optionsCount} options` always; appended `· ${editedCount} edited` in `text-orange-500 text-xs font-semibold` when that page has any edited options. Trailing `ChevronRight` 12 px. Click → `setSelectedPage(page)`.
- Edited count derived once via `useMemo` from `processOverrides` keys × `layout.pages[i].optgroups[*].options`.

**Mode B — page detail** (`selectedPage !== null`):

```
┌─ Sheet ─────────────────────────────────────────────────────────┐
│  ‹ Process settings  /  Quality                ✕               │
│  ───────────────────────────────────────────────────────────── │
│  LAYER HEIGHT                                                   │
│  • Layer height                              0.16 mm  ›         │
│  • First layer height                        0.20 mm  ›         │
│                                                                 │
│  WALL                                                           │
│    Wall loops                                  3      ›         │
│    Detect thin wall                           on      ›         │
│  …                                                              │
└─────────────────────────────────────────────────────────────────┘
```

- **Header**: `ChevronLeft` button (back to mode A) + breadcrumb `Process settings / ${page.label}`. Reset-all button stays in the trailing slot. Search field hidden in detail mode (search is global; user goes back to use it).
- **Body**: scrollable. One section per `optgroup`; section header is `optgroup.label.toUpperCase()` rendered `text-xs font-semibold tracking-wide uppercase text-muted-foreground` with `pb-2 pt-4`. Rows are `<ProcessOptionRow>` in layout order — no client-side sorting.
- **Tooltip caption**: rendered as a single-line caption (`text-xs text-muted-foreground line-clamp-1`) under the label on every row in this view. Mirrors iOS All-view density.

**Drill-down animation**: 180 ms ease-out, 8 px horizontal slide + crossfade on `selectedPage` change. Respect `motion-safe:`. Closing the Sheet always returns to mode A on next open.

**Mobile (`< 640px`)**: `w-full` covers the viewport. The two modes feel native (page list ≡ master, page detail ≡ pushed). Back chevron doubles as drill-up.

### `ProcessOptionRow`

Shared row, used in the Modified card, the page detail, and the search results list.

#### Collapsed

```
│ • Layer height                                       0.16 mm  ›  │
```

Three-column flex (`gap-3`):

| Column | Content | Tailwind |
|---|---|---|
| Leading gutter (12 px) | Status dot (8 px filled circle): `bg-orange-500` if user-edited; else `bg-sky-500` if 3MF-modified; else empty. User-edited wins over 3MF-modified when both apply. | `flex w-3 items-center justify-center` |
| Center | Label (`text-sm`). All-view only: second line tooltip first sentence (`text-xs text-muted-foreground line-clamp-1`). | `flex-1 min-w-0` |
| Trailing | Effective value (`text-sm font-medium tabular-nums`) + sidetext suffix (`text-xs text-muted-foreground ml-1`) + `ChevronRight` 12 px (rotated 90° when expanded). | `flex items-center gap-1` |

- Whole row is a `<button>` with `data-state={expanded ? 'open' : 'closed'}`, `aria-expanded`, focus ring (`ring-2 ring-ring`), `hover:bg-accent/30`, `active:bg-accent/50`. `min-h-11` (≥ 44 px touch target).
- Read-only types (`coPoint*`, `coBools`, `coNone`): chevron suppressed; click still expands but the editor body shows only the read-only-vector view.

#### Expanded

```
│ • Layer height                                       0.16 mm  ⌄ │
│ ──────────────────────────────────────────────────────────────  │
│   Distance between the layers, controls vertical resolution.   │  ← tooltip
│   [More]                                                        │  ← only if truncated
│                                                                 │
│   ┌────────────────────────────────┐                            │
│   │  0.16                       mm │                            │  ← editor widget
│   └────────────────────────────────┘                            │
│   Range 0.05–0.75 mm                                            │  ← range hint
│   ⚠ Must be ≥ 0.05 mm                                           │  ← validation, on invalid only
│                                                                 │
│   From file: 0.20 mm           [↺ Revert]                       │  ← footer
```

Padding `px-3 py-3 pl-6` (label-aligned indent), `gap-2` between blocks.

| Block | Detail |
|---|---|
| Tooltip | `text-sm text-muted-foreground` with `line-clamp-2`. If truncated, a `<Button variant="link" size="sm">More</Button>` toggles full text inline. |
| Widget | Per-type, see table below. Receives effective value as initial; commits via `setProcessOverride(key, stringified)`. |
| Range hint | `text-xs text-muted-foreground`. Rendered only when at least one of `min` / `max` is non-null. Format: `Range ${min}–${max} ${sidetext}`. |
| Validation | `text-xs text-destructive` with `<AlertTriangle>` 14 px icon. Shown on blur, not per keystroke. Auto-commit suppressed while invalid; the row keeps the invalid draft locally. |
| Footer | Flex row, `justify-between`. **Leading**: `From file: ${revertTarget} ${sidetext}` (3MF-modified) or `Default: ${revertTarget} ${sidetext}` (otherwise) in `text-xs text-muted-foreground`. **Trailing**: `<Button variant="ghost" size="sm">` with `RotateCcw` icon + `Revert`. Disabled when `key ∉ processOverrides`. |

Animation: CSS Grid `grid-template-rows: 0fr → 1fr` transition (the standard shadcn-friendly pattern), `duration-200 ease-out`, `motion-reduce:transition-none`.

Only one row is expanded at a time per surface. Lifted via `expandedKey: string | null` on the parent.

### Per-type widget mapping

| `type` (+ `guiType`) | Widget | shadcn dep | Commit value |
|---|---|---|---|
| `coBool` | `<Switch>` aligned right | `switch` (NEW) | `'1'` / `'0'` |
| `coInt`, `coInts` | `<Input type="number" inputMode="numeric" step="1">` with `−` / `+` stepper buttons; clamped on commit | `input`, `button` | integer string |
| `coFloat`, `coFloats` | `<Input type="number" inputMode="decimal" step="any">` + sidetext suffix; clamped on commit | `input` | decimal string |
| `coPercent`, `coPercents` | Same as float; suffix locked to `%` | `input` | `'<n>%'` |
| `coFloatOrPercent`, `coFloatsOrPercents` | `<ToggleGroup type="single">` `mm`/`%` + numeric `<Input>`. Default segment from parsed input. | `toggle-group` (NEW), `input` | `'<n>'` or `'<n>%'` |
| `coString`, `coStrings`, `guiType=one_string` | `<Input type="text">` | `input` | string |
| `coEnum` | `<Select>` over zipped `enumValues` × `enumLabels` | `select` | enum value |
| `guiType=color` | `<Input type="color">` (native), value bridged to libslic3r-style `#rrggbb` | `input` | `'#rrggbb'` |
| `guiType=slider` (with `min`/`max`) | `<Slider>` + companion `<Input type="number">` (kept in sync) | `slider` (NEW), `input` | numeric string |
| `coPoint`, `coPoints`, `coPoint3`, `coBools`, `coNone` | Read-only `<code className="text-xs">` of raw value + banner *"Editing this option type is not yet supported."* | none | not editable |

**Auto-commit timing:**

- `Switch`, `Select`, `ToggleGroup` (`mm`/`%`), color picker, slider drag end → on change.
- Numeric inputs, text inputs → on blur or Enter; clamped to `[min, max]` (numeric) before commit.
- Slider drag while held → updates local draft only; commits on `pointerup`.
- Stepper buttons (`±` on `coInt*`) → commit immediately, clamped.

**Revert** removes the key from `processOverrides`; the row re-derives its effective value; the modified marker reverts to `bg-sky-500` (3MF-modified) or empty (was a default).

## Submit & response handling

### Building the submission

In the existing `print.ts` / `slice-jobs.ts` builders:

```ts
const overrides = Object.keys(processOverrides).length ? processOverrides : undefined;
if (overrides) fd.append('process_overrides', JSON.stringify(overrides));
```

Empty dict → field omitted. Mirrors the existing `filament_profiles` form-field pattern.

### Reading the response

`/api/print-preview`, `/api/print`, and `/api/slice-jobs` return a `settings_transfer` block with optional `process_overrides_applied`. After a successful slice:

```ts
const sent = Object.keys(processOverrides);
const applied = (response.settings_transfer?.process_overrides_applied ?? []).map(o => o.key);
const dropped = sent.filter(k => !applied.includes(k));

if (dropped.length > 0) {
  toast.message(
    `${applied.length} setting(s) sent, ${dropped.length} ignored: ${dropped.join(', ')}`,
  );
}
```

Uses the existing Sonner instance. Non-blocking, informational. Dropped overrides remain in `processOverrides` (they're harmless and the slicer keeps filtering them) — we don't pre-clean them client-side. Mirrors iOS verbatim.

## Error states

| Condition | Surface |
|---|---|
| Catalogue or layout fetch fails (network / 5xx / decode) | Modified card body and All sheet body show `<Alert variant="destructive">` with `Couldn't load process settings — Retry` and a Retry button that invalidates the relevant queries. Slice / print path keeps working — submitting with no overrides is unaffected. |
| `info.process_modifications` missing on an older gateway | Modified card renders the empty state; `Show all settings` still works as long as catalogue + layout load. |
| Catalogue is missing a key referenced by `modifiedKeys` | Row renders with the raw key as label (mono font hint), treated as read-only, value displayed as-is. Don't crash. |
| Layout references a key the catalogue lacks | Skip the row silently. Catalogue is authoritative for metadata. |
| Numeric input outside `min` / `max` | Editor clamps on commit and shows the violation message inline. No toast. |
| Stale process profile after a profile swap mid-session | Baseline re-fetched for the new `processSettingId`; overrides preserved. |
| 3MF without `project_settings.config` | `processModifications = { processSettingId: '', modifiedKeys: [], values: {} }`. Empty state; baseline fetch skipped. |
| Unsupported v1 type (vector / point / `coNone`) | Render read-only with the unsupported banner. `console.debug` logs key + type so we notice when the layout grows. |

## Edge cases

- **Catalogue missing a key referenced by `modifiedKeys`** → render with raw key as label, read-only, value displayed as-is.
- **Layout references a key the catalogue lacks** → skip the row silently.
- **Numeric input outside `min`/`max`** → clamp on commit; one-line violation message.
- **Stale process profile after a profile swap mid-session** → baseline re-resolves; overrides stay sticky.
- **Concurrent in-flight fetches** → coalesced by TanStack Query.
- **3MF without `project_settings.config`** → empty modifications block; empty-state card.
- **Sheet open while a new 3MF is dropped** → sheet stays open but the page-list edited counts and the Modified-card content re-render against the new modifications + cleared overrides.

## Versioning & cache invalidation

- `version` (in catalogue + layout) keys the option metadata. Replaced wholesale on change.
- `allowlistRevision` parsed but not consulted client-side (allowlist deprecated; gateway proxies for now).
- No persistence of either — in-memory only in v1. Catalogue (~150 KB, ~609 entries) is small enough to refetch on relaunch.

## Visual specification

Dark-only. Reuses existing Tailwind / shadcn tokens. No new colours or font weights introduced.

### Tokens

| Role | Token | Use |
|---|---|---|
| Card surface | `bg-card` | Modified card and Sheet body |
| Surface raised on hover | `bg-accent/30` | Row hover |
| Surface raised on press | `bg-accent/50` | Row active |
| Primary text | `text-foreground` | Labels, values |
| Secondary text | `text-muted-foreground` | Sidetext, tooltip caption, range hint, footer revert target, page-row meta count |
| Accent (3MF-modified) | `bg-sky-500` | 8 px modified dot |
| Accent (user-edited) | `bg-orange-500` | 8 px edited dot, edited count chip |
| Destructive | `text-destructive` | Validation message, error alert |
| Focus ring | `ring-ring` | All interactive rows / buttons / inputs |
| Separator | shadcn `<Separator>` at `opacity-40` | Between rows in the Modified card |

### Typography

| Role | Classes |
|---|---|
| Card title / Sheet title | `text-base font-semibold` |
| Modified badge | shadcn `<Badge variant="secondary">` (default sizing) |
| Page row label | `text-sm` |
| Page row meta | `text-xs text-muted-foreground` |
| Optgroup section header | `text-xs font-semibold tracking-wide uppercase text-muted-foreground` |
| Row label | `text-sm` |
| Row value | `text-sm font-medium tabular-nums` |
| Row sidetext | `text-xs text-muted-foreground` |
| Tooltip caption (All view only) | `text-xs text-muted-foreground line-clamp-1` (collapsed) / `line-clamp-2` (expanded) |
| Range hint | `text-xs text-muted-foreground` |
| Footer revert target | `text-xs text-muted-foreground` |

### Spacing & dimensions

- Modified card: `p-4` outer, rows `px-3 py-2.5` (`min-h-11`).
- Inline-expand body: `px-3 py-3 pl-6` (label-aligned indent), `gap-2` between blocks.
- All sheet content area: `px-4 py-3`. Optgroup spacing: `pb-2 pt-4` for headers, `py-2` between rows. Page-row `min-h-12`.
- Page-list / page-detail crossfade: 180 ms ease-out, 8 px horizontal slide.
- Row expand / collapse: 200 ms ease-out, CSS-grid `grid-template-rows` transition.
- All animations gated by `motion-safe:`; reduced-motion users see snaps.

### Iconography (Lucide)

| Use | Icon | Size |
|---|---|---|
| Card title leading | `Settings2` | 14 px, accent |
| Show-all chevron / row chevron | `ChevronRight` | 12 px (rotated for expand) |
| User-edited dot | `<span className="size-2 rounded-full bg-orange-500" />` | 8 px |
| 3MF-modified dot | `<span className="size-2 rounded-full bg-sky-500" />` | 8 px |
| Revert button | `RotateCcw` | 14 px |
| Reset-all toolbar | `RotateCcw` | 16 px |
| Search field | `Search` | 16 px |
| Back (page detail) | `ChevronLeft` | 16 px |
| Close (Sheet) | shadcn default X | — |
| Empty state | `SlidersHorizontal` | 28 px, `text-muted-foreground` |
| Validation | `AlertTriangle` | 14 px, destructive |

## Accessibility

- Every collapsed row exposes `aria-label="${label}, ${value} ${sidetext}"` and `aria-expanded`. Status dots are `aria-hidden`; status is conveyed via `aria-description` (`"modified by file"` / `"edited by you"`).
- Tooltip text is rendered as visible body text (not in a hover Tooltip) so screen readers reach it without focus tricks.
- Editor widgets use shadcn defaults (already AA-conformant); validation messages are `role="alert"` with `aria-live="polite"`.
- Keyboard: Tab traverses rows in DOM order (matches visual). Space / Enter toggles row expansion. Esc collapses the expanded row, then closes the Sheet on a second press. Arrow keys inside Slider / Select / ToggleGroup behave per shadcn defaults.
- Reduced motion: row expansion, page-list crossfade, and Sheet slide-in all gated by `motion-safe:`. With reduced motion, transitions snap.
- Sheet traps focus; Esc and X dismiss; focus returns to the trigger button on close.

## Copy

Verbatim from iOS spec — single source of truth.

| Surface | Copy |
|---|---|
| Card title | "Process settings" |
| Modified badge (`n>0`) | `${n} modified` |
| Empty state body | "No customizations from default profile" |
| Card "Show all" button | "Show all settings" |
| Sheet title | "Process settings" |
| Sheet search placeholder | "Search settings" |
| Reset-all toolbar tooltip | "Reset all" |
| Reset confirmation | "Reset all process settings?" / `Reset` / `Cancel` |
| Revert footer (3MF-modified) | `From file: ${value}${sidetext}` |
| Revert footer (default) | `Default: ${value}${sidetext}` |
| Vector unsupported banner | "Editing this option type is not yet supported." |
| Loading error | "Couldn't load process settings — Retry" |
| Drop notice | `${applied} setting(s) sent, ${dropped} ignored: ${joinedKeys}` |
| Validation min | `Must be ≥ ${min} ${sidetext}` |
| Validation max | `Must be ≤ ${max} ${sidetext}` |
| Validation parse | `Enter a valid ${type} value` |

## Testing

### Vitest unit

- `lib/process/effective-value.ts` — table-driven cases over the four-rung resolver chain (override → modifications → baseline → catalogue default → null).
- `revertTarget` — same chain minus overrides; covers 3MF-modified and unmodified keys.
- Submission builder — empty `processOverrides` omits the form field; non-empty serialises verbatim.
- TanStack Query invalidation — `version` change in a fresh response replaces cached catalogue / layout; same `version` keeps the cache.

### React Testing Library / component

- `<ProcessOptionRow>`: renders correct widget per `(type, guiType)`; status dot reflects override / modification state; auto-commit timing per control type; revert clears override and restores effective value; clamp-on-commit for numeric inputs; validation message on out-of-range; read-only banner for vector types.
- `<ProcessParametersCard>`: empty state vs row list; one-row-expanded-at-a-time invariant; loading skeleton and error alert paths.
- `<ProcessAllSheet>`: page-list ↔ page-detail drill-down; search filters across all options globally; reset-all confirms via AlertDialog and clears overrides; edited-count badge per page.

### Playwright integration (one happy path)

- Import a 3MF with `process_modifications`, observe the Modified card populating; expand a row, change `layer_height`, verify the slice request body carries `process_overrides`, and the response's `process_overrides_applied` does not produce a toast (silent success). Drop a 3MF with no modifications, verify the empty-state copy.

### Manual / cross-cutting

- Mobile (`< 640px`) — Sheet covers viewport, drill-down feels native, all touch targets ≥ 44 px.
- Reduced-motion — row expand and Sheet animations snap; no behaviour regressions.
- Keyboard-only — full traversal across card → header → All sheet → page list → page detail → row → editor → revert.
- 3MF with no `process_modifications` (`null` from older gateway) — empty state, no errors, All sheet still openable as long as catalogue + layout load.

## Open questions

None blocking the implementation plan.
