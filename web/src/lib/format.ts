/** "1h 23m" / "23m" / "—" for ≤0 or NaN. */
export function formatRemaining(minutes: number | null | undefined): string {
  if (minutes == null || !Number.isFinite(minutes) || minutes <= 0) return '—';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

/**
 * "24°/0°" — current with target as a `text-2` suffix.
 * The component is responsible for splitting current/suffix so it can style
 * the target portion separately. This helper just rounds.
 */
export function formatTemp(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return `${Math.round(value)}°`;
}
