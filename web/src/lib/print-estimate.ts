import type { PrintEstimate } from '@/lib/api/types';

const lengthFormatter = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const massFormatter = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function hasPrintEstimate(estimate: PrintEstimate | null | undefined): estimate is PrintEstimate {
  if (!estimate) return false;
  return Object.values(estimate).some((value) => value != null);
}

export function formatEstimateLength(millimeters: number | null | undefined): string | null {
  if (millimeters == null) return null;
  return `${lengthFormatter.format(millimeters / 1000)} m`;
}

export function formatEstimateMass(grams: number | null | undefined): string | null {
  if (grams == null) return null;
  return `${massFormatter.format(grams)} g`;
}

export function formatEstimateDuration(seconds: number | null | undefined): string | null {
  if (seconds == null) return null;
  const wholeSeconds = Math.max(0, Math.round(seconds));
  if (wholeSeconds < 60) return `${wholeSeconds}s`;
  const minutes = Math.floor(wholeSeconds / 60);
  const remainingSeconds = wholeSeconds % 60;
  if (minutes < 60) return `${minutes}m ${remainingSeconds}s`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours}h ${remainingMinutes}m`;
}
