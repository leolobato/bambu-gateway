import { Switch } from '@/components/ui/switch';
import type { EditorProps } from './types';

/** `coBool` — emits "1" / "0". */
export function BoolEditor({ value, onCommit }: EditorProps) {
  const checked = value === '1' || value.toLowerCase() === 'true';
  return (
    <div className="flex items-center justify-end">
      <Switch
        checked={checked}
        onCheckedChange={(next) => onCommit(next ? '1' : '0')}
        aria-label="Toggle value"
      />
    </div>
  );
}
