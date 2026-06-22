import { fetchJson } from './client';
import type { CloudProfile, CloudStatus } from './types';

export async function getCloudAuthUrl(): Promise<{ url: string }> {
  return fetchJson<{ url: string }>('/api/cloud/auth/url');
}

export async function getCloudStatus(): Promise<CloudStatus> {
  return fetchJson<CloudStatus>('/api/cloud/auth/status');
}

export async function pasteCloudLogin(
  pastedUrl: string,
): Promise<{ profile: CloudProfile; connected: boolean }> {
  return fetchJson('/api/cloud/auth/paste', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pasted_url: pastedUrl }),
  });
}

export async function cloudLogout(): Promise<{ ok: boolean }> {
  return fetchJson('/api/cloud/auth/logout', { method: 'POST' });
}
