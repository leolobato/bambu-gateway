import type { ProcessModifications, ProcessOption, ProcessOptionsCatalogue } from './types';

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
 * Maps an enum option's raw value (e.g. "no_brim") to its catalogue label
 * (e.g. "No brim"). Falls through to the raw value for non-enum options or
 * when the value isn't in `enum_values`. The editor keeps using the raw
 * value so save/revert round-trips stay exact — only summary rows and
 * footers should call this.
 */
export function displayValue(value: string, option: ProcessOption | undefined | null): string {
  if (!option) return value;
  if (option.type === 'coEnum') {
    const values = option.enumValues;
    const labels = option.enumLabels;
    if (!values || !labels) return value;
    const i = values.indexOf(value);
    if (i < 0 || i >= labels.length) return value;
    return labels[i];
  }
  // Strip a trailing unit that matches sidetext so the unit isn't rendered
  // twice when callers display sidetext separately next to the value.
  const suffix = option.sidetext;
  if (suffix && value.length > suffix.length && value.endsWith(suffix)) {
    return value.slice(0, -suffix.length).trimEnd();
  }
  return value;
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
