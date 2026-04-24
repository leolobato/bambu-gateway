import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { cn } from '@/lib/utils';

export type InfoBannerVariant = 'info' | 'warn' | 'success' | 'error';

const VARIANT_CLASSES: Record<InfoBannerVariant, string> = {
  info:    'bg-accent/10 border-accent/40 text-text-0 [&>svg+div]:text-text-0',
  warn:    'bg-warm/10 border-warm/40 text-text-0',
  success: 'bg-success/10 border-success/40 text-text-0',
  error:   'bg-danger/10 border-danger/40 text-text-0',
};

export function InfoBanner({
  variant,
  title,
  message,
  details,
  action,
}: {
  variant: InfoBannerVariant;
  title: string;
  /** Short body line (always shown). */
  message?: string;
  /** Long expandable body (e.g. an error stack). When present, a Details toggle appears. */
  details?: string;
  /** Optional right-aligned action button (e.g. Retry). */
  action?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <Alert className={cn('flex flex-col gap-2', VARIANT_CLASSES[variant])}>
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <AlertTitle className="text-[14px] font-semibold text-white">{title}</AlertTitle>
          {message && (
            <AlertDescription className="text-sm text-text-0">{message}</AlertDescription>
          )}
        </div>
        {action}
      </div>
      {details && (
        <>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="self-start flex items-center gap-1 text-[12px] text-text-1 hover:text-white"
          >
            {open ? <ChevronDown className="w-3.5 h-3.5" aria-hidden /> : <ChevronRight className="w-3.5 h-3.5" aria-hidden />}
            Details
          </button>
          {open && (
            <pre className="text-[12px] font-mono text-text-1 whitespace-pre-wrap break-words bg-bg-0/40 rounded p-2">
              {details}
            </pre>
          )}
        </>
      )}
    </Alert>
  );
}
