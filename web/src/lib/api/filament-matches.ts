import { fetchJson } from './client';
import type { FilamentInfo, FilamentMatchResponse } from './types';

/**
 * POST /api/filament-matches — ask the backend to map each project filament
 * (from the parsed 3MF) to a preferred AMS tray slot for the given printer.
 */
export async function getFilamentMatches(
  printerId: string,
  filaments: FilamentInfo[],
): Promise<FilamentMatchResponse> {
  return fetchJson<FilamentMatchResponse>('/api/filament-matches', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ printer_id: printerId, filaments }),
  });
}
