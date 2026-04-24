import { CheckCircle2 } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { StateBadge } from '@/components/state-badge';
import { formatRemaining } from '@/lib/format';
import type { PrinterStatus } from '@/lib/api/types';
import { cn } from '@/lib/utils';

export function HeroCard({ printer }: { printer: PrinterStatus }) {
  if (!printer.online) return <OfflineHero name={printer.name} />;
  if (printer.state === 'error') return <ErrorHero printer={printer} />;
  if (
    (printer.state === 'printing' || printer.state === 'preparing' || printer.state === 'paused') &&
    printer.job
  ) {
    return <ActiveHero printer={printer} />;
  }
  return <IdleHero printer={printer} />;
}

function ActiveHero({ printer }: { printer: PrinterStatus }) {
  const job = printer.job!;
  const progress = Math.max(0, Math.min(100, job.progress));
  return (
    <Card className="p-5 bg-card border-border flex flex-col gap-4">
      <StateBadge state={printer.state} />
      <div className="text-[40px] sm:text-[48px] font-extrabold tracking-[-0.03em] font-mono tabular-nums text-white leading-none">
        {progress}%
      </div>
      <MetaLine job={job} stage={printer.stage_name} />
      <Progress
        value={progress}
        className="h-1.5 bg-bg-1 [&>[data-state=indeterminate]]:hidden [&>div]:bg-gradient-to-r [&>div]:from-accent-strong [&>div]:to-accent"
      />
    </Card>
  );
}

function MetaLine({ job, stage }: { job: PrinterStatus['job']; stage: string | null }) {
  if (!job) return null;
  const parts: React.ReactNode[] = [
    <span key="file" className="text-text-0 truncate" title={job.file_name}>
      {job.file_name || '—'}
    </span>,
  ];
  if (stage) {
    parts.push(<span key="stage" className="text-text-0">{stage}</span>);
  } else if (job.total_layers > 0) {
    parts.push(
      <span key="layer" className="text-text-0 font-mono tabular-nums">
        Layer {job.current_layer}/{job.total_layers}
      </span>,
    );
  }
  parts.push(
    <span key="rem" className="text-text-0 font-mono tabular-nums">
      {formatRemaining(job.remaining_minutes)} left
    </span>,
  );

  return (
    <div className="flex flex-wrap items-center gap-x-2 text-sm text-text-1">
      {parts.map((node, i) => (
        <span key={i} className="flex items-center gap-2">
          {i > 0 && <span aria-hidden>·</span>}
          {node}
        </span>
      ))}
    </div>
  );
}

function IdleHero({ printer }: { printer: PrinterStatus }) {
  const lastFile = printer.job?.file_name || '';
  return (
    <Card className="p-5 bg-card border-border flex flex-col gap-4">
      <StateBadge state={printer.state} />
      <div className="flex items-center gap-3 text-white">
        <CheckCircle2 className="w-7 h-7 text-success" aria-hidden />
        <div className="text-[28px] font-extrabold tracking-tight">Ready</div>
      </div>
      {lastFile && (
        <div className="text-sm text-text-1 truncate" title={lastFile}>
          Last: <span className="text-text-0">{lastFile}</span>
        </div>
      )}
    </Card>
  );
}

function OfflineHero({ name }: { name: string }) {
  return (
    <Card className="p-5 bg-card border-border opacity-60 flex flex-col gap-3">
      <StateBadge state="offline" />
      <div className="text-[22px] font-bold text-white">Offline · Check connection</div>
      <div className="text-sm text-text-1">
        <a href="/settings" className="text-accent hover:underline">
          Open Settings to verify {name}'s IP and access code →
        </a>
      </div>
    </Card>
  );
}

function ErrorHero({ printer }: { printer: PrinterStatus }) {
  const msg = printer.error_message || 'The printer reported an error.';
  return (
    <div className="flex flex-col gap-3">
      <Card className="p-5 bg-card border-border flex flex-col gap-3">
        <StateBadge state="error" />
        <div className="text-[22px] font-bold text-white">Error</div>
      </Card>
      <Card
        className={cn(
          'p-4 bg-card border border-danger/40 flex items-start gap-3',
        )}
      >
        <div className="w-1 self-stretch rounded-full bg-danger" aria-hidden />
        <div className="flex-1 text-sm text-text-0 break-words">{msg}</div>
      </Card>
    </div>
  );
}
