import { Card } from '@/components/ui/card';
import type { PlateInfo, ThreeMFInfo } from '@/lib/api/types';
import { cn } from '@/lib/utils';

export function PlateCard({
  filename,
  info,
  selectedPlateId,
  onClear,
}: {
  filename: string;
  info: ThreeMFInfo;
  /** Currently-selected plate id (1-based). */
  selectedPlateId: number;
  onClear: () => void;
}) {
  const plate = info.plates.find((p) => p.id === selectedPlateId) ?? info.plates[0];
  const layers = info.print_profile.layer_height
    ? estimateLayers(info, plate)
    : null;

  return (
    <Card className="p-4 bg-card border-border flex gap-4 items-start">
      <PlateThumb plate={plate} />
      <div className="flex flex-col gap-2 min-w-0 flex-1">
        <div className="text-[15px] font-semibold text-white break-words">{filename}</div>
        <div className="text-xs text-text-1">
          Plate {plate?.id ?? '—'} of {info.plates.length} · {info.filaments.length} filament{info.filaments.length === 1 ? '' : 's'}
          {layers != null && (
            <>
              {' · '}
              <span className="font-mono tabular-nums">{layers} layers</span>
            </>
          )}
        </div>
        <button
          type="button"
          onClick={onClear}
          className="self-start text-[12px] font-semibold text-danger hover:underline"
        >
          Clear
        </button>
      </div>
    </Card>
  );
}

function PlateThumb({ plate }: { plate: PlateInfo | undefined }) {
  const hasThumb = !!plate?.thumbnail;
  return (
    <div
      className={cn(
        'shrink-0 w-[140px] h-[140px] rounded-xl bg-bg-0 overflow-hidden flex items-center justify-center',
        !hasThumb && 'border border-dashed border-text-2',
      )}
    >
      {hasThumb ? (
        <img
          src={plate!.thumbnail}
          alt={`Plate ${plate!.id} preview`}
          className="w-full h-full object-contain"
          draggable={false}
        />
      ) : (
        <span className="text-xs text-text-2">No preview</span>
      )}
    </div>
  );
}

/**
 * Layers can't be derived exactly without slicing; we don't display a value
 * unless the 3MF has both layer_height and the printer reports plate height.
 * Returning null hides the segment cleanly.
 */
function estimateLayers(_info: ThreeMFInfo, _plate: PlateInfo | undefined): number | null {
  // The backend doesn't expose total_layers in parse-3mf — we can revisit
  // when slicing returns the count via the SSE result.
  return null;
}
