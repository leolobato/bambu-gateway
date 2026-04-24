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
import { deleteDevice, sendTestPush } from '@/lib/api/devices';
import type { DeviceInfo } from '@/lib/api/types';

export function PushDeviceRow({ device }: { device: DeviceInfo }) {
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const test = useMutation({
    mutationFn: () => sendTestPush(device.id),
    onSuccess: () => toast.success(`Test push sent to ${device.name || device.id}`),
    onError: (err: Error) => toast.error(`Test push failed: ${err.message}`),
  });

  const remove = useMutation({
    mutationFn: () => deleteDevice(device.id),
    onSuccess: () => {
      toast.success(`${device.name || device.id} removed`);
      queryClient.invalidateQueries({ queryKey: ['devices'] });
      setConfirmOpen(false);
    },
    onError: (err: Error) => toast.error(`Remove failed: ${err.message}`),
  });

  const tokens: string[] = [];
  if (device.has_device_token) tokens.push('alert');
  if (device.has_live_activity_start_token) tokens.push('live activity');
  if (device.active_activity_count > 0) {
    tokens.push(
      `${device.active_activity_count} activity${device.active_activity_count === 1 ? '' : 'ies'}`,
    );
  }
  const subtitle = device.id;
  const body = tokens.length > 0 ? tokens.join(' · ') : 'no tokens';

  return (
    <>
      <TrayRow
        // Devices don't carry a color — show a muted dot to keep alignment.
        colorDot="#6B7280"
        title={device.name || 'Unnamed device'}
        subtitle={subtitle}
        body={body}
        right={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                className="h-8 w-8 p-0 text-text-1 hover:text-white"
                aria-label={`Actions for ${device.name || device.id}`}
              >
                <MoreHorizontal className="w-4 h-4" aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="bg-bg-1 border-border">
              <DropdownMenuItem
                onSelect={() => test.mutate()}
                disabled={test.isPending || !device.has_device_token}
              >
                Send test push
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => setConfirmOpen(true)}
                className="text-danger focus:text-danger focus:bg-danger/10"
              >
                Remove
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this device?</AlertDialogTitle>
            <AlertDialogDescription>
              {device.name || device.id} will stop receiving push notifications
              until it re-registers (typically on next iOS app launch).
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
