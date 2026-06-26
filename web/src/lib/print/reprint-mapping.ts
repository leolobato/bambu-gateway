import type { FilamentMapping } from '@/components/print/filaments-group';
import type { FilamentProfileEntry, ThreeMFInfo } from '@/lib/api/types';

/**
 * Rebuild the Print UI's filament→tray mapping from a stored job's
 * `filament_profiles`.
 *
 * `filament_profiles` is keyed by dense *position* in `info.filaments`, while
 * `FilamentMapping` is keyed by the 3MF's authored slot `index` (the two
 * diverge for sparse 3MFs). Bare-string entries are resolver fallbacks with no
 * tray, so they contribute no mapping.
 */
export function buildReprintMapping(
  filamentProfiles: Record<string, FilamentProfileEntry> | FilamentProfileEntry[],
  info: ThreeMFInfo,
): FilamentMapping {
  const mapping: FilamentMapping = {};
  const entries: [string, FilamentProfileEntry][] = Array.isArray(filamentProfiles)
    ? filamentProfiles.map((v, i) => [String(i), v])
    : Object.entries(filamentProfiles);
  for (const [posKey, entry] of entries) {
    if (typeof entry === 'string') continue;
    const position = Number.parseInt(posKey, 10);
    const filament = info.filaments[position];
    if (!filament) continue;
    mapping[filament.index] = entry.tray_slot;
  }
  return mapping;
}
