import { afterEach, describe, expect, test, vi } from 'vitest';
import { setAmsAutoRefill } from './printer-commands';

function mockFetch(response: Response) {
  const fn = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('printer commands api', () => {
  test('setAmsAutoRefill posts enabled flag', async () => {
    const fetchMock = mockFetch(Response.json({
      status: 'ok',
      printer_id: 'P01',
      command: 'ams_auto_refill:on',
    }));

    await setAmsAutoRefill('P01', true);

    expect(fetchMock).toHaveBeenCalledWith('/api/printers/P01/ams/auto-refill', {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: true }),
    });
  });
});
