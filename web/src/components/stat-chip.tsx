import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

export type StatChipVariant = 'warm' | 'accent' | 'neutral';

const VALUE_COLOR: Record<StatChipVariant, string> = {
  warm: 'text-warm-hot',
  accent: 'text-accent',
  neutral: 'text-white',
};

export function StatChip({
  label,
  value,
  unit,
  variant = 'neutral',
  chevron = false,
  onClick,
}: {
  label: string;
  /** Pre-formatted main value (e.g. "24°", "Sport"). Rendered in tabular-nums. */
  value: string;
  /** Smaller suffix in `text-2` next to the value (e.g. "/0°"). */
  unit?: string;
  variant?: StatChipVariant;
  chevron?: boolean;
  onClick?: () => void;
}) {
  const Component = onClick ? 'button' : 'div';
  return (
    <Component
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className={cn(
        'flex flex-col gap-1.5 p-3 rounded-xl bg-card text-left transition-colors duration-fast',
        onClick && 'hover:bg-surface-2',
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
          {label}
        </span>
        {chevron && <ChevronRight className="w-3.5 h-3.5 text-text-2" aria-hidden />}
      </div>
      <div className={cn('flex items-baseline gap-1 font-mono tabular-nums', VALUE_COLOR[variant])}>
        <span className="text-[22px] font-bold leading-none">{value}</span>
        {unit && <span className="text-[13px] font-normal text-text-2">{unit}</span>}
      </div>
    </Component>
  );
}
