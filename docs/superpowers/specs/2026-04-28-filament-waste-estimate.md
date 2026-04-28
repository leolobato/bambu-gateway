# Filament Waste Estimate — Design

## Goal

Surface the amount of filament wasted during filament changes (purge / "poop")
in the Print tab's slicer estimate. Multicolor prints can lose a meaningful
fraction of filament to flushing; today the UI shows only totals so users
can't see how much of their spool is going to waste before they print.

## Approach (this pass — gateway-side parsing)

Extend the existing `app/print_estimate.py` to parse the sliced gcode embedded
in the 3MF and compute approximate flush waste. Populate the existing — but
currently mirrored — `model_filament_grams` and `model_filament_millimeters`
fields on `PrintEstimate` with `total - waste`. The frontend gains a "Filament
waste" row in `PrintEstimationCard` showing the difference.

The gateway already has a slot for the breakdown in the response model
(`app/print_estimate.py:42-48`); today both `model_*` and `total_*` fields are
set to the same values because the source data isn't separated. This change
makes them distinct.

## Approach for later (Option B — slicer-side)

The slicer already computes the model/wipe-tower split internally
(`OrcaSlicer/src/libslic3r/GCode/GCodeProcessor.hpp:70-75` —
`model_volumes_per_extruder`, `wipe_tower_volumes_per_extruder`,
`flush_per_filament`). It just doesn't persist it. The gateway parsing in
this pass is an approximation; for an accurate number, the slicer should
emit it directly.

**Two avenues, in priority order:**

1. **Patch `orcaslicer-cli`** (in this workspace at
   `../orcaslicer-cli/`) to capture stats from the slicer binary and
   emit a sidecar metadata file (e.g. `Metadata/flush_stats.json`) in the
   sliced 3MF. The gateway then reads that instead of parsing gcode. No
   upstream dependency.
2. **Push to upstream OrcaSlicer.** Modify
   `OrcaSlicer/src/libslic3r/Format/bbs_3mf.cpp:593-616` (`PlateData::parse_filament_info`)
   to also persist `model_volumes_per_extruder` and
   `wipe_tower_volumes_per_extruder` into `slice_info.config` as new
   per-`<filament>` attributes (e.g. `model_used_g`, `flushed_g`). This
   becomes the canonical source of truth for any consumer of Bambu/Orca
   3MFs.

When Option B lands, the gateway parser becomes a fallback for sliced files
that predate the slicer change — both paths can live side by side, slicer
data preferred when present.

## Scope (this pass)

- Backend: parse `Metadata/plate_*.gcode` inside the sliced 3MF, sum
  extrusion inside `; FLUSH_START` / `; FLUSH_END` regions, attribute to the
  active filament tool.
- Backend: convert mm → grams using filament density (read from the gcode's
  `; filament_density:` comma-separated header) and the standard 1.75 mm
  filament diameter.
- Backend: split `total_*` into `total_*` (unchanged — full amount) and
  `model_*` (= total − waste). No new fields on `PrintEstimate` — the schema
  already has both.
- Frontend: insert a "Filament waste" row in `PrintEstimationCard` between
  "Total filament" and "Model filament", labelled "~X g" to convey
  approximation. Hide the row when waste is null or 0.

## Non-goals

- Slicer-side fixes (Option B above — deferred).
- Per-filament breakdown of waste (one aggregate number is enough for the
  print-tab summary).
- Cost calculation (we have grams; cost would be a separate feature).
- Time impact of toolchanges (separate concern).

## Backend

### `app/print_estimate.py`

Existing function: `extract_print_estimate(file_data: bytes) -> PrintEstimate | None`.

Changes:

1. After reading `Metadata/slice_info.config` (existing logic), iterate the
   zip's namelist for entries matching `Metadata/plate_*.gcode` (one per
   plate; usually just `plate_1.gcode`).

2. For each gcode entry, stream it line by line through a new helper
   `_compute_flush_waste(gcode_bytes: bytes) -> tuple[float, float]` that
   returns `(waste_mm, waste_grams)`. Single pass with this state:

   - `in_flush: bool = False` — toggled by `; FLUSH_START` / `; FLUSH_END`
     comment lines (substring match, allowing leading whitespace).
   - `relative_e: bool = False` — `M83` switches to relative; `M82` switches
     back to absolute. Default is absolute.
   - `last_abs_e: float = 0.0` — last seen absolute E value for absolute mode.
   - `active_tool: int = 0` — set on `T<n>` and on `M620 S<n>A` lines.
   - `densities: list[float]` — parsed from the header line
     `; filament_density: x,y,z` (one comma-separated entry per filament).
     Falls back to 1.24 (PLA) per slot when missing.
   - `diameter_mm: float = 1.75` — from `; filament_diameter:` header,
     default 1.75.

3. For every `G1 ... E<v>` (regex `\bE(-?\d+\.?\d*)`) inside a flush region:
   - relative mode: `delta = max(0, v)` (negative E in flush = retract; ignore)
   - absolute mode: `delta = max(0, v - last_abs_e); last_abs_e = v`
   - Add `delta` to `flush_mm_per_tool[active_tool]`.

