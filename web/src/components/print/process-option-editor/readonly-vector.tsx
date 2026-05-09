import { Alert, AlertDescription } from '@/components/ui/alert';
import type { EditorProps } from './types';

export function ReadonlyVectorEditor({ value }: EditorProps) {
  return (
    <div className="space-y-2">
      <code className="text-xs">{value || '—'}</code>
      <Alert>
        <AlertDescription className="text-xs">
          Editing this option type is not yet supported.
        </AlertDescription>
      </Alert>
    </div>
  );
}
