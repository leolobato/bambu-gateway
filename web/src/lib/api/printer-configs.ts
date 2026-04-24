import { fetchJson } from './client';
import type {
  PrinterConfigInput,
  PrinterConfigListResponse,
  PrinterConfigResponse,
} from './types';

export async function listPrinterConfigs(): Promise<PrinterConfigListResponse> {
  return fetchJson<PrinterConfigListResponse>('/api/settings/printers');
}

export async function createPrinterConfig(
  input: PrinterConfigInput,
): Promise<PrinterConfigResponse> {
  return fetchJson<PrinterConfigResponse>('/api/settings/printers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export async function updatePrinterConfig(
  serial: string,
  input: PrinterConfigInput,
): Promise<PrinterConfigResponse> {
  return fetchJson<PrinterConfigResponse>(
    `/api/settings/printers/${encodeURIComponent(serial)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
  );
}

export async function deletePrinterConfig(serial: string): Promise<void> {
  // 204 No Content — fetchJson is JSON-only, so use fetch directly here.
  const res = await fetch(`/api/settings/printers/${encodeURIComponent(serial)}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // not JSON
    }
    throw new Error(`Delete failed: ${detail}`);
  }
}
