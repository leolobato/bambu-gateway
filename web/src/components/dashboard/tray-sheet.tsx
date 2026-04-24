import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Square } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { startDrying, stopDrying } from '@/lib/api/printer-commands';
import { normalizeTrayColor } from '@/lib/filament-color';
import { formatRemaining } from '@/lib/format';
import { useMediaQuery } from '@/lib/use-media-query';
import type { AMSTray, AMSUnit } from '@/lib/api/types';
import { cn } from '@/lib/utils';

export interface TraySheetSelection {
  tray: AMSTray;
  /** Null for the external spool (no AMS unit, never dries). */
  unit: AMSUnit | null;
  /** Header label (e.g. "Tray 3" or "External Spool"). */
  label: string;
}

export function TraySheet({
  printerId,
  selection,
  onClose,
}: {
  printerId: string;
  selection: TraySheetSelection | null;
  onClose: () => void;
}) {
  const isDesktop = useMediaQuery('(min-width: 640px)');
  const open = selection !== null;

  return (
    <Sheet open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent
        side={isDesktop ? 'right' : 'bottom'}
        className={cn(
          'bg-bg-1 border-border text-text-0 flex flex-col gap-5 overflow-y-auto',
          isDesktop ? 'w-[400px] sm:max-w-[400px]' : 'h-[80dvh]',
        )}
      >
        {selection && (
          <TraySheetBody
            printerId={printerId}
            tray={selection.tray}
            unit={selection.unit}
            label={selection.label}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

function TraySheetBody({
  printerId,
  tray,
  unit,
  label,
}: {
  printerId: string;
  tray: AMSTray;
  unit: AMSUnit | null;
  label: string;
}) {
  const color = normalizeTrayColor(tray.tray_color);
  const filamentName =
    tray.matched_filament?.name ||
    tray.tray_sub_brands ||
    'Unknown filament';
  const grams = tray.tray_weight?.trim();

  return (
    <>
      <SheetHeader className="text-left">
        <SheetTitle className="text-white">{label}</SheetTitle>
        <SheetDescription className="text-text-1">
          {tray.tray_type ? `${tray.tray_type}` : '—'}
          {tray.filament_id ? ` · ${tray.filament_id}` : ''}
        </SheetDescription>
      </SheetHeader>

      <section className="flex items-center gap-3">
        <span
          className={cn(
            'shrink-0 w-10 h-10 rounded-full',
            color == null && 'border border-dashed border-text-2',
          )}
          style={color ? { backgroundColor: color } : undefined}
          aria-hidden
        />
        <div className="flex flex-col gap-0.5 min-w-0">
          <div className="text-[15px] font-semibold text-white truncate">{filamentName}</div>
          {grams && (
            <div className="text-xs text-text-1 font-mono tabular-nums">
              {grams} g loaded
            </div>
          )}
        </div>
      </section>

      {unit?.supports_drying ? (
        <DryingControls printerId={printerId} unit={unit} />
      ) : (
        <p className="text-xs text-text-2">
          {unit
            ? "This AMS doesn't support drying."
            : 'Drying is only available for AMS-housed trays.'}
        </p>
      )}
    </>
  );
}

function DryingControls({ printerId, unit }: { printerId: string; unit: AMSUnit }) {
  const queryClient = useQueryClient();
  const drying = unit.dry_time_remaining > 0;

  const start = useMutation({
    mutationFn: ({ temp, dur }: { temp: number; dur: number }) =>
      startDrying(printerId, unit.id, { temperature: temp, durationMinutes: dur }),
    onSuccess: () => {
      toast.success('Drying started');
      queryClient.invalidateQueries({ queryKey: ['ams'] });
    },
    onError: (err: Error) => toast.error(`Start drying failed: ${err.message}`),
  });

  const stop = useMutation({
    mutationFn: () => stopDrying(printerId, unit.id),
    onSuccess: () => {
      toast.success('Drying stopped');
      queryClient.invalidateQueries({ queryKey: ['ams'] });
    },
    onError: (err: Error) => toast.error(`Stop drying failed: ${err.message}`),
  });

  if (drying) {
    return (
      <section className="flex flex-col gap-3">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
          Drying
        </div>
        <div className="flex items-center gap-2 text-text-0">
          <Loader2 className="w-4 h-4 text-accent animate-spin" aria-hidden />
          <span className="text-sm">
            {formatRemaining(unit.dry_time_remaining)} remaining
          </span>
        </div>
        <Button
          type="button"
          onClick={() => stop.mutate()}
          disabled={stop.isPending}
          className="rounded-full bg-danger/10 hover:bg-danger/20 text-danger border border-danger/40"
        >
          <Square className="w-4 h-4 mr-1.5" aria-hidden />
          {stop.isPending ? 'Stopping…' : 'Stop drying'}
        </Button>
      </section>
    );
  }

  return <DryingForm key={unit.id} unit={unit} onSubmit={(p) => start.mutate(p)} pending={start.isPending} />;
}

function DryingForm({
  unit,
  onSubmit,
  pending,
}: {
  unit: AMSUnit;
  onSubmit: (p: { temp: number; dur: number }) => void;
  pending: boolean;
}) {
  const [temp, setTemp] = useState<number>(unit.max_drying_temp);
  const [dur, setDur] = useState<number>(480);

  const tempInvalid = !Number.isFinite(temp) || temp <= 0 || temp > unit.max_drying_temp;
  const durInvalid = !Number.isFinite(dur) || dur <= 0 || dur > 24 * 60;

  return (
    <section className="flex flex-col gap-3">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
        Start drying
      </div>
      <label className="flex flex-col gap-1 text-sm text-text-1">
        Temperature (°C)
        <input
          type="number"
          inputMode="numeric"
          min={1}
          max={unit.max_drying_temp}
          value={Number.isFinite(temp) ? temp : ''}
          onChange={(e) => setTemp(Number(e.target.value))}
          className={cn(
            'h-10 px-3 rounded-md bg-card border border-border text-text-0 font-mono tabular-nums',
            'focus:outline-none focus:ring-2 focus:ring-ring',
            tempInvalid && 'border-danger',
          )}
        />
        <span className="text-[11px] text-text-2">Max for this AMS: {unit.max_drying_temp}°C</span>
      </label>
      <label className="flex flex-col gap-1 text-sm text-text-1">
        Duration (minutes)
        <input
          type="number"
          inputMode="numeric"
          min={1}
          max={24 * 60}
          value={Number.isFinite(dur) ? dur : ''}
          onChange={(e) => setDur(Number(e.target.value))}
          className={cn(
            'h-10 px-3 rounded-md bg-card border border-border text-text-0 font-mono tabular-nums',
            'focus:outline-none focus:ring-2 focus:ring-ring',
            durInvalid && 'border-danger',
          )}
        />
        <span className="text-[11px] text-text-2">Default 480 (8 h); max 1440 (24 h).</span>
      </label>
      <Button
        type="button"
        onClick={() => onSubmit({ temp, dur })}
        disabled={pending || tempInvalid || durInvalid}
        className="rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11"
      >
        {pending ? 'Starting…' : 'Start drying'}
      </Button>
    </section>
  );
}
