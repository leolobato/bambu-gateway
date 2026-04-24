import { fetchJson } from './client';
import type { SlicerFilament, SlicerMachine, SlicerPlateType, SlicerProcess } from './types';

export async function getSlicerMachines(): Promise<SlicerMachine[]> {
  return fetchJson<SlicerMachine[]>('/api/slicer/machines');
}

export async function getSlicerProcesses(machine?: string): Promise<SlicerProcess[]> {
  const path = machine
    ? `/api/slicer/processes?machine=${encodeURIComponent(machine)}`
    : '/api/slicer/processes';
  return fetchJson<SlicerProcess[]>(path);
}

export interface GetSlicerFilamentsParams {
  machine?: string;
  amsAssignable?: boolean;
}

export async function getSlicerFilaments(
  params: GetSlicerFilamentsParams = {},
): Promise<SlicerFilament[]> {
  const qs = new URLSearchParams();
  if (params.machine) qs.set('machine', params.machine);
  if (params.amsAssignable !== undefined) qs.set('ams_assignable', String(params.amsAssignable));
  const suffix = qs.toString() ? `?${qs}` : '';
  return fetchJson<SlicerFilament[]>(`/api/slicer/filaments${suffix}`);
}

export async function getSlicerPlateTypes(): Promise<SlicerPlateType[]> {
  return fetchJson<SlicerPlateType[]>('/api/slicer/plate-types');
}
