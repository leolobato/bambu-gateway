import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

const STORAGE_KEY = 'bg.active-printer-id';

type Ctx = {
  /** Active printer id, or null until the user has chosen one. */
  activePrinterId: string | null;
  setActivePrinterId: (id: string | null) => void;
};

const PrinterContext = createContext<Ctx | undefined>(undefined);

export function PrinterProvider({ children }: { children: React.ReactNode }) {
  // Lazy initializer reads localStorage exactly once on mount.
  const [activePrinterId, setActivePrinterIdState] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  });

  const setActivePrinterId = useCallback((id: string | null) => {
    setActivePrinterIdState(id);
    try {
      if (id == null) window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // Ignore quota / disabled-storage errors — selection just won't persist.
    }
  }, []);

  // Sync across browser tabs so opening Dashboard in two tabs stays consistent.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === STORAGE_KEY) setActivePrinterIdState(e.newValue);
    }
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const value = useMemo<Ctx>(
    () => ({ activePrinterId, setActivePrinterId }),
    [activePrinterId, setActivePrinterId],
  );

  return <PrinterContext.Provider value={value}>{children}</PrinterContext.Provider>;
}

export function usePrinterContext(): Ctx {
  const ctx = useContext(PrinterContext);
  if (ctx === undefined) {
    throw new Error('usePrinterContext must be used within <PrinterProvider/>');
  }
  return ctx;
}
