import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Loader2, Maximize2 } from 'lucide-react';
import type { PrinterStatus } from '@/lib/api/types';
import { cameraStreamUrl, getCameraStatus } from '@/lib/api/camera';
import { cn } from '@/lib/utils';

export function CameraFeed({ printer }: { printer: PrinterStatus }) {
  const camera = printer.camera;
  const transport = camera?.transport ?? null;
  const supported = transport === 'tcp_jpeg';

  if (!camera) return <Placeholder text="Camera not available for this printer." />;
  if (!supported) {
    return (
      <Placeholder
        text={
          transport === 'rtsps'
            ? 'RTSPS cameras (X1 family) aren't supported in the web UI yet.'
            : 'Camera not available for this printer.'
        }
      />
    );
  }

  return <SupportedFeed printerId={printer.id} online={printer.online} />;
}

function SupportedFeed({ printerId, online }: { printerId: string; online: boolean }) {
  const [retryToken, setRetryToken] = useState(0);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const statusQuery = useQuery({
    queryKey: ['camera-status', printerId],
    queryFn: () => getCameraStatus(printerId),
    refetchInterval: 2_000,
    enabled: online,
  });

  // Reset the cache-buster when the printer changes so the previous printer's
  // MJPEG connection is dropped immediately.
  useEffect(() => {
    setRetryToken(Date.now());
  }, [printerId]);

  const state = online ? statusQuery.data?.state ?? 'connecting' : 'failed';
  const error = statusQuery.data?.error ?? (online ? null : 'Printer offline.');

  const onFullscreen = () => {
    wrapperRef.current?.requestFullscreen?.().catch(() => {
      /* user gesture lost or already fullscreen */
    });
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-sm text-text-1">
        <span className={cn('w-2 h-2 rounded-full', dotClass(state))} aria-hidden />
        <span>Printer</span>
      </div>
      <div
        ref={wrapperRef}
        className="relative w-full aspect-video bg-black rounded-xl overflow-hidden border border-border"
      >
        {online && (
          <img
            src={cameraStreamUrl(printerId, retryToken)}
            alt="Printer camera"
            className="w-full h-full object-contain"
            draggable={false}
          />
        )}

        <button
          type="button"
          onClick={onFullscreen}
          aria-label="Enter fullscreen"
          className="absolute top-2 right-2 p-1.5 rounded-full bg-black/50 text-white/90 hover:bg-black/70"
        >
          <Maximize2 className="w-4 h-4" />
        </button>

        {(state === 'connecting' || state === 'idle') && <ConnectingOverlay />}
        {state === 'failed' && (
          <FailedOverlay
            message={error ?? 'Camera disconnected.'}
            onRetry={() => setRetryToken((t) => t + 1)}
          />
        )}
      </div>
    </div>
  );
}

function ConnectingOverlay() {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/50 text-white">
      <Loader2 className="w-6 h-6 animate-spin" aria-hidden />
      <div className="text-xs">Connecting…</div>
    </div>
  );
}

function FailedOverlay({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/70 text-white px-4 text-center">
      <AlertTriangle className="w-6 h-6 text-warm" aria-hidden />
      <div className="text-xs">{message}</div>
      <button
        type="button"
        onClick={onRetry}
        className="px-3 py-1.5 rounded-full bg-accent-strong text-white text-xs font-semibold hover:bg-accent"
      >
        Retry
      </button>
    </div>
  );
}

function Placeholder({ text }: { text: string }) {
  return (
    <div className="w-full aspect-video bg-surface-1 border border-border rounded-xl flex items-center justify-center text-sm text-text-1 px-6 text-center">
      {text}
    </div>
  );
}

function dotClass(state: string): string {
  switch (state) {
    case 'streaming': return 'bg-success';
    case 'connecting':
    case 'idle': return 'bg-warm';
    case 'failed': return 'bg-danger';
    default: return 'bg-text-2';
  }
}
