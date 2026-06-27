import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { FilamentParametersCard } from './filament-parameters-card';
import { PrintProvider } from '@/lib/print-context';

function withProviders(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  // Seed the cache so the queries don't sit in `isLoading` and trigger
  // network calls that would never resolve in JSDOM.
  qc.setQueryData(['filament-options', 'catalogue'], { version: 'v1', options: {} });
  qc.setQueryData(['filament-options', 'layout'], {
    version: 'v1', allowlistRevision: 'r1', pages: [],
  });
  qc.setQueryData(['filament-options', 'baseline', 'GFL99'], {});
  qc.setQueryData(['filament-options', 'baseline', 'GFG96'], {});
  return (
    <QueryClientProvider client={qc}>
      <PrintProvider>{ui}</PrintProvider>
    </QueryClientProvider>
  );
}

describe('FilamentParametersCard', () => {
  it('renders the title and a slot selector for each used filament slot', () => {
    render(
      withProviders(
        <FilamentParametersCard
          slots={[
            { slot: 0, label: 'PLA Basic', settingId: 'GFL99' },
            { slot: 1, label: 'PETG HF', settingId: 'GFG96' },
          ]}
        />,
      ),
    );
    expect(screen.getByText(/Filament settings/i)).toBeInTheDocument();
    // >1 slot renders a Select trigger exposing the active slot's label.
    expect(screen.getByText(/PLA Basic/)).toBeInTheDocument();
  });

  it('renders a static label (no dropdown) for a single slot', () => {
    render(
      withProviders(
        <FilamentParametersCard
          slots={[{ slot: 0, label: 'PLA Basic', settingId: 'GFL99' }]}
        />,
      ),
    );
    expect(screen.getByText(/Filament settings/i)).toBeInTheDocument();
    expect(screen.getByText(/PLA Basic/)).toBeInTheDocument();
    // The single-slot case is a static label, not a combobox.
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('shows the empty state when the active slot has no overrides', () => {
    render(
      withProviders(
        <FilamentParametersCard
          slots={[{ slot: 0, label: 'PLA Basic', settingId: 'GFL99' }]}
        />,
      ),
    );
    expect(
      screen.getByText('No customizations from default profile'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Show all settings/ }),
    ).toBeInTheDocument();
  });
});
