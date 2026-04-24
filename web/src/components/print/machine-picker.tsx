import { useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import type { SettingOption } from '@/components/print/setting-row';
import { cn } from '@/lib/utils';

/**
 * Machine picker with two groups (Your printer / All machines) and a search
 * filter. Used by `<SlicingSettingsGroup/>` to keep the active printer's
 * compatible machines one tap away in catalogs that contain hundreds of
 * vendor entries.
 */
export function MachinePicker({
  label,
  value,
  options,
  /** machine_model of the currently-selected printer, or null if unknown. */
  activeMachineModel,
  onChange,
  disabled = false,
}: {
  label: string;
  value: string;
  options: SettingOption[];
  activeMachineModel: string | null;
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);

  const yours = activeMachineModel
    ? options.filter((o) => o.value === activeMachineModel)
    : [];
  const others = activeMachineModel
    ? options.filter((o) => o.value !== activeMachineModel)
    : options;

  const currentLabel = options.find((o) => o.value === value)?.label ?? '—';

  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <span className="text-sm text-text-0">{label}</span>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            aria-label={label}
            className={cn(
              'flex items-center gap-1 max-w-[60%] py-1 px-2 rounded-md text-text-1 text-sm',
              'hover:text-white hover:bg-surface-2 transition-colors duration-fast',
              'disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent',
            )}
          >
            <span className="truncate">{currentLabel}</span>
            <ChevronDown className="w-3.5 h-3.5 shrink-0 opacity-50" aria-hidden />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="end"
          className="w-[320px] p-0 bg-bg-1 border-border"
        >
          <Command className="bg-transparent">
            <CommandInput placeholder="Search machines…" />
            <CommandList>
              <CommandEmpty>No machines match.</CommandEmpty>
              {yours.length > 0 && (
                <CommandGroup heading="Your printer">
                  {yours.map((opt) => (
                    <MachineRow
                      key={opt.value}
                      opt={opt}
                      selected={opt.value === value}
                      onSelect={() => {
                        onChange(opt.value);
                        setOpen(false);
                      }}
                    />
                  ))}
                </CommandGroup>
              )}
              <CommandGroup heading={yours.length > 0 ? 'All machines' : undefined}>
                {others.map((opt) => (
                  <MachineRow
                    key={opt.value}
                    opt={opt}
                    selected={opt.value === value}
                    onSelect={() => {
                      onChange(opt.value);
                      setOpen(false);
                    }}
                  />
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  );
}

function MachineRow({
  opt,
  selected,
  onSelect,
}: {
  opt: SettingOption;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <CommandItem value={opt.label} onSelect={onSelect} className="flex items-center gap-2">
      <span className={cn('flex-1 truncate', opt.fromFileMismatch && 'italic text-text-2')}>
        {opt.label}
        {opt.fromFileMismatch && ' (from file — different printer)'}
      </span>
      {selected && <Check className="w-4 h-4 text-accent shrink-0" aria-hidden />}
    </CommandItem>
  );
}
