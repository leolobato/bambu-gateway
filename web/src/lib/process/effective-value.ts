import type { ProcessModifications, ProcessOptionsCatalogue } from './types';

/**
 * Resolves the value to display for `key`, walking the four-rung fallback:
 * user override → 3MF modification → resolved baseline → catalogue default.
 * Returns null when the key is unknown to all four sources.
 */
export function effectiveValue(
  key: string,
  overrides: Record<string, string>,
  modifications: ProcessModifications | null,
  baseline: Record<string, string>,
  catalogue: ProcessOptionsCatalogue | null,
): string | null {
  if (key in overrides) return overrides[key];
  if (modifications && key in modifications.values) return modifications.values[key];
  if (key in baseline) return baseline[key];
  return catalogue?.options[key]?.default ?? null;
}

/**
 * The value a Revert button restores to — same chain as `effectiveValue`,
 * skipping the user override rung.
 */
export function revertTarget(
  key: string,
  modifications: ProcessModifications | null,
  baseline: Record<string, string>,
  catalogue: ProcessOptionsCatalogue | null,
): string | null {
  if (modifications && key in modifications.values) return modifications.values[key];
  if (key in baseline) return baseline[key];
  return catalogue?.options[key]?.default ?? null;
}
