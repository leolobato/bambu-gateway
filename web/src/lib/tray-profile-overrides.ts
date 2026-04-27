import { useEffect, useState } from 'react';

// Per-printer × per-AMS-slot sticky filament-profile preference. Mirrors the
// iOS `trayProfileBySlot` mapping (BambuGateway/App/AppViewModel.swift:341 +
// :1532) — when a project filament is mapped to a tray during printing, this
// override wins over `tray.matched_filament.setting_id` from the AMS auto-
// match. Stored per-printer in localStorage, never pushed to the printer.

const KEY_PREFIX = 'bg.tray-profile.';
const SAME_TAB_EVENT = 'bg:tray-profile-override';

function makeKey(printerId: string, slot: number): string {
  return `${KEY_PREFIX}${printerId}.${slot}`;
}

export function getTrayProfileOverride(
  printerId: string | null,
  slot: number,
): string | null {
  if (!printerId) return null;
  try {
    const v = window.localStorage.getItem(makeKey(printerId, slot));
    return v && v.trim() ? v : null;
  } catch {
    return null;
  }
}

export function setTrayProfileOverride(
  printerId: string,
  slot: number,
  settingId: string | null,
): void {
  const key = makeKey(printerId, slot);
  try {
    if (settingId == null || !settingId.trim()) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, settingId);
    }
    // The native `storage` event only fires on OTHER tabs, so notify same-
    // tab subscribers (the picker's host sheet, the print page) explicitly.
    window.dispatchEvent(
      new CustomEvent<TrayOverrideEventDetail>(SAME_TAB_EVENT, {
        detail: { printerId, slot },
      }),
    );
  } catch {
    // Quota exceeded / private mode — selection just won't persist.
  }
}

interface TrayOverrideEventDetail {
  printerId: string;
  slot: number;
}

/**
 * Subscribe a component to the override for one (printer, slot) pair.
 * Tracks both cross-tab `storage` events and the same-tab custom event.
 */
export function useTrayProfileOverride(
  printerId: string | null,
  slot: number,
): [string | null, (settingId: string | null) => void] {
  const [value, setValue] = useState<string | null>(() =>
    getTrayProfileOverride(printerId, slot),
  );

  useEffect(() => {
    setValue(getTrayProfileOverride(printerId, slot));
  }, [printerId, slot]);

  useEffect(() => {
    if (!printerId) return;
    const targetKey = makeKey(printerId, slot);

    function refresh() {
      setValue(getTrayProfileOverride(printerId, slot));
    }
    function onStorage(e: StorageEvent) {
      // e.key is null when the storage was cleared wholesale.
      if (e.key === targetKey || e.key === null) refresh();
    }
    function onCustom(e: Event) {
      const detail = (e as CustomEvent<TrayOverrideEventDetail>).detail;
      if (!detail) return;
      if (detail.printerId === printerId && detail.slot === slot) refresh();
    }

    window.addEventListener('storage', onStorage);
    window.addEventListener(SAME_TAB_EVENT, onCustom as EventListener);
    return () => {
      window.removeEventListener('storage', onStorage);
      window.removeEventListener(SAME_TAB_EVENT, onCustom as EventListener);
    };
  }, [printerId, slot]);

  function set(settingId: string | null) {
    if (!printerId) return;
    setTrayProfileOverride(printerId, slot, settingId);
    setValue(settingId);
  }

  return [value, set];
}
