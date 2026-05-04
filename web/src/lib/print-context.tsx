import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type ReactNode,
  type SetStateAction,
} from 'react';
import type { SlicingSettings } from '@/components/print/slicing-settings-group';
import type { FilamentMapping } from '@/components/print/filaments-group';
import type {
  PrintEstimate,
  SettingsTransferInfo,
  ThreeMFInfo,
} from '@/lib/api/types';

/**
 * Discriminated state machine for the Print tab. Lives in `<PrintProvider/>`
 * so it survives navigation away from /print (e.g. switching to /jobs and
 * back) and so any in-flight slice/upload polling continues to drive the
 * same state regardless of whether the route is currently mounted.
 */
export type PrintState =
  | { kind: 'empty' }
  | {
      // Between file pick and 'imported': we're waiting on /api/parse-3mf
      // (upload + slicer inspect) and the AMS tray matcher. Renders an
      // indeterminate spinner so the drop zone doesn't sit silent.
      kind: 'importing';
      file: File;
      // Bumped on every fresh pick. Stale resolutions check this against
      // the current state to decide whether to commit their result.
      importId: string;
    }
  | {
      kind: 'imported';
      file: File;
      info: ThreeMFInfo;
      banner?: BannerData;
    }
  | {
      kind: 'slicing';
      file: File;
      info: ThreeMFInfo;
      jobId: string;
      percent: number | null;
      statusLine: string;
      isPreview: boolean;
    }
  | {
      kind: 'previewReady';
      file: File;
      info: ThreeMFInfo;
      jobId: string;
      transfer: SettingsTransferInfo | null;
      estimate: PrintEstimate | null;
    }
  | {
      kind: 'uploading';
      file: File;
      info: ThreeMFInfo;
      uploadId: string;
      percent: number;
    }
  | { kind: 'sent'; printerName: string | null; estimate: PrintEstimate | null };

export interface BannerData {
  variant: 'info' | 'warn' | 'success' | 'error';
  title: string;
  message?: string;
  details?: string;
}

type Ctx = {
  state: PrintState;
  setState: Dispatch<SetStateAction<PrintState>>;
  settings: SlicingSettings;
  setSettings: Dispatch<SetStateAction<SlicingSettings>>;
  selectedPlateId: number;
  setSelectedPlateId: Dispatch<SetStateAction<number>>;
  filamentMapping: FilamentMapping;
  setFilamentMapping: Dispatch<SetStateAction<FilamentMapping>>;
  /**
   * Lives across navigations so a slice job started on /print continues
   * polling even if the user moves to /jobs mid-flight; aborting it
   * cancels the polling loop wherever it's running.
   */
  sliceAbortRef: MutableRefObject<AbortController | null>;
};

const PrintContext = createContext<Ctx | undefined>(undefined);

export function PrintProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<PrintState>({ kind: 'empty' });
  const [settings, setSettings] = useState<SlicingSettings>({
    machine: '',
    process: '',
    plateType: '',
  });
  const [selectedPlateId, setSelectedPlateId] = useState<number>(1);
  const [filamentMapping, setFilamentMapping] = useState<FilamentMapping>({});
  const sliceAbortRef = useRef<AbortController | null>(null);

  const value = useMemo<Ctx>(
    () => ({
      state,
      setState,
      settings,
      setSettings,
      selectedPlateId,
      setSelectedPlateId,
      filamentMapping,
      setFilamentMapping,
      sliceAbortRef,
    }),
    [state, settings, selectedPlateId, filamentMapping],
  );

  return <PrintContext.Provider value={value}>{children}</PrintContext.Provider>;
}

export function usePrintContext(): Ctx {
  const ctx = useContext(PrintContext);
  if (ctx === undefined) {
    throw new Error('usePrintContext must be used within <PrintProvider/>');
  }
  return ctx;
}
