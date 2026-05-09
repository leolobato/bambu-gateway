import { useEffect, useRef, useState } from 'react';
import { Slider } from '@/components/ui/slider';
import { Input } from '@/components/ui/input';
import type { EditorProps } from './types';

export function SliderEditor({ option, value, onCommit }: EditorProps) {
  const min = option.min ?? 0;
  const max = option.max ?? 100;
  const initial = parseFloat(value);
  const [draft, setDraft] = useState<number>(Number.isFinite(initial) ? initial : min);
  const lastCommitted = useRef(value);

  useEffect(() => {
    if (value === lastCommitted.current) return;
    const next = parseFloat(value);
    if (Number.isFinite(next)) setDraft(next);
    lastCommitted.current = value;
  }, [value]);

  function commit(next: number) {
    const str = String(next);
    if (str === lastCommitted.current) return;
    lastCommitted.current = str;
    onCommit(str);
  }

  return (
    <div className="flex items-center gap-3 w-full">
      <Slider
        min={min}
        max={max}
        step={option.type === 'coInt' || option.type === 'coInts' ? 1 : 0.01}
        value={[draft]}
        onValueChange={(vals) => setDraft(vals[0])}
        onValueCommit={(vals) => commit(vals[0])}
        aria-label={option.label}
        className="flex-1"
      />
      <Input
        type="number"
        inputMode="decimal"
        value={draft}
        onChange={(e) => {
          const next = parseFloat(e.target.value);
          if (Number.isFinite(next)) setDraft(next);
        }}
        onBlur={() => commit(draft)}
        className="w-24 tabular-nums text-right"
        aria-label={`${option.label} value`}
      />
      {option.sidetext && (
        <span className="text-xs text-muted-foreground">{option.sidetext}</span>
      )}
    </div>
  );
}
