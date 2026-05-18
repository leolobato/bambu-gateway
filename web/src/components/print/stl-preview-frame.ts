import type { StlDraftObject, StlDraftScene } from '@/lib/api/types';

export interface PreviewBounds {
  min: [number, number, number];
  max: [number, number, number];
}

export interface PreviewCameraPose {
  position: [number, number, number];
  target: [number, number, number];
}

function includePoint(bounds: PreviewBounds, p: [number, number, number]) {
  bounds.min = [
    Math.min(bounds.min[0], p[0]),
    Math.min(bounds.min[1], p[1]),
    Math.min(bounds.min[2], p[2]),
  ];
  bounds.max = [
    Math.max(bounds.max[0], p[0]),
    Math.max(bounds.max[1], p[1]),
    Math.max(bounds.max[2], p[2]),
  ];
}

function objectWorldCorners(obj: StlDraftObject): [number, number, number][] {
  const x0 = obj.bbox.min[0] + obj.transform.offset[0] + obj.mesh_transform.offset[0];
  const y0 = obj.bbox.min[1] + obj.transform.offset[1] + obj.mesh_transform.offset[1];
  const z0 = obj.bbox.min[2] + obj.transform.offset[2] + obj.mesh_transform.offset[2];
  const x1 = obj.bbox.max[0] + obj.transform.offset[0] + obj.mesh_transform.offset[0];
  const y1 = obj.bbox.max[1] + obj.transform.offset[1] + obj.mesh_transform.offset[1];
  const z1 = obj.bbox.max[2] + obj.transform.offset[2] + obj.mesh_transform.offset[2];
  return [
    [x0, y0, z0],
    [x0, y0, z1],
    [x0, y1, z0],
    [x0, y1, z1],
    [x1, y0, z0],
    [x1, y0, z1],
    [x1, y1, z0],
    [x1, y1, z1],
  ];
}

export function buildPreviewBounds(scene: StlDraftScene): PreviewBounds {
  const bedWidth = scene.bed.width || 256;
  const bedDepth = scene.bed.depth || 256;
  if (scene.objects.length === 0) return { min: [0, 0, 0], max: [bedWidth, bedDepth, 0] };
  const bounds: PreviewBounds = {
    min: [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY],
    max: [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY],
  };
  for (const obj of scene.objects) {
    for (const corner of objectWorldCorners(obj)) includePoint(bounds, corner);
  }
  return bounds;
}

export function cameraPoseForBounds(bounds: PreviewBounds): PreviewCameraPose {
  const cx = (bounds.min[0] + bounds.max[0]) / 2;
  const cy = (bounds.min[1] + bounds.max[1]) / 2;
  const cz = (bounds.min[2] + bounds.max[2]) / 2;
  const sx = Math.max(1, bounds.max[0] - bounds.min[0]);
  const sy = Math.max(1, bounds.max[1] - bounds.min[1]);
  const sz = Math.max(1, bounds.max[2] - bounds.min[2]);
  const size = Math.max(sx, sy, sz, 60);
  return {
    target: [cx, cy, cz],
    position: [cx + size * 0.8, cy - size * 1.25, cz + size * 0.95],
  };
}
