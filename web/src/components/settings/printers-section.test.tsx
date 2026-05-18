import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { PrintersSection } from './printers-section';

vi.mock('@/lib/api/printer-configs', () => ({
  listPrinterConfigs: vi.fn(),
  deletePrinterConfig: vi.fn(),
}));

vi.mock('@/lib/api/printers', () => ({
  listPrinters: vi.fn(),
}));

vi.mock('@/lib/api/ams', () => ({
  getAms: vi.fn(),
}));

vi.mock('@/lib/api/printer-commands', () => ({
  setAmsAutoRefill: vi.fn().mockResolvedValue(undefined),
}));

import { getAms } from '@/lib/api/ams';
import { listPrinterConfigs } from '@/lib/api/printer-configs';
import { listPrinters } from '@/lib/api/printers';

function renderSection() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <PrintersSection />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('PrintersSection', () => {
  test('renders AMS auto refill in each configured printer row', async () => {
    vi.mocked(listPrinterConfigs).mockResolvedValue({
      printers: [
        {
          serial: 'P01',
          ip: '10.0.1.10',
          name: 'A1 Mini',
          machine_model: 'GM020',
        },
      ],
    });
    vi.mocked(listPrinters).mockResolvedValue({
      printers: [
        {
          id: 'P01',
          name: 'A1 Mini',
          machine_model: 'GM020',
          online: true,
          state: 'idle',
          stg_cur: 0,
          stage_name: null,
          stage_category: null,
          speed_level: 2,
          active_tray: null,
          temperatures: { nozzle_temp: 0, nozzle_target: 0, bed_temp: 0, bed_target: 0 },
          job: null,
          hms_codes: [],
          print_error: 0,
          error_message: null,
          camera: null,
        },
      ],
    });
    vi.mocked(getAms).mockResolvedValue({
      printer_id: 'P01',
      trays: [],
      units: [],
      vt_tray: null,
      auto_refill_enabled: true,
      auto_refill_supported: true,
    });

    renderSection();

    expect(await screen.findByText('A1 Mini')).toBeInTheDocument();
    expect(await screen.findByText('AMS auto refill')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'AMS auto refill' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(getAms).toHaveBeenCalledWith('P01');
  });
});
