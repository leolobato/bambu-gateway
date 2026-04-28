import { fetchJson } from './client';

export type CameraState = 'unsupported' | 'idle' | 'connecting' | 'streaming' | 'failed';

export interface CameraStatus {
  state: CameraState;
  error: string | null;
  last_frame_at: number | null;
}

export async function getCameraStatus(printerId: string): Promise<CameraStatus> {
  return fetchJson<CameraStatus>(
    `/api/printers/${encodeURIComponent(printerId)}/camera/status`,
  );
}

/** Build a cache-busted MJPEG URL. Bump `token` to force the browser to reconnect. */
export function cameraStreamUrl(printerId: string, token: number): string {
  return `/api/printers/${encodeURIComponent(printerId)}/camera/stream.mjpg?t=${token}`;
}
