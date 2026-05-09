import type { EditorProps } from './types';

export function ColorEditor({ option, value, onCommit }: EditorProps) {
  return (
    <div className="flex items-center gap-2">
      <input
        type="color"
        value={value || '#000000'}
        onChange={(e) => onCommit(e.target.value.toLowerCase())}
        aria-label={option.label}
        className="size-10 rounded border border-input bg-background"
      />
      <code className="text-xs text-muted-foreground">{value}</code>
    </div>
  );
}
