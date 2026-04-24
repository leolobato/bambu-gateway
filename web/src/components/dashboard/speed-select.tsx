import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select';
import { setPrinterSpeed } from '@/lib/api/printer-commands';
import type { PrinterListResponse, PrinterStatus, SpeedLevel } from '@/lib/api/types';
import { cn } from '@/lib/utils';

const SPEED_OPTIONS: { value: SpeedLevel; label: string }[] = [
  { value: 1, label: 'Silent' },
  { value: 2, label: 'Standard' },
  { value: 3, label: 'Sport' },
  { value: 4, label: 'Ludicrous' },
];

const VALID_LEVELS: ReadonlySet<number> = new Set([1, 2, 3, 4]);

function isSpeedLevel(v: number): v is SpeedLevel {
  return VALID_LEVELS.has(v);
}

export function SpeedSelect({ printer }: { printer: PrinterStatus }) {
  const queryClient = useQueryClient();
  const current = isSpeedLevel(printer.speed_level) ? printer.speed_level : null;
  const currentLabel =
    SPEED_OPTIONS.find((o) => o.value === current)?.label ?? '—';

  const mutation = useMutation({
    mutationFn: (level: SpeedLevel) => setPrinterSpeed(printer.id, level),
    onMutate: async (level) => {
      await queryClient.cancelQueries({ queryKey: ['printers'] });
      const prev = queryClient.getQueryData<PrinterListResponse>(['printers']);
      if (prev) {
        queryClient.setQueryData<PrinterListResponse>(['printers'], {
          printers: prev.printers.map((p) =>
            p.id === printer.id ? { ...p, speed_level: level } : p,
          ),
        });
      }
      return { prev };
    },
    onError: (err: Error, _level, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(['printers'], ctx.prev);
      toast.error(`Speed change failed: ${err.message}`);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
    },
  });

  const offline = !printer.online;
  const value = current != null ? String(current) : '';

  return (
    <div
      className={cn(
        'flex flex-col gap-1.5 p-3 rounded-xl bg-card text-left',
        offline && 'opacity-60',
      )}
    >
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
        Speed
      </span>
      <Select
        value={value}
        onValueChange={(v) => {
          const level = Number(v);
          if (isSpeedLevel(level)) mutation.mutate(level);
        }}
        disabled={offline || mutation.isPending}
      >
        <SelectTrigger
          aria-label="Print speed"
          className={cn(
            'h-auto p-0 border-0 bg-transparent text-accent font-mono tabular-nums',
            'focus:ring-0 focus:ring-offset-0',
          )}
        >
          <span className="flex items-baseline gap-1">
            <span className="text-[22px] font-bold leading-none">{currentLabel}</span>
          </span>
        </SelectTrigger>
        <SelectContent>
          {SPEED_OPTIONS.map((opt) => (
            <SelectItem key={opt.value} value={String(opt.value)}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
