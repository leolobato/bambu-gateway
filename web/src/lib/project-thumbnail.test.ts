import { describe, expect, test } from 'vitest';
import {
  findSliceJobThumbnailUrl,
  normalizeProjectName,
} from './project-thumbnail';
import type { SliceJob } from './api/types';

function job(partial: Partial<SliceJob>): SliceJob {
  return {
    job_id: partial.job_id ?? 'j1',
    status: partial.status ?? 'ready',
    progress: 100,
    phase: null,
    filename: partial.filename ?? 'model.3mf',
    printer_id: null,
    auto_print: false,
    created_at: partial.created_at ?? '2026-05-13T10:00:00Z',
    updated_at: partial.updated_at ?? '2026-05-13T10:00:00Z',
    estimate: null,
    settings_transfer: null,
    output_size: null,
    error: null,
    has_thumbnail: partial.has_thumbnail ?? true,
    printed: false,
  };
}

describe('normalizeProjectName', () => {
  test('strips .3mf suffix and lowercases', () => {
    expect(normalizeProjectName('Model.3MF')).toBe('model');
  });

  test('strips .gcode.3mf suffix', () => {
    expect(normalizeProjectName('plate_1.gcode.3mf')).toBe('plate_1');
  });

  test('strips Unix and Windows path components', () => {
    expect(normalizeProjectName('Metadata/plate_1.gcode.3mf')).toBe('plate_1');
    expect(normalizeProjectName('C:\\jobs\\model.3mf')).toBe('model');
  });

  test('returns empty string for empty/whitespace input', () => {
    expect(normalizeProjectName('')).toBe('');
    expect(normalizeProjectName('   ')).toBe('');
  });

  test('leaves non-3mf names lowercased and basename-only', () => {
    expect(normalizeProjectName('foo/bar.gcode')).toBe('bar.gcode');
  });
});

describe('findSliceJobThumbnailUrl', () => {
  test('returns null when target name is empty', () => {
    const jobs = [job({ filename: 'model.3mf' })];
    expect(findSliceJobThumbnailUrl('', jobs)).toBeNull();
  });

  test('returns null when no job matches', () => {
    const jobs = [job({ filename: 'other.3mf' })];
    expect(findSliceJobThumbnailUrl('model.3mf', jobs)).toBeNull();
  });

  test('skips jobs without a thumbnail', () => {
    const jobs = [job({ filename: 'model.3mf', has_thumbnail: false })];
    expect(findSliceJobThumbnailUrl('model.3mf', jobs)).toBeNull();
  });

  test('matches across the .gcode.3mf / .3mf rename', () => {
    const jobs = [job({ job_id: 'j-abc', filename: 'model.3mf' })];
    const url = findSliceJobThumbnailUrl('Metadata/model.gcode.3mf', jobs);
    expect(url).toBe('/api/slice-jobs/j-abc/thumbnail');
  });

  test('returns the most recently updated matching job', () => {
    const jobs = [
      job({ job_id: 'old', filename: 'model.3mf', updated_at: '2026-05-12T00:00:00Z' }),
      job({ job_id: 'new', filename: 'model.3mf', updated_at: '2026-05-13T00:00:00Z' }),
    ];
    expect(findSliceJobThumbnailUrl('model.3mf', jobs)).toBe('/api/slice-jobs/new/thumbnail');
  });
});
