import { sliceJobThumbnailUrl } from './api/slice-jobs';
import type { SliceJob } from './api/types';

/**
 * Mirrors `app/live_activity_thumbnail.py::_normalize_filename` so the
 * dashboard can match `printer.job.file_name` (which firmware sometimes
 * reports as `Metadata/plate_1.gcode.3mf`) against a slice job stored as
 * `plate_1.3mf`.
 */
export function normalizeProjectName(name: string): string {
  const trimmed = (name ?? '').trim().toLowerCase();
  if (!trimmed) return '';
  const basename = trimmed.split('/').pop()!.split('\\').pop()!;
  for (const suffix of ['.gcode.3mf', '.3mf']) {
    if (basename.endsWith(suffix)) {
      return basename.slice(0, -suffix.length);
    }
  }
  return basename;
}

/**
 * Returns the thumbnail URL for the most recently updated slice job whose
 * normalized filename matches `target`, or `null` when no usable match
 * exists. Jobs without a thumbnail are ignored.
 */
export function findSliceJobThumbnailUrl(
  target: string,
  jobs: readonly SliceJob[],
): string | null {
  const normTarget = normalizeProjectName(target);
  if (!normTarget) return null;
  const candidates = jobs.filter(
    (j) => j.has_thumbnail && normalizeProjectName(j.filename) === normTarget,
  );
  if (candidates.length === 0) return null;
  candidates.sort(
    (a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at),
  );
  return sliceJobThumbnailUrl(candidates[0].job_id);
}
