import { Card } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { FilamentMappingRow } from '@/components/print/filament-mapping-row';
import type { AMSTray, FilamentInfo } from '@/lib/api/types';

export type FilamentMapping = Record<number, number>;

export function FilamentsGroup({
  projectFilaments,
  usedFilamentIndices,
  trays,
  mapping,
  onChange,
  disabled = false,
}: {
  projectFilaments: FilamentInfo[];
  /**
   * Indices the selected plate actually prints. `null` means we don't know
   * (old gateway, generic 3MF) — fall back to `f.used`. An empty array
   * means the plate explicitly prints nothing, so nothing is shown.
   */
  usedFilamentIndices: number[] | null;
  trays: AMSTray[];
  /** Map of project-filament-index → tray-slot (-1 = unmapped). */
  mapping: FilamentMapping;
  onChange: (next: FilamentMapping) => void;
  disabled?: boolean;
}) {
  // Prefer the per-plate map sent by the gateway. Fall back to the global
  // `f.used` flag for old gateways that don't yet send `used_filament_indices`,
  // and finally to "show everything" for generic 3MFs without any extruder
  // metadata at all.
  const visibleFilaments = (() => {
    if (usedFilamentIndices !== null) {
      const allow = new Set(usedFilamentIndices);
      return projectFilaments.filter((f) => allow.has(f.index));
    }
    if (projectFilaments.some((f) => f.used)) {
      return projectFilaments.filter((f) => f.used);
    }
    return projectFilaments;
  })();

  if (visibleFilaments.length === 0) return null;

  return (
    <section className="flex flex-col gap-1">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-text-2 px-1">
        Filaments
      </div>
      <Card className="px-4 bg-card border-border">
        {visibleFilaments.map((filament, idx) => (
          <div key={filament.index}>
            <FilamentMappingRow
              filament={filament}
              trays={trays}
              selectedTraySlot={mapping[filament.index] ?? -1}
              onChange={(slot) => onChange({ ...mapping, [filament.index]: slot })}
              disabled={disabled}
            />
            {idx < visibleFilaments.length - 1 && <Separator className="bg-border" />}
          </div>
        ))}
      </Card>
    </section>
  );
}
