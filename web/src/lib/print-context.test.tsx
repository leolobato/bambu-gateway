import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { PrintProvider, usePrintContext } from './print-context';

function wrapper({ children }: { children: React.ReactNode }) {
  return <PrintProvider>{children}</PrintProvider>;
}

describe('filamentOverrides', () => {
  it('sets, reverts, and resets per-slot overrides', () => {
    const { result } = renderHook(() => usePrintContext(), { wrapper });

    act(() => result.current.setFilamentOverride(0, 'nozzle_temperature', '230'));
    act(() => result.current.setFilamentOverride(1, 'filament_flow_ratio', '0.95'));
    expect(result.current.filamentOverrides).toEqual({
      0: { nozzle_temperature: '230' },
      1: { filament_flow_ratio: '0.95' },
    });

    act(() => result.current.revertFilamentOverride(0, 'nozzle_temperature'));
    expect(result.current.filamentOverrides[0]).toBeUndefined();

    act(() => result.current.resetAllFilamentOverrides());
    expect(result.current.filamentOverrides).toEqual({});
  });
});
