import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { AmsAutoRefillCard } from './ams-auto-refill-card';

vi.mock('@/lib/api/printer-commands', () => ({
  setAmsAutoRefill: vi.fn().mockResolvedValue(undefined),
}));

import { setAmsAutoRefill } from '@/lib/api/printer-commands';

function renderCard(props: {
  enabled: boolean | null;
  supported: boolean | null;
  online?: boolean;
}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <AmsAutoRefillCard
        printerId="P01"
        enabled={props.enabled}
        supported={props.supported}
        online={props.online ?? true}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('AmsAutoRefillCard', () => {
  test('renders enabled state', () => {
    renderCard({ enabled: true, supported: true });
    expect(screen.getByText('AMS auto refill')).toBeInTheDocument();
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  });

  test('toggles by calling the command API', async () => {
    renderCard({ enabled: false, supported: true });
    await userEvent.click(screen.getByRole('switch'));
    expect(setAmsAutoRefill).toHaveBeenCalledWith('P01', true);
  });

  test('disables switch when support is reported false', () => {
    renderCard({ enabled: false, supported: false });
    expect(screen.getByRole('switch')).toBeDisabled();
    expect(screen.getByText('Not supported by this printer')).toBeInTheDocument();
  });

  test('disables switch while offline', () => {
    renderCard({ enabled: true, supported: true, online: false });
    expect(screen.getByRole('switch')).toBeDisabled();
    expect(screen.getByText('Printer offline')).toBeInTheDocument();
  });
});
