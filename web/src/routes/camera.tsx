import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Skeleton } from '@/components/ui/skeleton';
import { Card } from '@/components/ui/card';
import { PrinterPicker } from '@/components/printer-picker';
import { ChamberLightToggle } from '@/components/camera/chamber-light-toggle';
import { CameraFeed } from '@/components/camera/camera-feed';
import { listPrinters } from '@/lib/api/printers';
import { usePrinterContext } from '@/lib/printer-context';

export default function CameraRoute() {
  const { activePrinterId, setActivePrinterId } = usePrinterContext();

  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: () => listPrinters(),
    refetchInterval: 4_000,
  });

  const printers = printersQuery.data?.printers ?? [];

  useEffect(() => {
    if (printers.length === 0) return;
    const stillExists = activePrinterId && printers.some((p) => p.id === activePrinterId);
    if (!stillExists) setActivePrinterId(printers[0].id);
  }, [printers, activePrinterId, setActivePrinterId]);

  if (printersQuery.isLoading) return <CameraLoading />;
  if (printersQuery.isError) {
    return <CameraError detail={(printersQuery.error as Error).message} />;
  }
  if (printers.length === 0) return <CameraEmpty />;

  const active = printers.find((p) => p.id === activePrinterId) ?? printers[0];

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-[28px] font-extrabold tracking-tight text-white">Camera</h1>
      </header>

      <PrinterPicker printers={printers} activeId={active.id} onChange={setActivePrinterId} />

      <ChamberLightToggle printer={active} />

      <CameraFeed printer={active} />
    </div>
  );
}

function CameraLoading() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-9 w-32" />
      <Skeleton className="h-10 w-full max-w-md rounded-full" />
      <Skeleton className="h-14 w-full rounded-xl" />
      <Skeleton className="aspect-video w-full rounded-xl" />
    </div>
  );
}

function CameraError({ detail }: { detail: string }) {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Camera</h1>
      <Card className="p-4 bg-card border-danger/40 text-sm text-text-0">
        Failed to load printers: <span className="font-mono">{detail}</span>
      </Card>
    </div>
  );
}

function CameraEmpty() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Camera</h1>
      <Card className="p-6 bg-card border-border flex flex-col gap-3 items-start">
        <div className="text-base font-semibold text-white">No printers configured</div>
        <div className="text-sm text-text-1">Add a printer to see its camera feed here.</div>
        <a
          href="/settings"
          className="inline-flex items-center px-3.5 py-2 rounded-full bg-accent-strong text-white text-sm font-semibold hover:bg-accent transition-colors"
        >
          Open Settings →
        </a>
      </Card>
    </div>
  );
}
