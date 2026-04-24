import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select';
import { Card } from '@/components/ui/card';
import type { PlateInfo, ThreeMFInfo } from '@/lib/api/types';
import { cn } from '@/lib/utils';

export function PlateCard({
  filename,
  info,
  selectedPlateId,
  onSelectPlate,
  onClear,
  disabled = false,
}: {
  filename: string;
  info: ThreeMFInfo;
  /** Currently-selected plate id (1-based). */
  selectedPlateId: number;
  onSelectPlate: (plateId: number) => void;
  onClear: () => void;
  disabled?: boolean;
}) {
  const plate = info.plates.find((p) => p.id === selectedPlateId) ?? info.plates[0];
  const multiPlate = info.plates.length > 1;

  return (
    <Card className="p-4 bg-card border-border flex gap-4 items-start">
      <PlateThumb plate={plate} />
      <div className="flex flex-col gap-2 min-w-0 flex-1">
        <div className="text-[15px] font-semibold text-white break-words">{filename}</div>
        {multiPlate ? (
          <Select
            value={String(plate?.id ?? info.plates[0]?.id ?? 1)}
            onValueChange={(v) => onSelectPlate(Number(v))}
            disabled={disabled}
          >
            <SelectTrigger
              aria-label="Select plate"
              className={cn(
                'h-auto py-1 px-2 w-fit border-0 bg-transparent text-text-1 text-xs',
                'focus:ring-0 focus:ring-offset-0',
              )}
            >
              <span className="truncate">
                Plate {plate?.id ?? '—'} of {info.plates.length}
                {plate?.name ? ` · ${plate.name}` : ''}
              </span>
            </SelectTrigger>
            <SelectContent>
              {info.plates.map((p) => (
                <SelectItem key={p.id} value={String(p.id)}>
                  Plate {p.id}
                  {p.name ? ` · ${p.name}` : ''}
                  {p.objects.length > 0 ? ` · ${p.objects.length} object${p.objects.length === 1 ? '' : 's'}` : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <div className="text-xs text-text-1">
            Plate {plate?.id ?? '—'} of {info.plates.length}
          </div>
        )}
        <div className="text-xs text-text-1">
          {info.filaments.length} filament{info.filaments.length === 1 ? '' : 's'}
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
