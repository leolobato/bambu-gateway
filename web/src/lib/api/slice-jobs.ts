import { ApiError, fetchJson } from './client';
import type { SliceJob, SliceJobListResponse, SliceJobStatus } from './types';

export async function listSliceJobs(): Promise<SliceJob[]> {
  const res = await fetchJson<SliceJobListResponse>('/api/slice-jobs');
  return res.jobs;
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

export function sliceJobOutputUrl(jobId: string): string {
  return `/api/slice-jobs/${encodeURIComponent(jobId)}/output`;
}
