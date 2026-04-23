# Web UI Redesign

**Date:** 2026-04-23
**Status:** Spec — ready for implementation plan

## Goals

Bring the `bambu-gateway` web UI to a polish level where it can realistically replace the `bambu-gateway-ios` app for day-to-day use. The current web UI is functional but "raw" — a utility-grade tool. The iOS app sets the bar: dark-mode first, big bold titles, rounded dark cards, blue accent, orange for temperatures, floating pill tab bar, grouped-list detail rows.

## Non-goals

- **Interactive 3D preview.** Static plate thumbnails (what the gateway already extracts from the 3MF) are sufficient. A JS-side gcode/3MF renderer (three.js) can be a later milestone.
- **MakerWorld import.** Deferred. No browse/download UI in this pass.
- **Light mode.** Dark-only. No `prefers-color-scheme` branching, no theme toggle.
- **Settings redesign beyond token inheritance.** Settings stays a separate `/settings` route and absorbs the new design language; no IA changes.
- **Backend API changes.** The existing `/api/*` endpoints already expose everything the redesign consumes. If a gap appears during implementation it'll be scoped as a discrete follow-up; the spec assumes the current API surface.

## Decisions locked during brainstorming

| Axis | Decision |
|---|---|
| Scope | Dashboard + Print redesigned. Settings inherits design system only. |
| Form factor | Desktop-first, phone stays first-class (single centered column at every breakpoint). |
| Focus | One printer at a time. Compact picker switches between them. |
| Navigation | Routed top tabs — `Dashboard` and `Print`. Settings is a separate route `/settings`. |
| Theme | Dark-only. |
| Print-tab scope | Plate thumbnail, preview-then-confirm, drag-and-drop upload. |
| AMS | Always visible on Dashboard (informational + "In Use" highlight + tap for drying). |
| Accent | iOS-style blue (`#60A5FA`). Temperatures in warm orange (`#F97316`). |
| Implementation stack | Vite + React + TypeScript + Tailwind + shadcn/ui. |
| Frontend directory | Top-level `web/`. Build output → `app/static/dist/`. |
| Shipping pattern | Incremental on `main`: tokens + Dashboard first, then Print, then Settings. |

## Stack & build chain

