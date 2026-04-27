import { fetchJson } from './client';
import type { SpeedLevel } from './types';

export async function pausePrint(printerId: string): Promise<void> {
  await fetchJson<unknown>(`/api/printers/${encodeURIComponent(printerId)}/pause`, {
    method: 'POST',
  });
}

export async function resumePrint(printerId: string): Promise<void> {
  await fetchJson<unknown>(`/api/printers/${encodeURIComponent(printerId)}/resume`, {
    method: 'POST',
  });
}

export async function cancelPrint(printerId: string): Promise<void> {
  await fetchJson<unknown>(`/api/printers/${encodeURIComponent(printerId)}/cancel`, {
    method: 'POST',
  });
}

export async function setPrinterSpeed(
  printerId: string,
  level: SpeedLevel,
): Promise<void> {
  await fetchJson<unknown>(`/api/printers/${encodeURIComponent(printerId)}/speed`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ level }),
  });
}

export interface StartDryingParams {
  /** Drying temperature in °C. Backend defaults to 55. */
  temperature: number;
  /** Drying duration in minutes. Backend defaults to 480. */
  durationMinutes: number;
}

export async function startDrying(
  printerId: string,
  amsId: number,
  params: StartDryingParams,
): Promise<void> {
  await fetchJson<unknown>(
    `/api/printers/${encodeURIComponent(printerId)}/ams/${amsId}/start-drying`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        temperature: params.temperature,
        duration_minutes: params.durationMinutes,
      }),
    },
  );
}

export async function stopDrying(printerId: string, amsId: number): Promise<void> {
  await fetchJson<unknown>(
    `/api/printers/${encodeURIComponent(printerId)}/ams/${amsId}/stop-drying`,
    { method: 'POST' },
  );
}

export interface SetAmsFilamentParams {
  /** Slicer profile setting_id (e.g. "GFSA00"). The gateway resolves the rest. */
  settingId: string;
  /** 8-char "RRGGBBAA" hex (no leading "#"). Optional — gateway falls back to the profile default. */
  trayColor?: string;
}

/**
 * Assign a filament profile to one AMS tray. `trayId` is the per-AMS slot
 * index 0..3 (NOT the global slot). For the external spool, pass amsId=255
 * and trayId=254 — Bambu's reserved values.
 */
export async function setAmsFilament(
  printerId: string,
  amsId: number,
  trayId: number,
  params: SetAmsFilamentParams,
): Promise<void> {
  await fetchJson<unknown>(
    `/api/printers/${encodeURIComponent(printerId)}/ams/${amsId}/tray/${trayId}/filament`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        setting_id: params.settingId,
        tray_color: params.trayColor ?? null,
      }),
    },
  );
}
