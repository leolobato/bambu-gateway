import { useQuery } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { getCapabilities } from '@/lib/api/capabilities';

export function AboutSection() {
  const capsQuery = useQuery({
    queryKey: ['capabilities'],
    queryFn: getCapabilities,
    staleTime: 60_000,
  });

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-base font-semibold text-white px-1">About</h2>
      <Card className="bg-card border-border p-4 flex flex-col gap-3">
        <div className="flex items-baseline justify-between">
          <span className="text-sm text-text-1">Bambu Gateway</span>
          {capsQuery.isLoading ? (
            <Skeleton className="h-4 w-12" />
          ) : (
            <span className="text-sm text-text-0 font-mono tabular-nums">
              v{capsQuery.data?.version || '?'}
            </span>
          )}
        </div>
        <a
          href="https://github.com/leolobato/bambu-gateway"
          target="_blank"
          rel="noreferrer"
          className="text-sm text-accent hover:underline inline-flex items-center gap-1"
        >
          Source code
          <ExternalLink className="w-3.5 h-3.5" aria-hidden />
        </a>
      </Card>
    </section>
  );
}
