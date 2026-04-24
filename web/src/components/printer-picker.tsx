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
import type { PrinterStatus } from '@/lib/api/types';
import { cn } from '@/lib/utils';

function dotColorClass(p: PrinterStatus): string {
  if (!p.online) return 'bg-text-2';
  switch (p.state) {
    case 'printing':
    case 'preparing':
      return 'bg-accent';
    case 'paused':
      return 'bg-warm';
    case 'error':
      return 'bg-danger';
    default:
      return 'bg-success';
  }
}

export function PrinterPicker({
  printers,
  activeId,
  onChange,
}: {
  printers: PrinterStatus[];
  activeId: string | null;
  onChange: (id: string) => void;
}) {
  if (printers.length === 0) return null;
  if (printers.length <= 3) return <SegmentedPicker printers={printers} activeId={activeId} onChange={onChange} />;
  return <CommandPicker printers={printers} activeId={activeId} onChange={onChange} />;
}

function SegmentedPicker({
  printers,
  activeId,
  onChange,
}: {
  printers: PrinterStatus[];
  activeId: string | null;
  onChange: (id: string) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Active printer"
      className="flex gap-1 overflow-x-auto bg-bg-1 border border-border rounded-full p-[3px]"
    >
      {printers.map((p) => {
        const active = p.id === activeId;
        return (
          <button
            key={p.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(p.id)}
            className={cn(
              'flex items-center gap-2 px-3.5 py-2 rounded-full text-[13px] font-medium whitespace-nowrap transition-colors duration-fast',
              active ? 'bg-surface-3 text-white' : 'text-text-1 hover:text-white',
            )}
          >
            <span className={cn('w-1.5 h-1.5 rounded-full', dotColorClass(p))} aria-hidden />
            {p.name}
          </button>
        );
      })}
    </div>
  );
}

function CommandPicker({
  printers,
  activeId,
  onChange,
}: {
  printers: PrinterStatus[];
  activeId: string | null;
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const active = printers.find((p) => p.id === activeId) ?? printers[0];

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Active printer: ${active.name}. Click to change.`}
          className="flex items-center gap-2 px-3.5 py-2 rounded-full bg-bg-1 border border-border text-[13px] font-medium text-white"
        >
          <span className={cn('w-1.5 h-1.5 rounded-full', dotColorClass(active))} aria-hidden />
          {active.name}
          <ChevronDown className="w-3.5 h-3.5 text-text-1" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[280px] p-0 bg-bg-1 border-border">
        <Command className="bg-transparent">
          <CommandInput placeholder="Search printers…" />
          <CommandList>
            <CommandEmpty>No printers found.</CommandEmpty>
            <CommandGroup>
              {printers.map((p) => {
                const isActive = p.id === active.id;
                return (
                  <CommandItem
                    key={p.id}
                    value={`${p.name} ${p.id}`}
                    onSelect={() => {
                      onChange(p.id);
                      setOpen(false);
                    }}
                    className="flex items-center gap-2"
                  >
                    <span className={cn('w-1.5 h-1.5 rounded-full', dotColorClass(p))} aria-hidden />
                    <span className="flex-1">{p.name}</span>
                    {isActive && <Check className="w-4 h-4 text-accent" aria-hidden />}
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
