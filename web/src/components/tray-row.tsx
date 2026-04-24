import { cn } from '@/lib/utils';

export function TrayRow({
  colorDot,
  title,
  subtitle,
  body,
  right,
  highlighted = false,
  onClick,
}: {
  /** Hex color string ("#RRGGBB") or null → outlined empty swatch. */
  colorDot: string | null;
  title: React.ReactNode;
  /** Mono eyebrow line (e.g. "PLA · GFA00"). */
  subtitle?: React.ReactNode;
  /** Body line (filament name, "Empty", etc.). */
  body?: React.ReactNode;
  /** Right-aligned content (chevron, badge, etc.). */
  right?: React.ReactNode;
  /** "In Use" highlight: 1px accent border + accent ring. */
  highlighted?: boolean;
  onClick?: () => void;
}) {
  const Component = onClick ? 'button' : 'div';
  return (
    <Component
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className={cn(
        'flex items-center gap-3 w-full p-3 rounded-2xl bg-card text-left transition-colors duration-fast',
        onClick && 'hover:bg-surface-2',
        highlighted && 'border border-accent shadow-[0_0_0_3px_rgba(96,165,250,0.18)]',
        !highlighted && 'border border-transparent',
      )}
    >
      <span
        className={cn(
          'shrink-0 w-7 h-7 rounded-full',
          colorDot == null && 'border border-dashed border-text-2',
        )}
        style={colorDot ? { backgroundColor: colorDot } : undefined}
        aria-hidden
      />
      <div className="flex-1 min-w-0 flex flex-col gap-0.5">
        <div className="text-[14px] font-semibold text-white truncate">{title}</div>
        {subtitle && (
          <div className="text-[11px] font-mono text-text-1 truncate">{subtitle}</div>
        )}
        {body && <div className="text-[13px] text-text-0 truncate">{body}</div>}
      </div>
      {right && <div className="shrink-0 flex items-center text-text-1">{right}</div>}
    </Component>
  );
}
