import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ProcessParametersCard } from './process-parameters-card';
import { PrintProvider } from '@/lib/print-context';
import type { ProcessModifications } from '@/lib/process/types';

function withProviders(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  // Seed the cache so the queries don't sit in `isLoading` and trigger
  // network calls that would never resolve in JSDOM.
  qc.setQueryData(['process-options', 'catalogue'], { version: 'v1', options: {} });
  qc.setQueryData(['process-options', 'layout'], {
    version: 'v1', allowlistRevision: 'r1', pages: [],
  });
  return (
    <QueryClientProvider client={qc}>
      <PrintProvider>{ui}</PrintProvider>
    </QueryClientProvider>
  );
}

describe('ProcessParametersCard', () => {
  it('renders the empty state when no modifications', () => {
    const mods: ProcessModifications = {
      processSettingId: 'P1P 0.20', modifiedKeys: [], values: {},
    };
    render(withProviders(<ProcessParametersCard modifications={mods} />));
    expect(
      screen.getByText('No customizations from default profile'),
    ).toBeInTheDocument();
    // Bottom button is the entry point into the All sheet; the header
    // shows the count but isn't tappable.
    expect(
      screen.getByRole('button', { name: /Show all settings/ }),
    ).toBeInTheDocument();
  });

  it('shows the modified badge when keys are present', () => {
    const mods: ProcessModifications = {
      processSettingId: 'P1P 0.20',
      modifiedKeys: ['layer_height', 'sparse_infill_density'],
      values: { layer_height: '0.16', sparse_infill_density: '20%' },
    };
    render(withProviders(<ProcessParametersCard modifications={mods} />));
    expect(screen.getByText('2 modified')).toBeInTheDocument();
  });
});
