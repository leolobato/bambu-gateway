import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select';
import type { AMSTray, FilamentInfo } from '@/lib/api/types';
import { normalizeTrayColor } from '@/lib/filament-color';
import { cn } from '@/lib/utils';

export function FilamentMappingRow({
  filament,
  trays,
  selectedTraySlot,
  onChange,
  disabled = false,
}: {
  filament: FilamentInfo;
  trays: AMSTray[];
  /** -1 = unmapped (slicer will keep the file's filament profile). */
  selectedTraySlot: number;
  onChange: (slot: number) => void;
  disabled?: boolean;
}) {
  // FilamentInfo.color is "RRGGBB" without alpha — wrap to "RRGGBBFF" so
  // normalizeTrayColor's validation accepts it.
  const projectColor = normalizeTrayColor(filament.color ? `${filament.color}FF` : '');
  const selectedTray = trays.find((t) => t.slot === selectedTraySlot);
  const selectedTrayColor = selectedTray ? normalizeTrayColor(selectedTray.tray_color) : null;
  const selectedTrayDetail = selectedTray
    ? [selectedTray.tray_type, selectedTray.matched_filament?.name].filter(Boolean).join(' · ')
    : '';
  const projectName = filament.setting_id || `Filament ${filament.index + 1}`;

  return (
    <div className="flex items-center justify-between gap-3 py-3">
      <div className="flex items-center gap-2.5 min-w-0">
        <span
          className={cn(
            'shrink-0 w-6 h-6 rounded-full',
            projectColor == null && 'border border-dashed border-text-2',
          )}
          style={projectColor ? { backgroundColor: projectColor } : undefined}
          aria-hidden
        />
        <div className="flex flex-col gap-0.5 min-w-0">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
            {filament.type || '—'}
          </span>
          <span className="text-sm text-text-0 truncate">{projectName}</span>
        </div>
      </div>
      <Select
        value={String(selectedTraySlot)}
        onValueChange={(v) => onChange(Number(v))}
        disabled={disabled}
      >
        <SelectTrigger
          aria-label={`Map ${projectName} to AMS tray`}
          className={cn(
            'h-auto py-1 px-2 max-w-[55%] border-0 bg-transparent text-text-1 text-sm',
            'focus:ring-0 focus:ring-offset-0',
          )}
        >
          {/*
            Wrapper is a <div> (not <span>) on purpose: SelectTrigger's base
            class applies `[&>span]:line-clamp-1` (display: -webkit-box) which
            clobbers flex layout and collapses the empty color dot to zero
            width. A div doesn't match that selector, so flex + the dot work.
          */}
          <div className="flex items-center gap-1.5 min-w-0">
            <span aria-hidden>→</span>
            {selectedTrayColor && (
              <span
                className="shrink-0 w-3 h-3 rounded-full"
                style={{ backgroundColor: selectedTrayColor }}
                aria-hidden
              />
            )}
            <span className="truncate">
              {selectedTray
                ? `Tray ${selectedTray.slot + 1}${selectedTrayDetail ? ` · ${selectedTrayDetail}` : ''}`
                : 'Skip'}
            </span>
          </div>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="-1">Skip (use file's profile)</SelectItem>
          {trays.map((tray) => {
            const trayColor = normalizeTrayColor(tray.tray_color);
            const detail = [tray.tray_type, tray.matched_filament?.name].filter(Boolean).join(' · ');
            return (
              <SelectItem key={`${tray.ams_id}-${tray.slot}`} value={String(tray.slot)}>
                <span className="flex items-center gap-2">
                  <span
                    className={cn(
                      'shrink-0 w-3 h-3 rounded-full',
                      trayColor == null && 'border border-dashed border-text-2',
                    )}
                    style={trayColor ? { backgroundColor: trayColor } : undefined}
                    aria-hidden
                  />
                  <span>
                    Tray {tray.slot + 1}
                    {detail && ` · ${detail}`}
                  </span>
                </span>
              </SelectItem>
            );
          })}
        </SelectContent>
      </Select>
    </div>
  );
}
