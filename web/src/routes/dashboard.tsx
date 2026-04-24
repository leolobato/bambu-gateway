import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Skeleton } from '@/components/ui/skeleton';
import { Card } from '@/components/ui/card';
import { PrinterPicker } from '@/components/printer-picker';
import { HeroCard } from '@/components/dashboard/hero-card';
import { StatChipsRow } from '@/components/dashboard/stat-chips-row';
import { AmsSection } from '@/components/dashboard/ams-section';
import { listPrinters } from '@/lib/api/printers';
import { getAms } from '@/lib/api/ams';
import { usePrinterContext } from '@/lib/printer-context';

export default function DashboardRoute() {
  const { activePrinterId, setActivePrinterId } = usePrinterContext();

  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: () => listPrinters(),
    refetchInterval: 4_000,
  });

  // Active id is null on first paint until the picker defaults; once known the
  // query key includes it so switching printers refetches without staleness.
  const amsTargetId = activePrinterId ?? printersQuery.data?.printers[0]?.id ?? null;
  const amsQuery = useQuery({
    queryKey: ['ams', amsTargetId],
    queryFn: () => getAms(amsTargetId ?? undefined),
    refetchInterval: 4_000,
    enabled: !!amsTargetId,
    retry: false,
  });

  const printers = printersQuery.data?.printers ?? [];

  // Default-pick the first printer once the list arrives, or recover when the
  // saved id no longer exists in the configured list.
  useEffect(() => {
    if (printers.length === 0) return;
    const stillExists = activePrinterId && printers.some((p) => p.id === activePrinterId);
    if (!stillExists) setActivePrinterId(printers[0].id);
  }, [printers, activePrinterId, setActivePrinterId]);

  if (printersQuery.isLoading) return <DashboardLoading />;
  if (printersQuery.isError) return <DashboardError detail={(printersQuery.error as Error).message} />;
  if (printers.length === 0) return <DashboardEmpty />;

  const active = printers.find((p) => p.id === activePrinterId) ?? printers[0];

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-[28px] font-extrabold tracking-tight text-white">Dashboard</h1>
      </header>

      <PrinterPicker
        printers={printers}
        activeId={active.id}
        onChange={setActivePrinterId}
      />

      <HeroCard printer={active} />

      <StatChipsRow printer={active} />

      {amsQuery.data && (
        <AmsSection
          printerId={active.id}
          ams={amsQuery.data}
          activeTrayId={active.active_tray}
        />
      )}
    </div>
  );
}

function DashboardLoading() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-9 w-48" />
      <Skeleton className="h-10 w-full max-w-md rounded-full" />
      <Skeleton className="h-44 w-full rounded-2xl" />
      <div className="grid grid-cols-3 gap-2.5">
        <Skeleton className="h-20 rounded-xl" />
        <Skeleton className="h-20 rounded-xl" />
        <Skeleton className="h-20 rounded-xl" />
      </div>
    </div>
  );
}

function DashboardError({ detail }: { detail: string }) {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Dashboard</h1>
      <Card className="p-4 bg-card border-danger/40 text-sm text-text-0">
        Failed to load printer status: <span className="font-mono">{detail}</span>
      </Card>
    </div>
  );
}

function DashboardEmpty() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Dashboard</h1>
      <Card className="p-6 bg-card border-border flex flex-col gap-3 items-start">
        <div className="text-base font-semibold text-white">No printers configured</div>
        <div className="text-sm text-text-1">Add a printer to start monitoring its status here.</div>
        <a
          href="/beta/settings"
          className="inline-flex items-center px-3.5 py-2 rounded-full bg-accent-strong text-white text-sm font-semibold hover:bg-accent transition-colors"
        >
          Open Settings →
        </a>
      </Card>
    </div>
  );
}