4. On `G92 E<v>`: in absolute mode, reset `last_abs_e = v` (no contribution
   to flush totals — G92 is not extrusion).

5. Convert per-tool mm to grams:
   `g = π * (diameter_mm / 2) ** 2 * flush_mm_per_tool[t] * densities[t] / 1000`.
   Sum to total `waste_g` and `waste_mm`.

6. In the existing computation:

   ```python
   waste_mm, waste_g = 0.0, 0.0
   for name in zf.namelist():
       if name.startswith("Metadata/plate_") and name.endswith(".gcode"):
           wm, wg = _compute_flush_waste(zf.read(name))
           waste_mm += wm
           waste_g += wg
   model_mm = max(0.0, total_mm - waste_mm) if total_mm else None
   model_g = max(0.0, total_g - waste_g) if total_g else None
   ```

   Replace the current `model_filament_*=total_*` mirroring with the values
   above.

7. Errors during gcode parsing must not break the estimate. Wrap the inner
   loop in `try/except Exception` and treat as zero waste with a logger
   warning. The total/timing fields stay valid.

### Caveat (and why "approximate")

Bambu's `M620.10 A1 ... L[flush_length]` instruction does an internal
filament-cut-and-load that the printer firmware executes — there's no
corresponding `G1 E` move in the gcode. Our parser undercounts that
component. Empirically the visible `; FLUSH_START` / `; FLUSH_END` G1 E moves
account for roughly 70–90% of OrcaSlicer's reported flush volume. The UI
labels the value as approximate ("~X g") to set expectations honestly. When
Option B lands, the number becomes exact.

## Frontend

### `web/src/components/print/print-estimation-card.tsx`

Add one new filament row between "Total filament" and "Model filament":

```tsx
<FilamentRow
  icon={<Trash2 className="h-4 w-4" aria-hidden />}
  label="Filament waste (~)"
  millimeters={wasteMm}
  grams={wasteG}
/>
```

Where:

```ts
const wasteG =
  estimate.total_filament_grams != null && estimate.model_filament_grams != null
    ? Math.max(0, estimate.total_filament_grams - estimate.model_filament_grams)
    : null;
const wasteMm =
  estimate.total_filament_millimeters != null && estimate.model_filament_millimeters != null
    ? Math.max(0, estimate.total_filament_millimeters - estimate.model_filament_millimeters)
    : null;
```

Render the row only when `wasteG != null && wasteG > 0.05` (avoids a
spurious "0.00 g" line for single-color prints where the math returns a
near-zero rounding artifact).

Add `Trash2` to the lucide-react import in the file.

The "~" suffix on the label, plus its position between Total and Model,
makes the relationship visible at a glance: total = model + waste.

## Tests

### `tests/test_print_estimate.py`

Either new file or extend an existing `test_print_estimate.py` if present.
Use `zipfile` to build synthetic 3MFs in-memory.

1. **`test_extractPrintEstimate_multicolorWithFlushBlocks_splitsModelAndWaste`**
   — synthetic 3MF: `slice_info.config` declares two filaments with `used_g`
   summing to 50 g; `plate_1.gcode` includes two `; FLUSH_START` blocks
   each containing relative-mode `G1 E100` (so 200 mm of waste in tool 0).
   Assert `total_filament_grams ≈ 50`, `model_filament_grams ≈ 50 - waste_g`,
   and that `waste_g > 0`.

2. **`test_extractPrintEstimate_singleColorNoFlush_modelEqualsTotal`** —
   synthetic 3MF with one filament, no `; FLUSH_START` blocks. Assert
   `model_filament_grams == total_filament_grams`.

3. **`test_computeFlushWaste_absoluteExtruderMode_accumulatesPositiveDeltas`**
   — direct unit test of `_compute_flush_waste` with hand-crafted gcode using
   `M82` (absolute), `G92 E0`, `; FLUSH_START`, `G1 E50`, `; FLUSH_END`.
   Expect 50 mm.

4. **`test_computeFlushWaste_g92Reset_doesNotContribute`** — gcode with
   `G92 E0` inside a flush region after `G1 E50` (absolute). Expect 50 mm
   (G92 reset is not extrusion).

5. **`test_computeFlushWaste_relativeExtruderMode_negativeRetractsIgnored`**
   — relative mode (`M83`), `G1 E100` then `G1 E-2` inside flush. Expect
   100 mm (retract during flush ignored).

6. **`test_extractPrintEstimate_corruptGcode_returnsTotalsOnly`** — 3MF with
   valid `slice_info.config` but malformed gcode. Assert totals come back
   correctly and `model_*` falls back to total (no exception).

No frontend tests (the project has no frontend test framework). Manual
verification against a multicolor sliced job at
`http://10.0.1.9:4844/print` is acceptable.

## File touch list

- `app/print_estimate.py` — extend `extract_print_estimate`; add
  `_compute_flush_waste` helper.
- `tests/test_print_estimate.py` — new or extended.
- `web/src/components/print/print-estimation-card.tsx` — add Waste row,
  import `Trash2`.

`PrintEstimate` model (`app/models.py`) is unchanged — fields already exist.
`web/src/lib/api/types.ts` `PrintEstimate` interface is unchanged for the
same reason.
