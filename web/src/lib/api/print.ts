import { ApiError } from './client';
import type { PrintEstimate, SettingsTransferInfo } from './types';

export interface PrintDirectResponse {
  status: string;
  file_name: string;
  printer_id: string;
  was_sliced: boolean;
  settings_transfer?: SettingsTransferInfo | null;
  upload_id: string | null;
  estimate?: PrintEstimate | null;
}

async function postPrint(fd: FormData): Promise<PrintDirectResponse> {
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
  return (await res.json()) as PrintDirectResponse;
}

/**
 * POST /api/print using a stored preview id (skip re-slicing).
 */
export async function printFromPreview(
  previewId: string,
  printerId?: string,
): Promise<PrintDirectResponse> {
  const fd = new FormData();
  fd.append('preview_id', previewId);
  if (printerId) fd.append('printer_id', printerId);
  return postPrint(fd);
}

/**
 * POST /api/print using a slice job id (skip re-slicing).
 */
export async function printFromJob(
  jobId: string,
  printerId?: string,
): Promise<PrintDirectResponse> {
  const fd = new FormData();
  fd.append('job_id', jobId);
  if (printerId) fd.append('printer_id', printerId);
  return postPrint(fd);
}

/**
 * POST /api/print with a 3MF that already contains G-code (no slicing).
 * Returns the upload_id so the caller can poll /api/uploads/{id}.
 */
export async function printGcodeFile(
  file: File,
  printerId?: string,
  filamentProfiles?: Record<string, { profile_setting_id: string; tray_slot: number }>,
  plateId?: number,
): Promise<PrintDirectResponse> {
  const fd = new FormData();
  fd.append('file', file);
  if (printerId) fd.append('printer_id', printerId);
  if (plateId) fd.append('plate_id', String(plateId));
  if (filamentProfiles && Object.keys(filamentProfiles).length > 0) {
    fd.append('filament_profiles', JSON.stringify(filamentProfiles));
  }
  return postPrint(fd);
}
