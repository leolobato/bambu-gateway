import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { SettingsTransferInfo } from '@/lib/api/types';

/**
 * Read-only collapsed summary of the slicer's settings_transfer report.
 * Emitted alongside the SSE `result` event when the slicer applied or
 * discarded process/filament customizations.
 */
export function SettingsTransferNote({ info }: { info: SettingsTransferInfo | null }) {
  const [open, setOpen] = useState(false);
  if (!info) return null;
  const transferredCount = info.transferred?.length ?? 0;
  const filamentNotes = info.filaments ?? [];
  const hasContent = transferredCount > 0 || filamentNotes.length > 0;
  if (!hasContent) return null;

  return (
    <div className="flex flex-col gap-1 text-[12px] text-text-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="self-start flex items-center gap-1 hover:text-white"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5" aria-hidden /> : <ChevronRight className="w-3.5 h-3.5" aria-hidden />}
        Settings transferred ({transferredCount + filamentNotes.length})
      </button>
      {open && (
        <ul className="list-disc list-inside font-mono">
          {info.transferred?.map((s) => (
            <li key={`t-${s.key}`}>
              {s.key}: {s.value}
              {s.original ? ` (was ${s.original})` : ''}
            </li>
          ))}
          {filamentNotes.map((f) => (
            <li key={`f-${f.slot}`}>
              Slot {f.slot}: {f.status}
              {f.discarded && f.discarded.length > 0 ? ` — discarded ${f.discarded.join(', ')}` : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