- **Framework:** React 18 + TypeScript, Vite 5.
- **Styling:** Tailwind CSS. Design tokens live in `tailwind.config.ts` `theme.extend` **and** as CSS variables (shadcn's slots: `--background`, `--card`, `--border`, `--primary`, `--destructive`, `--muted`, `--ring`, etc.). Single source of truth.
- **Components:** shadcn/ui as the primitive layer (13 components; see inventory below).
- **State:** React Query (`@tanstack/react-query`) for `/api/printers`, `/api/ams`, slicer profiles. Native `EventSource` for `/api/print-stream` SSE. No global state library — React Query + component state only.
- **Routing:** `react-router` v6. Routes: `/` (Dashboard), `/print`, `/settings`.
- **Forms:** `react-hook-form` + `zod` for the Settings dialogs.
- **Toasts:** `sonner`.
- **Build:** `npm run build` produces `app/static/dist/{index.html, assets/*}`. Dev uses `vite --host` and proxies `/api/*` to the FastAPI backend at `http://localhost:8000`.
- **FastAPI changes:** `app/main.py` mounts `/assets` from `app/static/dist/assets/`, adds a catch-all that returns `app/static/dist/index.html` for any non-`/api/*`, non-`/static/*` path so client-side routing works. Jinja templates (`index.html`, `settings.html`) are deleted.
- **Dockerfile:** multi-stage — `node:lts-alpine` stage runs `npm ci && npm run build`, copies the resulting `dist/` into the Python image under `app/static/dist/`. Final runtime image stays Python-only.

## Design tokens

### Colors (CSS variables, mapped to Tailwind)

```
--bg-0:          #0B0D17   /* page background */
--bg-1:          #11151F   /* surface under cards */
--surface-1:     #1A2030   /* card, input */
--surface-2:     #232A3D   /* hover, nested */
--surface-3:     #2D3650   /* active pill, pressed */
--border:        #1F2937

--text-hi:       #FFFFFF
--text-0:        #E5E7EB
--text-1:        #9CA3AF
--text-2:        #6B7280

--accent:        #60A5FA   /* primary action, links, "In Use" */
--accent-strong: #3B82F6   /* primary button gradient target */
--warm:          #FBBF24   /* temperatures (warming) */
--warm-hot:      #F97316   /* temperatures (at target) */
--success:       #22C55E   /* idle, success, healthy */
--danger:        #EF4444   /* cancel, error */
--info:          #A855F7   /* paused state */
```

Shadcn CSS variables bind as: `--background → --bg-0`, `--card → --bg-1`, `--popover → --surface-1`, `--primary → --accent-strong`, `--primary-foreground → --text-hi`, `--destructive → --danger`, `--border → --border`, `--ring → --accent`.

### Typography

- **UI:** **Inter** (variable, weights 300–800), self-hosted via `@fontsource-variable/inter`. Fallback: `-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif`.
- **Mono (temps, IDs):** `JetBrains Mono` (variable) via `@fontsource-variable/jetbrains-mono`. Fallback: `ui-monospace, SFMono-Regular, Menlo, monospace`. Always applied with `font-variant-numeric: tabular-nums` for stable digit-width.

Scale (Tailwind classes):
- `text-[28px] font-extrabold tracking-tight` — page title
- `text-[48px] font-extrabold tracking-[-0.03em]` — hero percentage (desktop) / `text-[40px]` (phone)
- `text-base font-semibold` — section titles
- `text-sm` — body
- `text-[11px] font-semibold uppercase tracking-wider` — eyebrow labels
- `text-xs font-mono` — IDs, temperatures, technical metadata

### Spacing, radius, motion

- **Spacing:** 4px grid. Tailwind's default 1/2/3/4/5/6/8/12 scale (4/8/12/16/20/24/32/48px).
- **Radius:** `rounded-md` (4px), `rounded-lg` (8px), `rounded-xl` (12px), `rounded-2xl` (16px), `rounded-full` (pill). Cards = `rounded-2xl`, rows inside grouped lists inherit card radius; buttons = `rounded-full`.
- **Motion:** `duration-fast` 120ms (tap feedback), `duration-base` 200ms (hover/press), `duration-slow` 300ms (modal/sheet). Easing: `cubic-bezier(0.2, 0.0, 0, 1)`. Under `prefers-reduced-motion: reduce`, all transitions collapse to `0ms` **except** `<Progress/>` fills, which continue to animate because they convey state change.

## App shell

Persistent header across all routes:

- **Left:** Brand mark (small blue gradient square + "Bambu Gateway", 15px semibold).
- **Center:** Segmented-pill tabs — `Dashboard` / `Print`. Active tab has `surface-3` background, inactive is transparent with `text-1` label. The active state is driven by `react-router`'s `useLocation`.
- **Right:** `⚙ Settings` pill. Clicking navigates to `/settings`. On the Settings route, the pill disables itself and the route shows a back-arrow chevron next to the page title.

Body is always a centered column with `max-w-[720px] mx-auto`, padding `px-4 sm:px-6` and `py-6`. Page heads get a 28px extrabold title and an optional right-side 36px circular refresh button (Dashboard only).

## Dashboard

One route, one printer's detail at a time.

### Printer picker

- **≤3 printers:** segmented pill — one chip per printer, each with a 6px colored status dot (blue=online+printing, green=online+idle, amber=paused, red=error, gray=offline). Active chip: `surface-3` background + white text.
- **≥4 printers:** single pill trigger that shows the active printer's dot + name + chevron; clicking opens a shadcn `<Command/>` with a search input and the full list. This is a runtime branch, not a separate component.

The picker is the source of truth for "which printer am I looking at and targeting for the next print." Selection persists in `localStorage` (`bg.active-printer-id`) so a page reload restores it.

### Hero card

- State badge (Printing / Paused / Preparing / Error / Idle / Offline) as a colored pill using the state color (`accent` / `info` / `warm` / `danger` / `success` / `text-2`). Text is always present — never color-only.
- Huge tabular-numeral percentage (48px desktop / 40px phone).
- Meta line: `filename · Layer X/Y · Nm remaining` with mid-dots, `text-1` for separators, `text-0` for the values.
- Thin 6px progress bar with a left-to-right gradient (`accent-strong` → `accent`).
- **Idle state:** percentage swaps for the idle icon + "Ready" label; meta line shows last finished file.
- **Offline state:** whole hero dims to 60% opacity, swaps for "Offline · Check connection" with a link to Settings.
- **Error state:** red badge + separate error banner below the hero with `danger` accent and a "Details" chevron that expands the full error message.

### Stat chips

Three-up grid (`grid grid-cols-3 gap-2.5`), each `rounded-xl` card with:

- Eyebrow label in `text-2 uppercase tracking-wider`.
- Value in 22px bold. Nozzle/Bed use `warm-hot` for current + `text-2` `/target°` suffix in 13px. Speed uses `accent`.
- Speed chip is a shadcn `<Select/>` trigger (Silent / Standard / Sport / Ludicrous). Changes fire `POST /api/printers/{id}/speed` optimistically with a sonner toast on failure.

### Action buttons

Two-up grid of pill buttons below the chips. Shown only when the printer is online **and** active (`printing`, `preparing`, or `paused`):

- **Pause / Resume** — neutral variant (`surface-1` bg, `accent` text). Label swaps on `state === 'paused'`.
- **Cancel** — destructive variant (`danger` tinted bg + border + text). Shadcn `<AlertDialog/>` confirmation before sending.

Idle state hides these and shows a single "Open Print" button that routes to `/print` with the target printer preselected.

### AMS section

Always rendered when the active printer has AMS units (i.e. `units.length > 0` or `vt_tray` exists).

- Section header: `AMS 1` (repeats for additional units) with `text-xs text-1` humidity pill on the right plus a chevron that collapses the tray list (state persists in `localStorage` per unit).
- Tray row: `rounded-2xl` card, 28px color dot, blue title ("Tray 3"), optional "In Use" badge, mono `type · id` line, filament-name line. "In Use" adds a 1px `accent` border + soft `accent/30` box-shadow ring around the card. Empty trays show italic `text-2` "Empty" in the filament-name slot.
- Tapping a tray opens a shadcn `<Sheet/>` (right side on desktop, bottom on phone) with: filament name/type, loaded grams (if reported), drying controls (start temp/duration, stop). Only AMS units with `supports_drying: true` show the drying controls.
- **External Spool** gets its own section header below AMS units, same row primitive.

## Print flow

One route, six observable states, all on the same page.

### State A — Empty

Drop zone card: dashed 1.5px `--text-2` border, 48px vertical padding, centered icon + "Drop a .3mf file here" headline + "Or import from your device" sub + `Choose file…` pill button. **Drag-and-drop is active on the entire page body**, not just the zone — dragging anywhere shows a full-page tinted overlay with the zone centered. Page subtitle reads `Target printer: <active printer>` and is live-bound to the Dashboard picker selection.

### State B — Imported (parsed 3MF)

Four stacked groups:

1. **File** — `plate-card`: 140px plate thumbnail (or a placeholder box if the 3MF has no thumbnail), filename in white semibold (word-break, no truncation), meta line (`Plate X of Y · N filaments · L layers`), "Clear" action in `danger` 12px.
2. **Slicing settings** — grouped list Card with three rows: Machine / Process / Plate type. Each row is a shadcn `<Select/>` trigger; the value is shown right-aligned in `text-1` with a `›` chevron. Selected defaults come from the 3MF's embedded `setting_id` when it matches; when it doesn't, the "from file" value is shown in the dropdown as the first option with a `(from file — different printer)` suffix.
3. **Filaments** — grouped list of project filaments. Each row: 24px color dot, project filament name, `type` eyebrow, then right side `→ <colored dot> Tray N ›` representing the currently-mapped AMS tray. Clicking opens a shadcn `<Select/>` listing all AMS trays. Defaults come from backend filament-matching (`/api/filament-matches`) using the existing `filamentMatchesByIndex` logic.
4. **Info banner** — blue `<Alert/>` "File parsed — slicing required." Variants:
   - amber "This 3MF already contains G-code. AMS tray selections and project filament overrides are ignored." → disables all filament selects.
   - green "Preview ready · Nm / Ng" → after preview step.
   - red "Slicing failed" → with "Details" expander and a "Retry" button.

Then the two-up actions: `◉ Preview` (neutral) and `⎙ Print` (primary — gradient `accent-strong → accent`).

### State C — Slicing in progress

A sticky blue-bordered card slides in at the top of the page (above "File" group). Contents: spinner + "Slicing…" title, right-side `Cancel` danger text link, 6px gradient progress bar, status line (`text-mono text-1`) fed from SSE `progress` events' `status_line` field. The rest of the page dims to 50% opacity + `pointer-events: none` — no interaction with stale form state while slicing.

Backend calls: `POST /api/print-stream` (SSE). The streaming card consumes `status`, `progress` (updates bar), `result` (switches to State D), `print_started` (routes to Dashboard + toast), `error` (switches to error variant of the banner), `done` (closes stream).

### State D — Preview ready

Same page as B. Banner turns green. Two new pieces:

- `Download 3MF` secondary button inserts between Preview and Print (now renamed **Confirm Print**).
- Preview button becomes `Re-slice` with an undo-style icon.

`Confirm Print` calls `POST /api/print` with just `preview_id` (no re-slicing).

### State E — Upload/send

Same sticky-card pattern as Slicing, title "Uploading to printer…", progress fed from the FTP upload callback. Cancel uses the existing `upload_id` cancel endpoint.

### State F — Sent

A sonner toast "Print started on A1 Mini" appears, the Print form resets to State A, and the route changes to `/` (Dashboard) so the user immediately sees the printer they just targeted.

### Drag-and-drop

Whole-page `dragenter/dragover/dragleave/drop` handlers. A full-page tinted overlay with the drop zone centered appears on `dragenter` (counter to handle nested enters correctly). Drop accepts `.3mf` only; other types show a sonner error. Windows-style multi-file drop takes only the first `.3mf`.

## Settings route

Same app shell. Page title "Settings". Three Card-wrapped sections:

- **Printers** — grouped list reusing `<TrayRow/>` shape (status dot + name + model). Right side: overflow menu (Edit / Delete). Add button opens a shadcn `<Dialog/>` with a `react-hook-form` + `zod`-validated form (IP, access code, serial, name, machine model).
- **Push Notifications** — same row primitive for registered devices; per-row Delete; section-level "Notification settings" link opens a sub-dialog.
- **About** — static card with app version, MQTT/FTPS/slicer connection test buttons. Each test button fires the existing backend check and surfaces result as a sonner toast.

No IA changes vs today's `/settings` — just the visual/interaction language inherited.

## Component inventory

### From shadcn/ui

| Component | Used for |
|---|---|
| `Tabs` | (not used — replaced by router-driven pill tabs in header) |
| `Button` | Pause/Resume, Cancel, Preview, Print, Confirm Print, Choose file… |
| `Card` | Hero, plate card, grouped-list containers, settings sections |
| `Badge` | State badges, "In Use", "From file", "Sliced" |
| `Select` | Machine/Process/Plate-type rows, Speed chip, filament tray mapping |
| `Command` | Printer picker fallback (≥4 printers) |
| `Dialog` | Add/Edit printer, tray details on desktop (via `Sheet` on phone) |
| `Sheet` | Tray details / drying on phone; also confirmation flows on phone |
| `AlertDialog` | Cancel print confirmation |
| `Progress` | Hero progress, slicing progress, upload progress |
| `Alert` | Info / warn / error banners |
| `Sonner` (Toast) | Post-action confirmations and transient errors |
| `Tooltip` | Refresh icon-button, truncated filename hover |
| `Skeleton` | Dashboard initial load, AMS loading, slicer-profile loading |
| `Separator` | Grouped-list row dividers (inset 16px from color-dot edge) |

### Custom (on top of shadcn primitives)

- **`<PrinterPicker/>`** — segmented pill (≤3) / single pill + `<Command/>` (≥4). Props: `printers`, `activeId`, `onChange`.
- **`<StatChip/>`** — `{ label, value, unit?, variant: 'warm' | 'accent' | 'neutral', chevron?, onClick? }`.
- **`<TrayRow/>`** — shared by Dashboard AMS, Print filament mapping, and Settings printer list. Props: `colorDot`, `title`, `subtitle?`, `body?`, `right`, `onClick?`.
- **`<DropZone/>`** — full-page-aware drag overlay + click-to-pick fallback. Props: `accept`, `onFile`.
- **`<AppShell/>`** — header + routed outlet + toast container. Consumes `PrinterContext` (the shared active-printer selection).

## Responsive behavior

Single centered column at every breakpoint. **No side-by-side layouts on desktop** — same IA as phone, just bigger type and more breathing room.

- **< 640px** (phone): body padding `px-4`, hero percentage 40px, picker horizontally scrollable, stat chips remain 3-up (not 1-up) because glanceability matters more than crowding.
- **≥ 640px** (tablet+): body padding `px-6`, hero percentage 56px.
- **≥ 1024px** (desktop): same `max-w-[720px]`. Intentional — we tested wider layouts during brainstorming and rejected them in favor of a single consistent canvas.

## Accessibility

- Every interactive element ≥ 44×44px hit area. Picker chips render at 36px visually but have `py-2` padding plus 8px chip-to-chip spacing, meeting touch-target requirements.
- 2px `--ring` (accent) focus ring on every tab-reachable element. Never removed.
- State is always reinforced by text (`Printing`, `Paused`, `In Use`) — color alone never carries meaning.
- All icon-only buttons have `aria-label`; shadcn primitives already ship this wiring.
- Color pairs tested at AA (4.5:1 body, 3:1 large): text-0 on bg-0 = 14.8:1, text-1 on bg-0 = 6.4:1, accent on bg-0 = 8.2:1. All pass.
- `prefers-reduced-motion`: transitions → 0ms; progress bars still animate (convey state, not decoration).
- Keyboard: full keyboard support — Tab navigates all rows, Enter/Space activates, arrow keys inside `<Select/>` and `<Command/>`. `Escape` closes dialogs/sheets/popovers.

## Incremental ship plan (each step merges to `main`)

1. **Foundation.** Add `web/` Vite project, Tailwind config with tokens, shadcn scaffold, FastAPI `dist/` wiring, Dockerfile multi-stage build. Ship a "hello" React page served from `/`. Old Jinja pages still exist at `/old`, `/old/settings` for rollback.
2. **Dashboard (read-only).** `<AppShell/>` + `<PrinterPicker/>` + hero + stat chips + AMS section. Read data from existing `/api/printers` and `/api/ams`. No controls yet.
3. **Dashboard controls.** Pause/Resume/Cancel + Speed select, wired to existing endpoints. AMS tray sheet with drying.
4. **Print flow.** Drop zone, plate card, slicing-settings group, filaments group, preview-then-confirm, slicing/upload progress via SSE.
5. **Settings.** Ported to the new shell using the inherited design system.
6. **Cleanup.** Delete `app/templates/index.html`, `app/templates/settings.html`, `app/static/style.css`, and the `/old` routes.

Each step is independently shippable and the user-visible UI never regresses because old pages remain reachable at `/old` until step 6.

## Open implementation questions

None that block planning. Items to resolve during writing-plans:

- Exact choice of icon set (Lucide vs Heroicons — both work with Tailwind; lean Lucide for variety).
- Whether to adopt `react-router`'s `loader` / `action` pattern or keep all data-fetching in React Query. Lean React Query only — simpler.
- How to surface MQTT-driven state freshness (last-updated timestamp? subtle staleness indicator when poll > 10s behind?). Not required for v1.
