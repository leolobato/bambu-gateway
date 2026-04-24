import { fetchJson } from './client';
import type { AMSResponse } from './types';

// NOTE: backend currently has no ?printer_id= param — this returns the
// *default* printer's AMS only. The Dashboard hides AMS when the active
// printer differs from `response.printer_id`. A backend follow-up will
// add the param.
export async function getAms(): Promise<AMSResponse> {
  return fetchJson<AMSResponse>('/api/ams');
}
