import { fetchJson } from './client';
import type { PrinterListResponse } from './types';

export async function listPrinters(): Promise<PrinterListResponse> {
  return fetchJson<PrinterListResponse>('/api/printers');
}
