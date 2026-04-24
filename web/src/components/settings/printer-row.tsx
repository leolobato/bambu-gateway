import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { MoreHorizontal } from 'lucide-react';
import { toast } from 'sonner';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { TrayRow } from '@/components/tray-row';
import { deletePrinterConfig } from '@/lib/api/printer-configs';
import type { PrinterConfigResponse, PrinterStatus } from '@/lib/api/types';

function dotColor(status: PrinterStatus | undefined): string | null {
  if (!status || !status.online) return '#6B7280'; // text-2 / offline
  switch (status.state) {
    case 'printing':
    case 'preparing':
      return '#60A5FA'; // accent
    case 'paused':
      return '#FBBF24'; // warm
    case 'error':
      return '#EF4444'; // danger
    default:
      return '#22C55E'; // success
  }
}

export function PrinterRow({
  printer,
  liveStatus,
  onEdit,
}: {
  printer: PrinterConfigResponse;
  /** Live status from /api/printers; may be undefined while loading. */
  liveStatus: PrinterStatus | undefined;
  onEdit: () => void;
}) {
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const remove = useMutation({
    mutationFn: () => deletePrinterConfig(printer.serial),
    onSuccess: () => {
      toast.success(`${printer.name || printer.serial} removed`);
      queryClient.invalidateQueries({ queryKey: ['printer-configs'] });
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      setConfirmOpen(false);
    },
    onError: (err: Error) => {
      toast.error(`Remove failed: ${err.message}`);
    },
  });

  const subtitle = `${printer.serial} · ${printer.ip}`;
  const body = printer.machine_model || 'No machine model set';

  return (
    <>
      <TrayRow
        colorDot={dotColor(liveStatus)}
        title={printer.name || `Printer ${printer.serial.slice(-4)}`}
        subtitle={subtitle}
        body={body}
        right={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                className="h-8 w-8 p-0 text-text-1 hover:text-white"
                aria-label={`Actions for ${printer.name || printer.serial}`}
              >
                <MoreHorizontal className="w-4 h-4" aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="bg-bg-1 border-border">
              <DropdownMenuItem onSelect={onEdit}>Edit</DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => setConfirmOpen(true)}
                className="text-danger focus:text-danger focus:bg-danger/10"
              >
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Remove {printer.name || printer.serial}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              The MQTT connection will be dropped and the printer disappears from
              the Dashboard. The printer itself isn't affected.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>Keep</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                remove.mutate();
              }}
              disabled={remove.isPending}
              className="bg-danger text-white hover:bg-danger/90"
            >
              {remove.isPending ? 'Removing…' : 'Remove'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
