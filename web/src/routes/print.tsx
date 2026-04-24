import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { DropZoneCard, DropOverlay } from '@/components/print/drop-zone';
import { PlateCard } from '@/components/print/plate-card';
import { SlicingSettingsGroup, type SlicingSettings } from '@/components/print/slicing-settings-group';
import type { SettingOption } from '@/components/print/setting-row';
import { FilamentsGroup, type FilamentMapping } from '@/components/print/filaments-group';
import { InfoBanner } from '@/components/print/info-banner';
import { SlicingProgressCard } from '@/components/print/slicing-progress-card';
import { SettingsTransferNote } from '@/components/print/settings-transfer-note';
import { parse3mf } from '@/lib/api/3mf';
import {
  getSlicerMachines,
  getSlicerProcesses,
  getSlicerPlateTypes,
} from '@/lib/api/slicer-profiles';
import { getAms } from '@/lib/api/ams';
import { listPrinters } from '@/lib/api/printers';
import { getFilamentMatches } from '@/lib/api/filament-matches';
import { cancelUpload } from '@/lib/api/uploads';
import { printFromPreview } from '@/lib/api/print';
import { usePrintStream } from '@/lib/use-print-stream';
import { useDropZone } from '@/lib/use-drop-zone';
import { usePrinterContext } from '@/lib/printer-context';
import type {
  AMSTray,
  SettingsTransferInfo,
  ThreeMFInfo,
} from '@/lib/api/types';
import { cn } from '@/lib/utils';

type PrintState =
  | { kind: 'empty' }
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
      percent: number | null;
      statusLine: string;
    }
  | {
      kind: 'previewReady';
      file: File;
      info: ThreeMFInfo;
      previewId: string;
      transfer: SettingsTransferInfo | null;
    }
  | {
      kind: 'uploading';
      file: File;
      info: ThreeMFInfo;
      uploadId: string;
      percent: number;
    }
  | { kind: 'sent' };

interface BannerData {
  variant: 'info' | 'warn' | 'success' | 'error';
  title: string;
  message?: string;
  details?: string;
}

