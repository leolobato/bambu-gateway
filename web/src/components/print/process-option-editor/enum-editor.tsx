import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { EditorProps } from './types';

export function EnumEditor({ option, value, onCommit }: EditorProps) {
  const values = option.enumValues ?? [];
  const labels = option.enumLabels ?? values;
  return (
    <Select value={value} onValueChange={onCommit}>
      <SelectTrigger className="w-56" aria-label={option.label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {values.map((v, i) => (
          <SelectItem key={v} value={v}>
            {labels[i] ?? v}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
