import { toast } from 'sonner';
import type { ProcessOverrideApplied } from './types';

/**
 * Toast a non-blocking notice when the slicer dropped a subset of submitted overrides.
 * Silent when nothing was sent or every key was applied.
 */
export function notifyDroppedOverrides(
  sent: Record<string, string>,
  applied: ProcessOverrideApplied[] | undefined,
): void {
  const sentKeys = Object.keys(sent);
  if (sentKeys.length === 0) return;
  const appliedKeys = new Set((applied ?? []).map((o) => o.key));
  const dropped = sentKeys.filter((k) => !appliedKeys.has(k));
  if (dropped.length === 0) return;
  toast.message(
    `${appliedKeys.size} setting(s) sent, ${dropped.length} ignored: ${dropped.join(', ')}`,
  );
}
