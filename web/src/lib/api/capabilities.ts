import { fetchJson } from './client';
import type { Capabilities } from './types';

export async function getCapabilities(): Promise<Capabilities> {
  return fetchJson<Capabilities>('/api/capabilities');
}
