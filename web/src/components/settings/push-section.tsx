import { useQuery } from '@tanstack/react-query';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PushDeviceRow } from '@/components/settings/push-device-row';
import { listDevices } from '@/lib/api/devices';
import { getCapabilities } from '@/lib/api/capabilities';

export function PushSection() {
  const capsQuery = useQuery({
    queryKey: ['capabilities'],
    queryFn: getCapabilities,
    staleTime: 60_000,
  });

  const devicesQuery = useQuery({
    queryKey: ['devices'],
    queryFn: listDevices,
    enabled: capsQuery.data?.push === true,
  });

  const pushEnabled = capsQuery.data?.push === true;
  const devices = devicesQuery.data?.devices ?? [];

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-base font-semibold text-white px-1">Push Notifications</h2>
      <Card className="bg-card border-border p-4 flex flex-col gap-3">
        {capsQuery.isLoading ? (
          <Skeleton className="h-5 w-2/3" />
        ) : !pushEnabled ? (
          <p className="text-sm text-text-1">
            Push is <span className="text-text-0 font-semibold">disabled</span>. Configure
            APNs credentials in the gateway environment to enable — see{' '}
            <a
              href="https://github.com/leolobato/bambu-gateway/blob/main/docs/APNS.md"
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              docs/APNS.md
            </a>
            .
          </p>
        ) : (
          <>
            <p className="text-sm text-text-1">
              Push is <span className="text-text-0 font-semibold">enabled</span>. Devices
              register automatically when the iOS app launches and notifications are
              allowed.
            </p>
            {devicesQuery.isLoading ? (
              <Skeleton className="h-14 rounded-2xl" />
            ) : devices.length === 0 ? (
              <p className="text-sm text-text-2">No devices registered yet.</p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {devices.map((d) => (
                  <PushDeviceRow key={d.id} device={d} />
                ))}
              </div>
            )}
          </>
        )}
      </Card>
    </section>
  );
}
