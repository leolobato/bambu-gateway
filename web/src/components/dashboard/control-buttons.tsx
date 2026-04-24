import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Pause, Play, X } from 'lucide-react';
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
import { cancelPrint, pausePrint, resumePrint } from '@/lib/api/printer-commands';
import type { PrinterStatus } from '@/lib/api/types';
import { cn } from '@/lib/utils';

const ACTIVE_STATES = new Set<PrinterStatus['state']>(['printing', 'preparing', 'paused']);

export function ControlButtons({ printer }: { printer: PrinterStatus }) {
  if (!printer.online) return null;
  if (printer.state === 'error') return null;
  if (ACTIVE_STATES.has(printer.state)) return <ActiveControls printer={printer} />;
  return null;
}

function ActiveControls({ printer }: { printer: PrinterStatus }) {
  const queryClient = useQueryClient();
  const isPaused = printer.state === 'paused';

  const pauseResume = useMutation({
    mutationFn: () => (isPaused ? resumePrint(printer.id) : pausePrint(printer.id)),
    onSuccess: () => {
      toast.success(isPaused ? 'Resuming…' : 'Pausing…');
      queryClient.invalidateQueries({ queryKey: ['printers'] });
    },
    onError: (err: Error) => {
      toast.error(`${isPaused ? 'Resume' : 'Pause'} failed: ${err.message}`);
    },
  });

  const [confirmOpen, setConfirmOpen] = useState(false);
  const cancel = useMutation({
    mutationFn: () => cancelPrint(printer.id),
    onSuccess: () => {
      toast.success('Cancelling…');
      setConfirmOpen(false);
      queryClient.invalidateQueries({ queryKey: ['printers'] });
    },
    onError: (err: Error) => {
      toast.error(`Cancel failed: ${err.message}`);
    },
  });

  return (
    <>
      <div className="grid grid-cols-2 gap-2.5">
        <Button
          type="button"
          onClick={() => pauseResume.mutate()}
          disabled={pauseResume.isPending}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          {isPaused ? (
            <>
              <Play className="w-4 h-4 mr-1.5" aria-hidden /> Resume
            </>
          ) : (
            <>
              <Pause className="w-4 h-4 mr-1.5" aria-hidden /> Pause
            </>
          )}
        </Button>
        <Button
          type="button"
          onClick={() => setConfirmOpen(true)}
          disabled={cancel.isPending}
          className={cn(
            'rounded-full h-11 text-[14px] font-semibold border',
            'bg-danger/10 hover:bg-danger/20 text-danger border-danger/40',
          )}
        >
          <X className="w-4 h-4 mr-1.5" aria-hidden /> Cancel
        </Button>
      </div>
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel this print?</AlertDialogTitle>
            <AlertDialogDescription>
              The printer will stop immediately. This can't be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={cancel.isPending}>Keep printing</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                // Stop the dialog from auto-closing — we close on success/error in the mutation.
                e.preventDefault();
                cancel.mutate();
              }}
              disabled={cancel.isPending}
              className="bg-danger text-white hover:bg-danger/90"
            >
              {cancel.isPending ? 'Cancelling…' : 'Cancel print'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

