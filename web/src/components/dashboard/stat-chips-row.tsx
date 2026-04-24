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
