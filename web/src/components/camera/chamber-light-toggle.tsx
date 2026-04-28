import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Lightbulb } from 'lucide-react';
import type { PrinterStatus } from '@/lib/api/types';
import { setChamberLight } from '@/lib/api/printer-commands';
import { cn } from '@/lib/utils';

export function ChamberLightToggle({ printer }: { printer: PrinterStatus }) {
  const supported = printer.camera?.chamber_light?.supported ?? false;
  const reportedOn = printer.camera?.chamber_light?.on ?? null;
  const [optimisticOn, setOptimisticOn] = useState<boolean | null>(null);
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: (next: boolean) => setChamberLight(printer.id, next),
    onMutate: (next) => setOptimisticOn(next),
    onSettled: () => {
      setOptimisticOn(null);
      qc.invalidateQueries({ queryKey: ['printers'] });
    },
  });

  if (!supported || reportedOn === null) return null;

  const isOn = optimisticOn ?? reportedOn;
  const disabled = mutation.isPending || !printer.online;

  return (
    <button
      type="button"
      onClick={() => mutation.mutate(!isOn)}
      disabled={disabled}
      aria-label="Chamber light"
      aria-pressed={isOn}
      className={cn(
        'flex items-center justify-center gap-3 w-full h-14 rounded-xl border text-sm font-semibold transition-colors duration-fast',
        isOn
          ? 'bg-accent-strong border-accent-strong text-white'
          : 'bg-surface-1 border-border text-text-0 hover:text-white',
        disabled && 'opacity-60 cursor-not-allowed',
      )}
    >
      <Lightbulb className={cn('w-5 h-5', isOn ? 'fill-current' : '')} aria-hidden />
      {isOn ? 'Chamber light on' : 'Chamber light off'}
    </button>
  );
}