export default function PrintRoute() {
  const { activePrinterId } = usePrinterContext();
  const navigate = useNavigate();

  const [state, setState] = useState<PrintState>({ kind: 'empty' });
  const [settings, setSettings] = useState<SlicingSettings>({
    machine: '',
    process: '',
    plateType: '',
  });
  const [selectedPlateId, setSelectedPlateId] = useState<number>(1);
  const [filamentMapping, setFilamentMapping] = useState<FilamentMapping>({});

  // Slicer catalogs — load once, don't refetch automatically.
  const machinesQuery = useQuery({
    queryKey: ['slicer', 'machines'],
    queryFn: getSlicerMachines,
    staleTime: Infinity,
  });
  const processesQuery = useQuery({
    queryKey: ['slicer', 'processes', settings.machine],
    queryFn: () => getSlicerProcesses(settings.machine || undefined),
    staleTime: Infinity,
    enabled: !!settings.machine,
  });
  const plateTypesQuery = useQuery({
    queryKey: ['slicer', 'plate-types'],
    queryFn: getSlicerPlateTypes,
    staleTime: Infinity,
  });

  // Active printer's name (for the "Target printer" subtitle).
  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: listPrinters,
    refetchInterval: 4_000,
  });
  const activePrinter = printersQuery.data?.printers.find((p) => p.id === activePrinterId);
  const activePrinterName = activePrinter?.name ?? null;

  // AMS for the active printer (for tray dropdowns).
  const amsQuery = useQuery({
    queryKey: ['ams', activePrinterId],
    queryFn: () => getAms(activePrinterId ?? undefined),
    refetchInterval: 4_000,
    enabled: !!activePrinterId,
    retry: false,
  });
  const trays: AMSTray[] = useMemo(() => {
    if (!amsQuery.data) return [];
    const list = [...amsQuery.data.trays];
    if (amsQuery.data.vt_tray) list.push(amsQuery.data.vt_tray);
    return list;
  }, [amsQuery.data]);

  // SSE consumer.
  const stream = usePrintStream();

  // Drag-and-drop is active in every state EXCEPT slicing/uploading
  // (replacing the file mid-stream would be confusing).
  const ddEnabled = state.kind !== 'slicing' && state.kind !== 'uploading';
  const onDropFile = (file: File) => void importFile(file);
  const { dragging } = useDropZone({ accept: '.3mf', onFile: onDropFile, enabled: ddEnabled });

  async function importFile(file: File) {
    try {
      const info = await parse3mf(file);
      // Default plate selection.
      const firstPlate = info.plates[0]?.id ?? 1;
      setSelectedPlateId(firstPlate);
      // Pre-populate slicing settings from the 3MF if present.
      setSettings((prev) => ({
        machine: prev.machine || info.printer.printer_settings_id || '',
        process: prev.process || info.print_profile.print_settings_id || '',
        plateType: prev.plateType,
      }));
      // Filament-tray defaults via backend matcher.
      const initialMapping: FilamentMapping = {};
      if (activePrinterId) {
        try {
          const matches = await getFilamentMatches(activePrinterId, info.filaments);
          for (const m of matches.matches) {
            initialMapping[m.index] = m.preferred_tray_slot ?? -1;
          }
        } catch {
          // Non-fatal — user can pick manually.
        }
      }
      setFilamentMapping(initialMapping);
      const banner: BannerData = info.has_gcode
        ? {
            variant: 'warn',
            title: 'This 3MF already contains G-code.',
            message: 'AMS tray selections and project filament overrides are ignored.',
          }
        : { variant: 'info', title: 'File parsed — slicing required.' };
      setState({ kind: 'imported', file, info, banner });
    } catch (err) {
      toast.error(`Failed to parse 3MF: ${(err as Error).message}`);
    }
  }

  function clearImport() {
    stream.cancel();
    setState({ kind: 'empty' });
    setFilamentMapping({});
  }

  function buildFilamentProfilesPayload(
    info: ThreeMFInfo,
  ): Record<string, { profile_setting_id: string; tray_slot: number }> {
    const out: Record<string, { profile_setting_id: string; tray_slot: number }> = {};
    if (info.has_gcode) return out;
    for (const filament of info.filaments) {
      const slot = filamentMapping[filament.index];
      if (slot == null || slot < 0) continue;
      const tray = trays.find((t) => t.slot === slot);
      const settingId = tray?.matched_filament?.setting_id ?? '';
      if (!settingId) continue;
      out[String(filament.index)] = { profile_setting_id: settingId, tray_slot: slot };
    }
    return out;
  }

  function startSlicing(file: File, info: ThreeMFInfo, preview: boolean) {
    if (!settings.machine || !settings.process) {
      toast.error('Pick a machine and process before slicing.');
      return;
    }
    setState({ kind: 'slicing', file, info, percent: null, statusLine: 'Starting…' });
    void stream.start(
      {
        file,
        printerId: activePrinterId ?? undefined,
        plateId: selectedPlateId,
        machineProfile: settings.machine,
        processProfile: settings.process,
        filamentProfiles: buildFilamentProfilesPayload(info),
        plateType: settings.plateType || undefined,
        preview,
      },
      {
        onStatus: (s) =>
          setState((cur) => {
            if (cur.kind === 'slicing') {
              if (s.upload_id) {
                // Backend signalled the transition into the FTP upload phase.
                return {
                  kind: 'uploading',
                  file,
                  info,
                  uploadId: s.upload_id,
                  percent: 0,
                };
              }
              return { ...cur, statusLine: s.message };
            }
            if (cur.kind === 'uploading' && s.upload_id && !cur.uploadId) {
              return { ...cur, uploadId: s.upload_id };
            }
            return cur;
          }),
        onProgress: (p) =>
          setState((cur) =>
            cur.kind === 'slicing'
              ? {
                  ...cur,
                  percent: typeof p.percent === 'number' ? p.percent : cur.percent,
                  statusLine: typeof p.status_line === 'string' ? p.status_line : cur.statusLine,
                }
              : cur,
          ),
        onResult: (r) => {
          if (preview && r.preview_id) {
            setState({
              kind: 'previewReady',
              file,
              info,
              previewId: r.preview_id,
              transfer: r.settings_transfer ?? null,
            });
          }
          // For non-preview, the upload phase is signalled by the next `status`/`upload_progress` events.
        },
        onUploadProgress: (u) =>
          setState((cur) => {
            if (cur.kind === 'slicing') {
              return { kind: 'uploading', file, info, uploadId: '', percent: u.percent };
            }
            if (cur.kind === 'uploading') return { ...cur, percent: u.percent };
            return cur;
          }),
        onPrintStarted: (p) => {
          toast.success(`Print started on ${activePrinterName ?? p.printer_id}`);
          setState({ kind: 'sent' });
          navigate('/');
        },
        onError: (e) => {
          setState({
            kind: 'imported',
            file,
            info,
            banner: { variant: 'error', title: 'Slicing failed', details: e.error },
          });
        },
        onDone: () => {
          // Stream closed; state should already be terminal (previewReady/sent/error).
        },
      },
    );
  }

  async function confirmPrint() {
    if (state.kind !== 'previewReady') return;
    try {
      await printFromPreview(state.previewId, activePrinterId ?? undefined);
      toast.success(`Print started on ${activePrinterName ?? 'printer'}`);
      setState({ kind: 'sent' });
      navigate('/');
    } catch (err) {
      toast.error(`Print failed: ${(err as Error).message}`);
    }
  }

  function cancelSlicing() {
    if (state.kind !== 'slicing') return;
    stream.cancel();
    setState({ kind: 'imported', file: state.file, info: state.info, banner: undefined });
  }

  async function cancelUploading() {
    if (state.kind !== 'uploading') return;
    if (!state.uploadId) {
      stream.cancel();
      setState({ kind: 'imported', file: state.file, info: state.info, banner: undefined });
      return;
    }
    try {
      await cancelUpload(state.uploadId);
      // The SSE error handler will move state back to imported.
    } catch (err) {
      toast.error(`Cancel failed: ${(err as Error).message}`);
    }
  }

  // Build options for the select rows.
  const machineOptions: SettingOption[] = useMemo(() => {
    const base: SettingOption[] = (machinesQuery.data ?? []).map((m) => ({ value: m.setting_id, label: m.name }));
    if (state.kind !== 'empty' && state.kind !== 'sent') {
      const fileMachine = state.info.printer.printer_settings_id;
      if (fileMachine && !base.some((o) => o.value === fileMachine)) {
        base.unshift({ value: fileMachine, label: fileMachine, fromFileMismatch: true });
      }
    }
    return base;
  }, [machinesQuery.data, state]);

  const processOptions: SettingOption[] = useMemo(() => {
    return (processesQuery.data ?? []).map((p) => ({ value: p.setting_id, label: p.name }));
  }, [processesQuery.data]);

  const plateTypeOptions: SettingOption[] = useMemo(() => {
    return (plateTypesQuery.data ?? []).map((p) => ({ value: p.value, label: p.label }));
  }, [plateTypesQuery.data]);

  // --- Render ---

  return (
    <div className="flex flex-col gap-6">
      <DropOverlay visible={dragging} />

      <header>
        <h1 className="text-[28px] font-extrabold tracking-tight text-white">Print</h1>
      </header>

      {state.kind === 'empty' && (
        <DropZoneCard onFile={onDropFile} targetPrinterName={activePrinterName} />
      )}

      {(state.kind === 'slicing' || state.kind === 'uploading') && (
        <SlicingProgressCard
          title={state.kind === 'slicing' ? 'Slicing…' : 'Uploading to printer…'}
          statusLine={state.kind === 'slicing' ? state.statusLine : `${state.percent}%`}
          percent={state.kind === 'slicing' ? state.percent : state.percent}
          onCancel={state.kind === 'slicing' ? cancelSlicing : cancelUploading}
        />
      )}

      {(state.kind === 'imported' || state.kind === 'previewReady') && (
        <div className={cn('flex flex-col gap-5')}>
          <PlateCard
            filename={state.file.name}
            info={state.info}
            selectedPlateId={selectedPlateId}
            onClear={clearImport}
          />
          <SlicingSettingsGroup
            settings={settings}
            onChange={setSettings}
            machineOptions={machineOptions}
            processOptions={processOptions}
            plateTypeOptions={plateTypeOptions}
            disabled={state.kind === 'previewReady'}
          />
          <FilamentsGroup
            projectFilaments={state.info.filaments}
            trays={trays}
            mapping={filamentMapping}
            onChange={setFilamentMapping}
            disabled={state.info.has_gcode || state.kind === 'previewReady'}
          />
          {state.kind === 'imported' && state.banner && (
            <InfoBanner
              variant={state.banner.variant}
              title={state.banner.title}
              message={state.banner.message}
              details={state.banner.details}
            />
          )}
          {state.kind === 'previewReady' && (
            <>
              <InfoBanner
                variant="success"
                title="Preview ready"
                message="Review the sliced file, then confirm the print."
              />
              <SettingsTransferNote info={state.transfer} />
            </>
          )}
          <ActionButtons
            kind={state.kind}
            previewId={state.kind === 'previewReady' ? state.previewId : null}
            onPreview={() => startSlicing(state.file, state.info, true)}
            onPrint={() => startSlicing(state.file, state.info, false)}
            onReslice={() => startSlicing(state.file, state.info, true)}
            onConfirmPrint={confirmPrint}
          />
        </div>
      )}
    </div>
  );
}

