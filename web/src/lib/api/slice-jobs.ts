import { ApiError, fetchJson } from './client';
import type { SliceJob, SliceJobListResponse, SliceJobStatus } from './types';

export async function listSliceJobs(): Promise<SliceJob[]> {
  const res = await fetchJson<SliceJobListResponse>('/api/slice-jobs');
  return res.jobs;
}

export async function fetchSliceJob(jobId: string): Promise<SliceJob> {
  return fetchJson<SliceJob>(`/api/slice-jobs/${encodeURIComponent(jobId)}`);
}

export interface SubmitSliceJobArgs {
  file: File;
  printerId?: string;
  plateId: number;
  machineProfile: string;
  processProfile: string;
  filamentProfiles: Record<string, { profile_setting_id: string; tray_slot: number } | string>;
  plateType?: string;
  autoPrint?: boolean;
  processOverrides?: Record<string, string>;
  copies?: number;
}

export async function submitSliceJob(args: SubmitSliceJobArgs): Promise<SliceJob> {
  const fd = new FormData();
  fd.append('file', args.file);
  if (args.printerId) fd.append('printer_id', args.printerId);
  fd.append('plate_id', String(args.plateId));
  fd.append('machine_profile', args.machineProfile);
  fd.append('process_profile', args.processProfile);
  fd.append('filament_profiles', JSON.stringify(args.filamentProfiles));
  if (args.processOverrides && Object.keys(args.processOverrides).length > 0) {
    fd.append('process_overrides', JSON.stringify(args.processOverrides));
  }
  if (args.plateType) fd.append('plate_type', args.plateType);
  if (args.autoPrint) fd.append('auto_print', 'true');
  if (args.copies != null && args.copies > 1) fd.append('copies', String(args.copies));

  const res = await fetch('/api/slice-jobs', { method: 'POST', body: fd });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // body wasn't JSON
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as SliceJob;
}

export async function cancelSliceJob(jobId: string): Promise<SliceJob> {
  return fetchJson<SliceJob>(`/api/slice-jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
  });
}

export async function deleteSliceJob(jobId: string): Promise<void> {
  // Endpoint returns 204 No Content; bypass fetchJson which always parses JSON.
  const res = await fetch(`/api/slice-jobs/${encodeURIComponent(jobId)}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // body wasn't JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
}

export async function clearSliceJobs(
  statuses?: SliceJobStatus[],
): Promise<SliceJob[]> {
  const res = await fetchJson<SliceJobListResponse>('/api/slice-jobs/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ statuses: statuses ?? null }),
  });
  return res.jobs;
}

export function sliceJobInputUrl(jobId: string): string {
  return `/api/slice-jobs/${encodeURIComponent(jobId)}/input`;
}

export function sliceJobOutputUrl(jobId: string): string {
  return `/api/slice-jobs/${encodeURIComponent(jobId)}/output`;
}

export function sliceJobThumbnailUrl(jobId: string): string {
  return `/api/slice-jobs/${encodeURIComponent(jobId)}/thumbnail`;
}
