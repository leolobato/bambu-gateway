import { Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Button } from '@/components/ui/button';

export function SlicingProgressCard({
  title,
  statusLine,
  percent,
  onCancel,
  cancelDisabled = false,
}: {
  title: string;
  statusLine: string;
  /** 0–100; pass null for indeterminate. */
  percent: number | null;
  onCancel: () => void;
  cancelDisabled?: boolean;
}) {
  const value = percent != null ? Math.max(0, Math.min(100, percent)) : 0;
  return (
    <Card className="sticky top-2 z-10 p-4 bg-card border border-accent/40 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-white">
          <Loader2 className="w-4 h-4 text-accent animate-spin" aria-hidden />
          <span className="text-[14px] font-semibold">{title}</span>
        </div>
        <Button
          type="button"
          onClick={onCancel}
          disabled={cancelDisabled}
          variant="ghost"
          className="h-auto py-1 px-2 text-danger text-[13px] font-semibold hover:text-danger/80"
        >
          Cancel
        </Button>
      </div>
      <Progress
        value={value}
        className="h-1.5 bg-bg-1 [&>div]:bg-gradient-to-r [&>div]:from-accent-strong [&>div]:to-accent"
      />
      <div className="text-[12px] font-mono text-text-1 truncate">{statusLine || '—'}</div>
    </Card>
  );
}
