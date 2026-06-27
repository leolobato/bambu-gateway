import { describe, it, expect, vi } from 'vitest';
import { toast } from 'sonner';
import { notifyDroppedFilamentOverrides } from './drop-notice';

vi.mock('sonner', () => ({ toast: { warning: vi.fn() } }));

describe('notifyDroppedFilamentOverrides', () => {
  it('warns when a requested key was not applied', () => {
    notifyDroppedFilamentOverrides(
      { 0: { nozzle_temperature: '230', made_up_key: 'x' } },
      [{ slot: 0, key: 'nozzle_temperature', value: '230', previous: '220' }],
    );
    expect(toast.warning).toHaveBeenCalled();
  });

  it('stays silent when everything applied', () => {
    vi.mocked(toast.warning).mockClear();
    notifyDroppedFilamentOverrides(
      { 0: { nozzle_temperature: '230' } },
      [{ slot: 0, key: 'nozzle_temperature', value: '230', previous: '220' }],
    );
    expect(toast.warning).not.toHaveBeenCalled();
  });
});
