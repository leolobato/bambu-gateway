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
