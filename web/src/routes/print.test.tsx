import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';
import PrintRoute from './print';
import { PrinterProvider } from '@/lib/printer-context';
import { PrintProvider } from '@/lib/print-context';

vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
    warning: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock('@/components/print/stl-preview-card', () => ({
  StlPreviewCard: ({
    filename,
    onAccept,
    onPreviewPng,
  }: {
    filename: string;
    onAccept: () => void;
    onPreviewPng?: (dataUrl: string) => void;
  }) => (
    <div>
      <div>STL preview: {filename}</div>
      <button
        type="button"
        onClick={() => {
          onPreviewPng?.('data:image/png;base64,UE5H');
          onAccept();
        }}
      >
        Accept STL
      </button>
    </div>
  ),
}));

vi.mock('@/lib/api/slicer-profiles', () => ({
  getSlicerMachines: vi.fn(),
  getSlicerProcesses: vi.fn(),
  getSlicerPlateTypes: vi.fn(),
  resolveForMachine: vi.fn(),
}));

vi.mock('@/lib/api/printers', () => ({
  listPrinters: vi.fn(),
}));

vi.mock('@/lib/api/ams', () => ({
  getAms: vi.fn(),
}));

vi.mock('@/lib/api/stl-drafts', () => ({
  createStlDraft: vi.fn(),
  layoutStlDraft: vi.fn(),
  materializeStlDraft: vi.fn(),
}));

import { toast } from 'sonner';
import { getAms } from '@/lib/api/ams';
import { listPrinters } from '@/lib/api/printers';
import {
  getSlicerMachines,
  getSlicerPlateTypes,
  getSlicerProcesses,
} from '@/lib/api/slicer-profiles';
import { createStlDraft } from '@/lib/api/stl-drafts';

function installLocalStorageStub() {
  const values = new Map<string, string>();
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => {
        values.set(key, value);
      }),
      removeItem: vi.fn((key: string) => {
        values.delete(key);
      }),
      clear: vi.fn(() => {
        values.clear();
      }),
    },
  });
}

function renderPrintRoute() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <PrinterProvider>
        <PrintProvider>
          <MemoryRouter>
            <PrintRoute />
          </MemoryRouter>
        </PrintProvider>
      </PrinterProvider>
    </QueryClientProvider>,
  );
}

describe('PrintRoute STL import defaults', () => {
  beforeAll(() => {
    installLocalStorageStub();
  });

  beforeEach(() => {
    window.localStorage.setItem('bg.active-printer-id', 'printer-1');
    vi.mocked(listPrinters).mockResolvedValue({
      printers: [
        {
          id: 'printer-1',
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
      printer_id: 'printer-1',
      trays: [],
      units: [],
      vt_tray: null,
      auto_refill_enabled: null,
      auto_refill_supported: null,
    });
    vi.mocked(getSlicerMachines).mockResolvedValue([
      {
        setting_id: 'GM020',
        name: 'Bambu Lab A1 mini 0.4 nozzle',
        vendor: 'Bambu Lab',
        nozzle_diameter: '0.4',
        printer_model: 'A1 mini',
      },
    ]);
    vi.mocked(getSlicerProcesses).mockResolvedValue([
      {
        setting_id: 'GP049',
        name: '0.08mm Extra Fine @BBL A1M',
        vendor: 'Bambu Lab',
        compatible_printers: ['GM020'],
        layer_height: '0.08',
      },
      {
        setting_id: 'GP000',
        name: '0.20mm Standard @BBL A1M',
        vendor: 'Bambu Lab',
        compatible_printers: ['GM020'],
        layer_height: '0.20',
      },
    ]);
    vi.mocked(getSlicerPlateTypes).mockResolvedValue([
      { value: 'textured_pei_plate', label: 'Textured PEI Plate' },
    ]);
    vi.mocked(createStlDraft).mockResolvedValue({
      draft_token: 'draft1234',
      source_filename: 'part.stl',
      source_url: '/api/stl-drafts/draft1234/source.stl',
      bed: { width: 180, depth: 180, printable_area: [] },
      objects: [],
      warnings: [],
      actions: [],
    });
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  test('imports an STL using the active printer machine and normal 0.20mm process', async () => {
    const { container } = renderPrintRoute();

    await waitFor(() => {
      expect(getSlicerProcesses).toHaveBeenCalledWith('GM020');
    });

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['solid part\nendsolid part\n'], 'part.stl', { type: 'model/stl' });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(createStlDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          file,
          machineProfile: 'GM020',
          processProfile: 'GP000',
        }),
      );
    });
    expect(toast.error).not.toHaveBeenCalledWith(
      'Pick a machine and process before importing STL.',
    );
    expect(await screen.findByText('STL preview: part.stl')).toBeInTheDocument();
  });
});
