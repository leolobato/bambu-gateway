import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PrinterRow } from '@/components/settings/printer-row';
import {
  PrinterFormDialog,
  type PrinterFormMode,
} from '@/components/settings/printer-form-dialog';
import { listPrinterConfigs } from '@/lib/api/printer-configs';
import { listPrinters } from '@/lib/api/printers';
import type { PrinterStatus } from '@/lib/api/types';

export function PrintersSection() {
  const [mode, setMode] = useState<PrinterFormMode | null>(null);

  const configsQuery = useQuery({
    queryKey: ['printer-configs'],
    queryFn: listPrinterConfigs,
    staleTime: 30_000,
  });

  // Live statuses for the per-row status dot.
  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: listPrinters,
    refetchInterval: 4_000,
  });
  const statusBySerial = useMemo(() => {
    const map = new Map<string, PrinterStatus>();
    for (const p of printersQuery.data?.printers ?? []) map.set(p.id, p);
    return map;
  }, [printersQuery.data]);

  const printers = configsQuery.data?.printers ?? [];

  return (
    <section className="flex flex-col gap-2">
      <header className="flex items-center justify-between px-1">
        <h2 className="text-base font-semibold text-white">Printers</h2>
        <Button
          type="button"
          onClick={() => setMode({ kind: 'add' })}
          className="rounded-full h-8 px-3 bg-surface-1 hover:bg-surface-2 text-accent border-0 text-[13px] font-semibold"
        >
          <Plus className="w-3.5 h-3.5 mr-1" aria-hidden /> Add Printer
        </Button>
      </header>
      <Card className="bg-card border-border p-2 flex flex-col gap-1.5">
        {configsQuery.isLoading ? (
          <Skeleton className="h-14 rounded-2xl" />
        ) : printers.length === 0 ? (
          <p className="text-sm text-text-1 px-3 py-4">
            No printers configured yet. Add one to start monitoring.
          </p>
        ) : (
          printers.map((printer) => (
            <PrinterRow
              key={printer.serial}
              printer={printer}
              liveStatus={statusBySerial.get(printer.serial)}
              onEdit={() => setMode({ kind: 'edit', printer })}
            />
          ))
        )}
      </Card>
      <PrinterFormDialog
        mode={mode}
        open={mode !== null}
        onClose={() => setMode(null)}
      />
    </section>
  );
}
