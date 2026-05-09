import { useEffect, useRef, useState } from 'react';
import { Input } from '@/components/ui/input';
import type { EditorProps } from './types';

export function StringEditor({ option, value, onCommit }: EditorProps) {
  const [draft, setDraft] = useState(value);
  const lastCommitted = useRef(value);

  useEffect(() => {
    if (value !== lastCommitted.current) {
      setDraft(value);
      lastCommitted.current = value;
    }
  }, [value]);

  function commit() {
    if (draft !== lastCommitted.current) {
      lastCommitted.current = draft;
      onCommit(draft);
    }
  }

  return (
    <Input
      type="text"
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
      aria-label={option.label}
    />
  );
}
