import { ApiError } from './client';

/**
 * POST /api/print using a stored preview id (skip re-slicing).
 * The endpoint accepts multipart form data; only `preview_id` and
 * (optionally) `printer_id` are needed for the preview path.
 */
export async function printFromPreview(
  previewId: string,
  printerId?: string,
): Promise<void> {
  const fd = new FormData();
  fd.append('preview_id', previewId);
  if (printerId) fd.append('printer_id', printerId);

  const res = await fetch('/api/print', { method: 'POST', body: fd });
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
}
