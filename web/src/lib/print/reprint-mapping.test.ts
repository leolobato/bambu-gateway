import { describe, expect, test } from 'vitest';
import { buildReprintMapping } from './reprint-mapping';
import type { ThreeMFInfo } from '@/lib/api/types';

function infoWith(indices: number[]): ThreeMFInfo {
  return {
    filaments: indices.map((index) => ({ index, used: true })),
  } as unknown as ThreeMFInfo;
}

describe('buildReprintMapping', () => {
  test('maps dense positions to the 3MF authored slot indices', () => {
    const info = infoWith([1, 3]); // sparse authored slots
    const mapping = buildReprintMapping(
      {
        '0': { profile_setting_id: 'GFA00', tray_slot: 0 },
        '1': { profile_setting_id: 'GFB01', tray_slot: 2 },
      },
      info,
    );
    expect(mapping).toEqual({ 1: 0, 3: 2 });
  });

  test('skips bare-string resolver-fallback entries (no tray)', () => {
    const info = infoWith([0, 1]);
    const mapping = buildReprintMapping(
      { '0': { profile_setting_id: 'GFA00', tray_slot: 1 }, '1': 'GFB01' },
      info,
    );
    expect(mapping).toEqual({ 0: 1 });
  });

  test('accepts the positional array form', () => {
    const info = infoWith([2]);
    const mapping = buildReprintMapping([{ profile_setting_id: 'GFA00', tray_slot: 3 }], info);
    expect(mapping).toEqual({ 2: 3 });
  });

  test('ignores positions with no matching filament', () => {
    const info = infoWith([0]);
    const mapping = buildReprintMapping(
      { '0': { profile_setting_id: 'GFA00', tray_slot: 1 }, '5': { profile_setting_id: 'GFB01', tray_slot: 2 } },
      info,
    );
    expect(mapping).toEqual({ 0: 1 });
  });
});
