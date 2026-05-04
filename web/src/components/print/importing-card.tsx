import { Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

/**
 * Indeterminate "we got the file, working on it" feedback rendered between
 * file pick and the slicing form. Covers /api/parse-3mf + AMS tray matcher.
 */
export function ImportingCard({
  filename,
  onCancel,
}: {
  filename: string;
  onCancel: () => void;
}) {
  return (
    <Card className="p-4 bg-card border border-accent/40 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-white">
          <Loader2 className="w-4 h-4 text-accent animate-spin" aria-hidden />
          <span className="text-[14px] font-semibold">Reading 3MF…</span>
        </div>
        <Button
          type="button"
          onClick={onCancel}
          variant="ghost"
          className="h-auto py-1 px-2 text-danger text-[13px] font-semibold hover:text-danger/80"
        >
          Cancel
        </Button>
      </div>
      <div className="text-[12px] font-mono text-text-1 truncate">{filename}</div>
    </Card>
  );
}
