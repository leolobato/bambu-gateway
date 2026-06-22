import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { CloudSection } from './cloud-section';

vi.mock('@/lib/api/capabilities', () => ({ getCapabilities: vi.fn() }));
vi.mock('@/lib/api/cloud', () => ({
  getCloudStatus: vi.fn(),
  pasteCloudLogin: vi.fn(),
  cloudLogout: vi.fn(),
}));

import { getCapabilities } from '@/lib/api/capabilities';
import {
  cloudLogout,
  getCloudStatus,
  pasteCloudLogin,
} from '@/lib/api/cloud';

function renderSection() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <CloudSection />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('CloudSection', () => {
  test('shows disabled note when cloud capability is false', async () => {
    vi.mocked(getCapabilities).mockResolvedValue({
      push: false, live_activities: false, cloud: false, version: '1',
    });
    renderSection();
    expect(await screen.findByText(/Cloud mode is off/i)).toBeInTheDocument();
    expect(getCloudStatus).not.toHaveBeenCalled();
  });

  test('shows the signed-in account and signs out', async () => {
    vi.mocked(getCapabilities).mockResolvedValue({
      push: false, live_activities: false, cloud: true, version: '1',
    });
    vi.mocked(getCloudStatus).mockResolvedValue({
      signed_in: true,
      region: 'US',
      profile: { name: 'Alice', account: 'a@b', avatar: '', uid: '42' },
    });
    vi.mocked(cloudLogout).mockResolvedValue({ ok: true });
    renderSection();

    expect(await screen.findByText('Alice')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }));
    await waitFor(() => expect(cloudLogout).toHaveBeenCalled());
  });

  test('completes login via paste', async () => {
    vi.mocked(getCapabilities).mockResolvedValue({
      push: false, live_activities: false, cloud: true, version: '1',
    });
    vi.mocked(getCloudStatus).mockResolvedValue({
      signed_in: false, region: 'US', profile: null,
    });
    vi.mocked(pasteCloudLogin).mockResolvedValue({
      profile: { name: 'Alice', account: 'a@b', avatar: '', uid: '42' },
      connected: true,
    });
    renderSection();

    const input = await screen.findByPlaceholderText(/localhost:13618/i);
    await userEvent.type(input, 'http://localhost:13618/?ticket=tk_abc');
    await userEvent.click(screen.getByRole('button', { name: /complete sign-in/i }));
    await waitFor(() =>
      expect(pasteCloudLogin).toHaveBeenCalledWith('http://localhost:13618/?ticket=tk_abc'),
    );
  });
});
