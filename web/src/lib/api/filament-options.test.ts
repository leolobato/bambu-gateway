import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchFilamentOptions, fetchFilamentLayout, fetchFilamentBaseline } from './filament-options';

describe('filament-options api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('adapts the filament catalogue', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        version: 'v',
        options: {
          nozzle_temperature: {
            key: 'nozzle_temperature', label: 'Nozzle temp', category: 'Filament',
            tooltip: '', type: 'coInts', sidetext: '°C', default: '220',
            min: 0, max: 300, enum_values: null, enum_labels: null,
            mode: 'simple', gui_type: '', nullable: false, readonly: false,
          },
        },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    const cat = await fetchFilamentOptions();
    expect(cat.options.nozzle_temperature.label).toBe('Nozzle temp');
  });

  it('fetches a resolved baseline', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ nozzle_temperature: '220' }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }),
    );
    const base = await fetchFilamentBaseline('GFL99');
    expect(base.nozzle_temperature).toBe('220');
  });
});
