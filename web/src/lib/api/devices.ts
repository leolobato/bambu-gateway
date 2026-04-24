import { fetchJson } from './client';
import type { DeviceListResponse } from './types';

export async function listDevices(): Promise<DeviceListResponse> {
  return fetchJson<DeviceListResponse>('/api/devices');
}

export async function deleteDevice(deviceId: string): Promise<void> {
  await fetchJson<unknown>(`/api/devices/${encodeURIComponent(deviceId)}`, {
    method: 'DELETE',
  });
}

export async function sendTestPush(deviceId: string): Promise<void> {
  await fetchJson<unknown>(`/api/devices/${encodeURIComponent(deviceId)}/test`, {
    method: 'POST',
  });
}
