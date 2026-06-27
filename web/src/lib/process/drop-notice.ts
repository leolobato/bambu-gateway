import { toast } from 'sonner';
import type { ProcessOverrideApplied } from './types';
import type { FilamentOverrideApplied } from '@/lib/api/types';

/**
 * Toast a non-blocking notice when the slicer dropped any per-slot filament overrides.
 * Silent when nothing was sent or every (slot, key) was applied.
 */
export function notifyDroppedFilamentOverrides(
  requested: Record<number, Record<string, string>>,
  applied: FilamentOverrideApplied[] | undefined,
): void {
  const appliedSet = new Set((applied ?? []).map((a) => `${a.slot}:${a.key}`));
  const dropped: string[] = [];
  for (const [slot, keys] of Object.entries(requested)) {
    for (const key of Object.keys(keys)) {
      if (!appliedSet.has(`${slot}:${key}`)) dropped.push(`slot ${slot} · ${key}`);
    }
  }
  if (dropped.length > 0) {
    toast.warning(
      `Some filament settings weren't applied: ${dropped.join(', ')}`,
    );
  }
}

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
