import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';

export interface SettingOption {
  value: string;
  label: string;
  /** Italicized / dimmed when the option came from the 3MF but isn't in the catalog. */
  fromFileMismatch?: boolean;
}

export function SettingRow({
  label,
  value,
  options,
  onChange,
  disabled = false,
}: {
  label: string;
  value: string;
  options: SettingOption[];
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <span className="text-sm text-text-0">{label}</span>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger
          aria-label={label}
          className={cn(
            'h-auto py-1 px-2 max-w-[60%] border-0 bg-transparent text-text-1 text-sm',
            'focus:ring-0 focus:ring-offset-0',
          )}
        >
          <span className="truncate">{labelFor(value, options)}</span>
        </SelectTrigger>
        <SelectContent>
          {options.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>
              <span className={cn(opt.fromFileMismatch && 'italic text-text-2')}>
                {opt.label}
                {opt.fromFileMismatch && ' (from file — different printer)'}
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function labelFor(value: string, options: SettingOption[]): string {
  const opt = options.find((o) => o.value === value);
  return opt?.label ?? '—';
}
