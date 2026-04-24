import type { PrinterState } from '@/lib/api/types';
import { cn } from '@/lib/utils';

const VARIANTS: Record<PrinterState, { className: string; label: string }> = {
  printing:  { className: 'bg-accent/15 text-accent',     label: 'Printing'  },
  paused:    { className: 'bg-info/15 text-info',         label: 'Paused'    },
  preparing: { className: 'bg-warm/15 text-warm',         label: 'Preparing' },
  error:     { className: 'bg-danger/15 text-danger',     label: 'Error'     },
  idle:      { className: 'bg-success/15 text-success',   label: 'Idle'      },
  finished:  { className: 'bg-success/15 text-success',   label: 'Finished'  },
  cancelled: { className: 'bg-text-2/15 text-text-1',     label: 'Cancelled' },
  offline:   { className: 'bg-text-2/15 text-text-2',     label: 'Offline'   },
};

export function StateBadge({ state }: { state: PrinterState }) {
  const { className, label } = VARIANTS[state];
  return (
    <span
      className={cn(
        'inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-semibold uppercase tracking-wider',
        className,
      )}
    >
      {label}
    </span>
  );
}
