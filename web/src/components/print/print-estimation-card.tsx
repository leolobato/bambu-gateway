import type { ReactNode } from 'react';
import { Box, Clock3, Printer, Route, Wrench } from 'lucide-react';
import type { PrintEstimate } from '@/lib/api/types';
import {
  formatEstimateDuration,
  formatEstimateLength,
  formatEstimateMass,
  hasPrintEstimate,
} from '@/lib/print-estimate';
import { cn } from '@/lib/utils';

export function PrintEstimationCard({
  estimate,
  className,
}: {
  estimate: PrintEstimate | null | undefined;
  className?: string;
}) {
  if (!hasPrintEstimate(estimate)) return null;

  const showFilament =
    estimate.total_filament_millimeters != null ||
    estimate.total_filament_grams != null ||
    estimate.model_filament_millimeters != null ||
    estimate.model_filament_grams != null;
  const showTime =
    estimate.prepare_seconds != null ||
    estimate.model_print_seconds != null ||
    estimate.total_seconds != null;

  return (
    <section
      className={cn(
        'rounded-[22px] border border-line bg-surface-1/85 p-4 shadow-card backdrop-blur',
        className,
      )}
      aria-label="Print summary"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-text-2">
            Print summary
          </p>
          <h2 className="text-[17px] font-bold text-text-0">Slicer estimate</h2>
        </div>
      </div>

      {showFilament && (
        <div className="grid gap-2">
          <FilamentRow
            icon={<Route className="h-4 w-4" aria-hidden />}
            label="Total filament"
            millimeters={estimate.total_filament_millimeters}
            grams={estimate.total_filament_grams}
          />
          <FilamentRow
            icon={<Box className="h-4 w-4" aria-hidden />}
            label="Model filament"
            millimeters={estimate.model_filament_millimeters}
            grams={estimate.model_filament_grams}
          />
        </div>
      )}

      {showFilament && showTime && <div className="my-3 h-px bg-line" />}

      {showTime && (
        <div className="grid gap-2">
          <TimeRow
            icon={<Wrench className="h-4 w-4" aria-hidden />}
            label="Prepare"
            seconds={estimate.prepare_seconds}
          />
          <TimeRow
            icon={<Printer className="h-4 w-4" aria-hidden />}
            label="Printing"
            seconds={estimate.model_print_seconds}
          />
          <TimeRow
            icon={<Clock3 className="h-4 w-4" aria-hidden />}
            label="Total"
            seconds={estimate.total_seconds}
            emphasized
          />
        </div>
      )}
    </section>
  );
}

function FilamentRow({
  icon,
  label,
  millimeters,
  grams,
}: {
  icon: ReactNode;
  label: string;
  millimeters: number | null | undefined;
  grams: number | null | undefined;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3 text-[13px]">
      <div className="flex min-w-0 items-center gap-2 text-text-2">
        <span className="text-text-2">{icon}</span>
        <span className="truncate">{label}</span>
      </div>
      <span className="min-w-[72px] text-right font-mono text-text-0">
        {formatEstimateLength(millimeters) ?? '—'}
      </span>
      <span className="min-w-[72px] text-right font-mono text-text-0">
        {formatEstimateMass(grams) ?? '—'}
      </span>
    </div>
  );
}

function TimeRow({
  icon,
  label,
  seconds,
  emphasized = false,
}: {
  icon: ReactNode;
  label: string;
  seconds: number | null | undefined;
  emphasized?: boolean;
}) {
  return (
    <div className="flex items-center gap-3 text-[13px]">
      <div className={cn('flex min-w-0 flex-1 items-center gap-2', emphasized ? 'text-text-0' : 'text-text-2')}>
        <span className="text-text-2">{icon}</span>
        <span className={cn('truncate', emphasized && 'font-semibold')}>{label}</span>
      </div>
      <span className={cn('font-mono text-text-0', emphasized && 'font-semibold')}>
        {formatEstimateDuration(seconds) ?? '—'}
      </span>
    </div>
  );
}
