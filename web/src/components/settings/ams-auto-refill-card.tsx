import { useMutation, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { Switch } from '@/components/ui/switch';
import { setAmsAutoRefill } from '@/lib/api/printer-commands';
import { cn } from '@/lib/utils';

export function AmsAutoRefillCard({
  printerId,
  enabled,
  supported,
  online,
}: {
  printerId: string;
  enabled: boolean | null;
  supported: boolean | null;
  online: boolean;
}) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: (next: boolean) => setAmsAutoRefill(printerId, next),
    onError: (err: Error) => {
      toast.error(`Auto refill toggle failed: ${err.message}`);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['ams', printerId] });
      qc.invalidateQueries({ queryKey: ['printers'] });
    },
  });

  const reportedEnabled = enabled ?? false;
  const supportedKnownFalse = supported === false;
  const disabled = mutation.isPending || !online || supportedKnownFalse || enabled === null;
  const detail = !online
    ? 'Printer offline'
    : supportedKnownFalse
      ? 'Not supported by this printer'
      : enabled === null
        ? 'Waiting for printer report'
        : reportedEnabled
          ? 'Identical spools can be used when the active spool runs out'
          : 'Enable to let the printer continue with an identical spool';

  return (
    <section className="flex items-center justify-between gap-4 rounded-md bg-surface-1/50 px-3 py-2.5">
      <div className="min-w-0 flex items-start gap-3">
        <RefreshCw
          className={cn(
            'mt-0.5 h-4 w-4 shrink-0',
            reportedEnabled ? 'text-accent' : 'text-text-2',
          )}
          aria-hidden
        />
        <div className="min-w-0">
          <div className="text-sm font-semibold text-white">AMS auto refill</div>
          <div className="mt-0.5 text-xs text-text-1">{detail}</div>
        </div>
      </div>
      <Switch
        checked={reportedEnabled}
        disabled={disabled}
        onCheckedChange={(next) => mutation.mutate(next)}
        aria-label="AMS auto refill"
      />
    </section>
  );
}
