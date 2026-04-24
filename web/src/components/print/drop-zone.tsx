import { useRef } from 'react';
import { createPortal } from 'react-dom';
import { Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export function DropZoneCard({
  onFile,
  targetPrinterName,
}: {
  onFile: (file: File) => void;
  targetPrinterName: string | null;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  return (
    <div className="flex flex-col gap-4">
      {targetPrinterName && (
        <p className="text-sm text-text-1">
          Target printer: <span className="text-text-0">{targetPrinterName}</span>
        </p>
      )}
      <div className="rounded-2xl border border-dashed border-text-2 bg-card flex flex-col items-center gap-3 py-12 px-6 text-center">
        <Upload className="w-7 h-7 text-text-1" aria-hidden />
        <div className="text-[18px] font-semibold text-white">Drop a .3mf file here</div>
        <div className="text-sm text-text-1">Or import from your device</div>
        <input
          ref={inputRef}
          type="file"
          accept=".3mf"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onFile(f);
            // Reset so the same file picked twice still fires onChange.
            e.target.value = '';
          }}
        />
        <Button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="mt-2 rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-10 px-5 text-[14px] font-semibold"
        >
          Choose file…
        </Button>
      </div>
    </div>
  );
}

export function DropOverlay({ visible }: { visible: boolean }) {
  if (typeof document === 'undefined') return null;
  return createPortal(
    <div
      aria-hidden
      className={cn(
        'fixed inset-0 z-50 flex items-center justify-center pointer-events-none transition-opacity duration-fast',
        visible ? 'opacity-100' : 'opacity-0',
      )}
    >
      <div className="absolute inset-0 bg-bg-0/80 backdrop-blur-sm" />
      <div className="relative rounded-2xl border-2 border-dashed border-accent bg-card px-8 py-10 flex flex-col items-center gap-3">
        <Upload className="w-8 h-8 text-accent" aria-hidden />
        <div className="text-[18px] font-semibold text-white">Drop to import</div>
        <div className="text-sm text-text-1">Releases a .3mf file</div>
      </div>
    </div>,
    document.body,
  );
}
