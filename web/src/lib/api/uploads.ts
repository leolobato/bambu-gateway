import { fetchJson } from './client';
import type { UploadState } from './types';

export async function getUploadState(uploadId: string): Promise<UploadState> {
  return fetchJson<UploadState>(`/api/uploads/${encodeURIComponent(uploadId)}`);
}

export async function cancelUpload(uploadId: string): Promise<void> {
  await fetchJson<unknown>(`/api/uploads/${encodeURIComponent(uploadId)}/cancel`, {
    method: 'POST',
  });
}
