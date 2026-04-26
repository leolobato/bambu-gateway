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
  // 3MFs commonly declare more filaments than any object actually prints.
  // The slicer doesn't care about unused slots (the gateway pads them with
  // a benign profile), so don't ask the user to map them.
  // Fallback: if no filament reports `used`, show everything — covers old
  // gateways that pre-date the parser change.
  const visibleFilaments = projectFilaments.some((f) => f.used)
    ? projectFilaments.filter((f) => f.used)
    : projectFilaments;

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
