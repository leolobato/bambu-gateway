import { useEffect, useRef, useState } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import {
  ToggleGroup,
  ToggleGroupItem,
} from '@/components/ui/toggle-group';
import { Minus, Plus } from 'lucide-react';
import type { EditorProps } from './types';

const PERCENT_TYPES = new Set(['coPercent', 'coPercents']);
const INT_TYPES = new Set(['coInt', 'coInts']);
const MIXED_TYPES = new Set(['coFloatOrPercent', 'coFloatsOrPercents']);

function parseDraft(raw: string): { num: string; isPercent: boolean } {
  const trimmed = raw.trim();
  if (trimmed.endsWith('%')) {
    return { num: trimmed.slice(0, -1).trim(), isPercent: true };
  }
  return { num: trimmed, isPercent: false };
}

function clamp(num: number, min: number | null, max: number | null): number {
  if (min !== null && num < min) return min;
  if (max !== null && num > max) return max;
  return num;
}

export function NumberEditor({ option, value, onCommit, onValidityChange }: EditorProps) {
  const initial = parseDraft(value);
  const isPercentLockedSuffix = PERCENT_TYPES.has(option.type);
  const isMixed = MIXED_TYPES.has(option.type);
  const isInt = INT_TYPES.has(option.type);

  const [draft, setDraft] = useState(initial.num);
  const [unit, setUnit] = useState<'mm' | '%'>(
    isPercentLockedSuffix || initial.isPercent ? '%' : 'mm',
  );
  const lastCommitted = useRef(value);

  useEffect(() => {
    // External value changed (e.g. revert): resync.
    if (value !== lastCommitted.current) {
      const parsed = parseDraft(value);
      setDraft(parsed.num);
      if (!isPercentLockedSuffix) setUnit(parsed.isPercent ? '%' : 'mm');
      lastCommitted.current = value;
    }
  }, [value, isPercentLockedSuffix]);

  function commit(): void {
    const num = isInt ? parseInt(draft, 10) : parseFloat(draft);
    if (Number.isNaN(num)) {
      onValidityChange?.(false);
      return;
    }
    const clamped = clamp(num, option.min, option.max);
    onValidityChange?.(true);
    const formatted = String(clamped);
    const final = isPercentLockedSuffix || (isMixed && unit === '%')
      ? `${formatted}%`
      : formatted;
    setDraft(formatted);
    lastCommitted.current = final;
    onCommit(final);
  }

  function step(direction: 1 | -1) {
    const num = parseFloat(draft);
    if (Number.isNaN(num)) return;
    const next = clamp(num + direction, option.min, option.max);
    setDraft(String(next));
    const final = isPercentLockedSuffix || (isMixed && unit === '%') ? `${next}%` : String(next);
    lastCommitted.current = final;
    onCommit(final);
  }

  return (
    <div className="flex items-center gap-2">
      {isInt && (
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Decrement"
          onClick={() => step(-1)}
        >
          <Minus className="size-4" />
        </Button>
      )}
      <Input
        type="number"
        inputMode={isInt ? 'numeric' : 'decimal'}
        step={isInt ? '1' : 'any'}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            commit();
            (e.target as HTMLInputElement).blur();
          }
        }}
        className="w-32 text-right tabular-nums"
        aria-label={option.label}
      />
      {isInt && (
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Increment"
          onClick={() => step(1)}
        >
          <Plus className="size-4" />
        </Button>
      )}
      {isMixed && (
        <ToggleGroup
          type="single"
          value={unit}
          onValueChange={(v) => v && setUnit(v as 'mm' | '%')}
          aria-label="Unit"
        >
          <ToggleGroupItem value="mm">{option.sidetext || 'mm'}</ToggleGroupItem>
          <ToggleGroupItem value="%">%</ToggleGroupItem>
        </ToggleGroup>
      )}
      {!isMixed && (
        <span className="text-xs text-muted-foreground tabular-nums">
          {isPercentLockedSuffix ? '%' : option.sidetext}
        </span>
      )}
    </div>
  );
}