function ActionButtons({
  kind,
  previewId,
  onPreview,
  onPrint,
  onReslice,
  onConfirmPrint,
}: {
  kind: 'imported' | 'previewReady';
  previewId: string | null;
  onPreview: () => void;
  onPrint: () => void;
  onReslice: () => void;
  onConfirmPrint: () => void;
}) {
  if (kind === 'imported') {
    return (
      <div className="grid grid-cols-2 gap-2.5">
        <Button
          type="button"
          onClick={onPreview}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          ◉ Preview
        </Button>
        <Button
          type="button"
          onClick={onPrint}
          className="rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11 text-[14px] font-semibold"
        >
          ⎙ Print
        </Button>
      </div>
    );
  }
  // previewReady — three buttons: Re-slice | Download 3MF | Confirm Print
  return (
    <div className="grid grid-cols-3 gap-2.5">
      <Button
        type="button"
        onClick={onReslice}
        className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
      >
        <RotateCcw className="w-4 h-4 mr-1.5" aria-hidden /> Re-slice
      </Button>
      <a
        href={`/api/print?preview_id=${encodeURIComponent(previewId ?? '')}&slice_only=true`}
        download
        className="inline-flex items-center justify-center rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
      >
        Download 3MF
      </a>
      <Button
        type="button"
        onClick={onConfirmPrint}
        className="rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11 text-[14px] font-semibold"
      >
        Confirm Print
      </Button>
    </div>
  );
}
