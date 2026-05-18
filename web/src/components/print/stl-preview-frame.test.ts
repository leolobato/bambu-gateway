import { describe, expect, test } from 'vitest';
import { buildPreviewBounds, cameraPoseForBounds } from './stl-preview-frame';
import type { StlDraftScene } from '@/lib/api/types';

function scene(offset: [number, number, number]): StlDraftScene {
  return {
    draft_token: 'draft1',
    source_filename: 'part.stl',
    source_url: '/api/stl-drafts/draft1/source.stl',
    bed: { width: 180, depth: 180, printable_area: [] },
    warnings: [],
    actions: [],
    objects: [{
      id: 'obj1',
      name: 'part',
      printable: true,
      transform: { offset, rotation: [0, 0, 0], scale: [1, 1, 1] },
      mesh_transform: { offset: [0, 0, 0], rotation: [0, 0, 0], scale: [1, 1, 1] },
      bbox: { min: [0, 0, 0], max: [20, 10, 8] },
    }],
  };
}

describe('STL preview framing', () => {
  test('targets placed object center instead of bed center', () => {
    const pose = cameraPoseForBounds(buildPreviewBounds(scene([120, 30, 0])));

    expect(pose.target[0]).toBeCloseTo(130);
    expect(pose.target[1]).toBeCloseTo(35);
    expect(pose.target[2]).toBeCloseTo(4);
  });

  test('uses bed center when no object exists', () => {
    const empty = { ...scene([0, 0, 0]), objects: [] };

    expect(cameraPoseForBounds(buildPreviewBounds(empty)).target).toEqual([90, 90, 0]);
  });
});
