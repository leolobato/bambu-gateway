import { ApiError } from './client';
import type { ThreeMFInfo } from './types';

/**
 * POST /api/parse-3mf with a multipart `file` field.
 * `fetchJson` is not used here because the body is `FormData`, not JSON,
 * and we need the request to set its own Content-Type with the boundary.
 */
export async function parse3mf(file: File): Promise<ThreeMFInfo> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/api/parse-3mf', { method: 'POST', body: fd });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // body wasn't JSON
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as ThreeMFInfo;
}
