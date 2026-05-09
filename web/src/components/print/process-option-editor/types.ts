import type { ProcessOption } from '@/lib/process/types';

export interface EditorProps {
  option: ProcessOption;
  value: string;
  /** Called when the user has committed a new value. The parent decides whether to write to overrides. */
  onCommit(next: string): void;
  /** Called whenever the draft changes (used for slider's local state). Optional. */
  onDraftChange?(next: string): void;
  /** Set when the editor's current draft is known invalid (e.g. out of range). Suppresses commit. */
  onValidityChange?(valid: boolean): void;
}
