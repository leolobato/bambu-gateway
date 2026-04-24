import { fetchJson } from './client';
import type { AMSResponse } from './types';

/**
 * Fetch AMS state. When `printerId` is omitted the backend falls back to the
 * default-configured printer (preserves Phase 2 behavior); when supplied it
 * targets exactly that printer or 404s on an unknown id.
 */
export async function getAms(printerId?: string): Promise<AMSResponse> {
  const path = printerId
    ? `/api/ams?printer_id=${encodeURIComponent(printerId)}`
    : '/api/ams';
  return fetchJson<AMSResponse>(path);
}
