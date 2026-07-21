import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { DropZoneCard, DropOverlay } from '@/components/print/drop-zone';
import { PlateCard } from '@/components/print/plate-card';
import { SlicingSettingsGroup } from '@/components/print/slicing-settings-group';
import type { SettingOption } from '@/components/print/setting-row';
import { FilamentsGroup, type FilamentMapping } from '@/components/print/filaments-group';
import { ProcessParametersCard } from '@/components/print/process-parameters-card';
import { ProcessAllSheet } from '@/components/print/process-all-sheet';
import { FilamentParametersCard } from '@/components/print/filament-parameters-card';
import { InfoBanner } from '@/components/print/info-banner';
import { SlicingProgressCard } from '@/components/print/slicing-progress-card';
import { ImportingCard } from '@/components/print/importing-card';
import { SettingsTransferNote } from '@/components/print/settings-transfer-note';
import { PrintEstimationCard } from '@/components/print/print-estimation-card';
import { parse3mf } from '@/lib/api/3mf';
import {
  getSlicerMachines,
  getSlicerProcesses,
  getSlicerPlateTypes,
  resolveForMachine,
} from '@/lib/api/slicer-profiles';
import { getAms } from '@/lib/api/ams';
import { listPrinters } from '@/lib/api/printers';
import { getFilamentMatches } from '@/lib/api/filament-matches';
import { cancelUpload, getUploadState } from '@/lib/api/uploads';
import { printFromJob, printGcodeFile } from '@/lib/api/print';
import {
  cancelSliceJob,
  fetchReprintConfig,
  fetchSliceJob,
  submitSliceJob,
  sliceJobInputUrl,
  sliceJobOutputUrl,
  sliceJobThumbnailUrl,
} from '@/lib/api/slice-jobs';
import { fetchProcessProfile } from '@/lib/api/process-options';
import { notifyDroppedOverrides, notifyDroppedFilamentOverrides } from '@/lib/process/drop-notice';
import { useDropZone } from '@/lib/use-drop-zone';
import { usePrinterContext } from '@/lib/printer-context';
import { usePrintContext, type BannerData, type PrintState } from '@/lib/print-context';
import {
  createStlDraft,
  layoutStlDraft,
  materializeStlDraft,
} from '@/lib/api/stl-drafts';
const StlPreviewCard = lazy(() =>
  import('@/components/print/stl-preview-card').then((m) => ({ default: m.StlPreviewCard })),
);
import type {
  AMSTray,
  PrintEstimate,
  ResolvedFilament,
  SlicerProcess,
  StlLayoutAction,
  ThreeMFInfo,
} from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { hasPrintEstimate } from '@/lib/print-estimate';
import { buildReprintMapping } from '@/lib/print/reprint-mapping';

// PrintState and BannerData live in `lib/print-context.tsx` so the state
// machine and any in-flight slice/upload polling survive navigation away
// from /print (e.g. flipping to /jobs and back).

function pickDefaultProcess(processes: SlicerProcess[] | undefined): string {
  const usable = (processes ?? []).filter((p) => p.setting_id);
  const isNormalLayer = (p: SlicerProcess) => {
    const layerHeight = Number.parseFloat(p.layer_height ?? '');
    return Number.isFinite(layerHeight) && Math.abs(layerHeight - 0.2) < 0.0001;
  };
  const standard = usable.find(
    (p) => isNormalLayer(p) && p.name.toLowerCase().includes('standard'),
  );
  return standard?.setting_id
    ?? usable.find(isNormalLayer)?.setting_id
    ?? usable[0]?.setting_id
    ?? '';
}

/**
 * Predicate for "this filament position prints on the selected plate". Shared
 * by `buildFilamentProfilesPayload` and `computeFilamentSlots` so the override
 * slot keys, the submitted payload keys, and the slicer's positional
 * `filament_settings_ids` always line up. `position` is the index in the dense
 * `info.filaments` list, NOT the authored `filament.index` (which is sparse).
 */
function makeIsPositionUsed(info: ThreeMFInfo, selectedPlateId: number) {
  const platUsed = info.plates.find((p) => p.id === selectedPlateId)?.used_filament_indices;
  return (filament: { index: number; used: boolean }) => {
    if (platUsed) return platUsed.includes(filament.index);
    if (info.filaments.some((f) => f.used)) return filament.used;
    return true;
  };
}

/**
 * One `{ slot, label, settingId }` per USED filament position, mirroring the
 * per-position resolution in `buildFilamentProfilesPayload`: the matched tray's
 * filament wins, then the cross-machine resolver fallback (unless the user
 * explicitly mapped the slot), else the 3MF's authored filament. `slot` is the
 * dense position so it keys overrides identically to the slice payload.
 */
function computeFilamentSlots(
  info: ThreeMFInfo,
  filamentMapping: FilamentMapping,
  trays: AMSTray[],
  resolvedFilaments: ResolvedFilament[],
  selectedPlateId: number,
): Array<{ slot: number; label: string; settingId: string }> {
  const isPositionUsed = makeIsPositionUsed(info, selectedPlateId);
  const slots: Array<{ slot: number; label: string; settingId: string }> = [];
  info.filaments.forEach((filament, position) => {
    if (!isPositionUsed(filament)) return;
    const authoredLabel = filament.setting_id || `Filament ${filament.index + 1}`;

    // 1. Matched tray filament (the user's explicit slot mapping).
    const traySlot = filamentMapping[filament.index];
    const userMapped = traySlot != null && traySlot >= 0;
    if (userMapped) {
      const matched = trays.find((t) => t.slot === traySlot)?.matched_filament;
      if (matched?.setting_id) {
        slots.push({ slot: position, label: matched.name || matched.setting_id, settingId: matched.setting_id });
        return;
      }
    }

    // 2. Cross-machine resolver fallback — skipped when the user mapped this
    // slot to a tray (their choice means don't auto-substitute), matching
    // `buildFilamentProfilesPayload`.
    if (!userMapped) {
      const r = resolvedFilaments.find((x) => x.slot === position);
      if (r && r.setting_id && r.match !== 'unchanged' && r.match !== 'none') {
        slots.push({ slot: position, label: r.name || r.setting_id, settingId: r.setting_id });
        return;
      }
    }

    // 3. The 3MF's authored filament.
    slots.push({ slot: position, label: authoredLabel, settingId: filament.setting_id });
  });
  return slots;
}

export default function PrintRoute() {
  const { activePrinterId, setActivePrinterId } = usePrinterContext();
  const navigate = useNavigate();

  const {
    state,
    setState,
    settings,
    setSettings,
    selectedPlateId,
    setSelectedPlateId,
    filamentMapping,
    setFilamentMapping,
    sliceAbortRef,
    processOverrides,
    resetAllProcessOverrides,
    setProcessOverride,
    setProcessBaseline,
    setProcessSheetOpen,
    filamentOverrides,
    setFilamentOverride,
    resetAllFilamentOverrides,
  } = usePrintContext();

  // Guards the print/slice submission buttons against double-clicks during the
  // multi-second window before the state machine moves off `imported`/`previewReady`.
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Slicer catalogs — load once, don't refetch automatically.
  const machinesQuery = useQuery({
    queryKey: ['slicer', 'machines'],
    queryFn: getSlicerMachines,
    staleTime: Infinity,
  });
  // Active printer's name (for the "Target printer" subtitle).
  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: listPrinters,
    refetchInterval: 4_000,
  });
  const printers = printersQuery.data?.printers ?? [];
  useEffect(() => {
    if (printers.length === 0) return;
    const stillExists = activePrinterId && printers.some((p) => p.id === activePrinterId);
    if (stillExists) return;
    setActivePrinterId(printers[0].id);
  }, [activePrinterId, printers, setActivePrinterId]);
  const activePrinter = printers.find((p) => p.id === activePrinterId) ?? printers[0];
  const requestPrinterId = activePrinter?.id ?? activePrinterId ?? null;
  const activePrinterName = activePrinter?.name ?? null;
  // `default_plate_type` is now surfaced on /api/printers (PrinterStatus), so
  // read it directly from the active printer status. When set, it's the
  // highest-precedence plate default in the import flow, winning over the 3MF's
  // authored plate and the resolve-for-machine fallback (mirroring the backend's
  // request → printer default → authored → machine default precedence).
  const activePrinterDefaultPlate = activePrinter?.default_plate_type?.trim() ?? '';
  const defaultMachine = useMemo(() => {
    const configuredMachine = activePrinter?.machine_model?.trim() ?? '';
    if (!configuredMachine) return '';
    const matchById = machinesQuery.data?.some((m) => m.setting_id === configuredMachine);
    return matchById ? configuredMachine : '';
  }, [activePrinter?.machine_model, machinesQuery.data]);
  const effectiveMachine = settings.machine || defaultMachine;
  // 3MF import seeds `settings.machine` with the printer's display name
  // (e.g. "Bambu Lab P2S 0.4 nozzle"); a separate effect rewrites it to a
  // setting_id once `machinesQuery` resolves. The slicer only accepts
  // setting_ids on `?machine=`, so wait for that translation before firing.
  const machineIsSettingId = !!effectiveMachine
    && !!machinesQuery.data?.some((m) => m.setting_id === effectiveMachine);
  const processesQuery = useQuery({
    queryKey: ['slicer', 'processes', effectiveMachine],
    queryFn: () => getSlicerProcesses(effectiveMachine || undefined),
    staleTime: Infinity,
    enabled: machineIsSettingId,
  });
  const defaultProcess = pickDefaultProcess(processesQuery.data);
  const effectiveProcess = settings.process || defaultProcess;
  const plateTypesQuery = useQuery({
    queryKey: ['slicer', 'plate-types'],
    queryFn: getSlicerPlateTypes,
    staleTime: Infinity,
  });

  // AMS for the active printer (for tray dropdowns).
  const amsQuery = useQuery({
    queryKey: ['ams', requestPrinterId],
    queryFn: () => getAms(requestPrinterId ?? undefined),
    refetchInterval: 4_000,
    enabled: !!requestPrinterId,
    retry: false,
  });
  const trays: AMSTray[] = useMemo(() => {
    if (!amsQuery.data) return [];
    const list = [...amsQuery.data.trays];
    if (amsQuery.data.vt_tray) list.push(amsQuery.data.vt_tray);
    return list;
  }, [amsQuery.data]);

  const queryClient = useQueryClient();

  // The 3MF stores the slicer profile *name* in `printer_settings_id` /
  // `print_settings_id` (e.g. "Bambu Lab A1 mini 0.4 nozzle"), but the
  // slicer API and `<Select>` work on the catalog's `setting_id` (e.g.
  // "GM020"). Translate name → id once the catalog loads so the picker
  // doesn't flag a real match as "different printer".
  useEffect(() => {
    if (!machinesQuery.data || !settings.machine) return;
    const matchById = machinesQuery.data.some((m) => m.setting_id === settings.machine);
    if (matchById) return;
    const byName = machinesQuery.data.find((m) => m.name === settings.machine);
    if (byName?.setting_id) {
      setSettings((prev) => ({ ...prev, machine: byName.setting_id }));
    }
  }, [machinesQuery.data, settings.machine]);

  useEffect(() => {
    if (!processesQuery.data || !settings.process) return;
    const matchById = processesQuery.data.some((p) => p.setting_id === settings.process);
    if (matchById) return;
    const byName = processesQuery.data.find((p) => p.name === settings.process);
    if (byName?.setting_id) {
      setSettings((prev) => ({ ...prev, process: byName.setting_id }));
    }
  }, [processesQuery.data, settings.process]);

  // Process-baseline refetch: whenever the active process profile changes
  // (initial load, 3MF import, user picker change), pull its system
  // baseline so the row resolver has a fallback rung between 3MF
  // modifications and catalogue defaults. User overrides are preserved
  // across profile swaps — redundant ones are harmless server-side.
  useEffect(() => {
    if (!settings.process) return;
    let cancelled = false;
    fetchProcessProfile(settings.process)
      .then((baseline) => {
        if (!cancelled) setProcessBaseline(baseline);
      })
      .catch(() => {
        if (!cancelled) setProcessBaseline({});
      });
    return () => { cancelled = true; };
  }, [settings.process, setProcessBaseline]);

  // GUI-equivalent profile fallback when the user retargets a 3MF to a
  // different printer. The slicer's `/profiles/resolve-for-machine` mirrors
  // OrcaSlicer's `PresetBundle::update_compatible`: same-alias variants of
  // the authored process/filaments win, and unsupported plate types fall
  // back to the machine's default. We feed the 3MF's authored names (not
  // the form's current values) so re-running the resolver after the user
  // edits doesn't unwind their choices.
  const infoForResolve = state.kind === 'imported' ? state.info : null;
  const resolveQuery = useQuery({
    queryKey: [
      'slicer',
      'resolve-for-machine',
      settings.machine,
      infoForResolve?.print_profile.print_settings_id ?? '',
      (infoForResolve?.filaments ?? []).map((f) => f.setting_id).join('|'),
      infoForResolve?.bed_type ?? '',
    ],
    queryFn: () =>
      resolveForMachine({
        machineId: settings.machine,
        processName: infoForResolve?.print_profile.print_settings_id ?? '',
        filamentNames: (infoForResolve?.filaments ?? []).map((f) => f.setting_id),
        plateType: infoForResolve
          ? plateTypesQuery.data?.find((p) => p.label === infoForResolve.bed_type)?.value ?? ''
          : '',
      }),
    staleTime: Infinity,
    enabled: machineIsSettingId && infoForResolve != null,
  });

  // Apply resolved process/plate-type once per machine change. Tracking the
  // "last applied" machine in a ref lets the user freely re-pick those
  // fields after the auto-apply without us clobbering them on the next
  // render. Resolved filament names flow into `buildFilamentProfilesPayload`
  // for unused-slot fallback rather than into form state.
  const appliedForMachineRef = useRef<string | null>(null);
  useEffect(() => {
    if (!resolveQuery.data) return;
    if (appliedForMachineRef.current === settings.machine) return;
    const resolved = resolveQuery.data;
    setSettings((prev) => {
      const next = { ...prev };
      if (resolved.process && resolved.process.setting_id) {
        next.process = resolved.process.setting_id;
      }
      // The printer's configured default plate (applied by the effect below)
      // outranks the resolver's authored/machine-default plate, so only let the
      // resolver govern the plate when the active printer has no default.
      if (!activePrinterDefaultPlate && resolved.plate_type && resolved.plate_type.resolved) {
        next.plateType = resolved.plate_type.resolved;
      }
      return next;
    });
    appliedForMachineRef.current = settings.machine;
  }, [resolveQuery.data, settings.machine, setSettings, activePrinterDefaultPlate]);

  // Printer default plate: the highest-precedence plate default. When the active
  // printer has a configured `default_plate_type`, seed `settings.plateType` from
  // it — over the 3MF's authored plate and the resolve-for-machine fallback —
  // but only once per (printer, machine) so a plate the user later picks is never
  // clobbered. Independent of `resolveQuery` so it still applies when the
  // resolver hasn't run (or failed). `rehydrateFromJob` pre-arms the ref to keep
  // a reprint's restored plate intact.
  const appliedPrinterPlateRef = useRef<string | null>(null);
  useEffect(() => {
    if (!activePrinterDefaultPlate) return;
    if (state.kind !== 'imported' && state.kind !== 'previewReady') return;
    const key = `${requestPrinterId ?? ''}|${settings.machine}`;
    if (appliedPrinterPlateRef.current === key) return;
    appliedPrinterPlateRef.current = key;
    setSettings((prev) =>
      prev.plateType === activePrinterDefaultPlate
        ? prev
        : { ...prev, plateType: activePrinterDefaultPlate },
    );
  }, [activePrinterDefaultPlate, requestPrinterId, settings.machine, state.kind, setSettings]);

  // Used filament slots for the "Filament settings" card. `slot` is the dense
  // position that keys overrides and the slice payload alike — see
  // `computeFilamentSlots`/`buildFilamentProfilesPayload`.
  const infoForFilamentSlots =
    state.kind === 'imported' || state.kind === 'previewReady' ? state.info : null;
  const filamentSlots = useMemo(
    () =>
      infoForFilamentSlots
        ? computeFilamentSlots(
            infoForFilamentSlots,
            filamentMapping,
            trays,
            resolveQuery.data?.filaments ?? [],
            selectedPlateId,
          )
        : [],
    [infoForFilamentSlots, filamentMapping, trays, resolveQuery.data, selectedPlateId],
  );

  // Drag-and-drop is active in every state EXCEPT slicing/uploading/importing
  // (replacing the file mid-stream would be confusing).
  const ddEnabled =
    state.kind !== 'slicing' &&
    state.kind !== 'uploading' &&
    state.kind !== 'importing';

  const importIdRef = useRef<string | null>(null);

  const importFile = useCallback(async (file: File) => {
    // Close the process-parameter sheet immediately so it doesn't linger
    // over the importing state while the new 3MF is being parsed.
    setProcessSheetOpen(false);
    // Bump the importId on every fresh pick. Stale resolutions compare
    // against importIdRef before committing — a Cancel click clears the
    // ref so any in-flight parse/match silently no-ops.
    const importId = `imp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    importIdRef.current = importId;
    setState({ kind: 'importing', file, importId });
    try {
      const info = await parse3mf(file);
      // Default plate selection.
      const firstPlate = info.plates[0]?.id ?? 1;
      setSelectedPlateId(firstPlate);
      // Pre-populate slicing settings from the 3MF if present. The 3MF stores
      // the bed-type label (e.g. "Textured PEI Plate"); the picker's value is
      // the slicer slug (e.g. "textured_pei_plate"), so map label → value via
      // the plate-types catalog.
      const bedTypeLabel = info.bed_type?.trim() ?? '';
      const matchedPlateType = bedTypeLabel
        ? plateTypesQuery.data?.find((p) => p.label === bedTypeLabel)?.value ?? ''
        : '';
      setSettings((prev) => ({
        machine: prev.machine || info.printer.printer_settings_id || '',
        process: prev.process || info.print_profile.print_settings_id || '',
        // Printer default plate (if any) outranks the 3MF's authored plate.
        plateType: prev.plateType || activePrinterDefaultPlate || matchedPlateType,
        copies: prev.copies,
      }));
      // Filament-tray defaults via backend matcher. Only ask about filaments
      // any object actually references — declared-but-unused slots get padded
      // server-side when the slice is submitted.
      const usedFilaments = info.filaments.some((f) => f.used)
        ? info.filaments.filter((f) => f.used)
        : info.filaments;
      const initialMapping: FilamentMapping = {};
      if (requestPrinterId) {
        try {
          const matches = await getFilamentMatches(requestPrinterId, usedFilaments);
          for (const m of matches.matches) {
            initialMapping[m.index] = m.preferred_tray_slot ?? -1;
          }
          // If we got matches back but none of them resolved to an AMS tray,
          // every filament row is going to read "Skip (use file's profile)" —
          // and a click-through "Slice" silently picks the file's authored
          // profile instead of whatever's loaded in the AMS. Warn the user
          // so they pick a tray (or knowingly accept the file defaults).
          const anyMatched = Object.values(initialMapping).some((v) => v >= 0);
          if (usedFilaments.length > 0 && !anyMatched) {
            toast.warning(
              "No AMS trays matched the project's filaments — pick a tray for each row, or the file's authored profiles will be used.",
            );
          }
        } catch (err) {
          // The previous behavior swallowed this silently and left the
          // mapping empty, which made it look like the auto-match worked
          // (rows showed "Skip") while the slice fell back to file defaults.
          toast.error(
            `Couldn't fetch AMS matches: ${(err as Error).message}. Pick filaments manually before slicing.`,
          );
        }
      }
      setFilamentMapping(initialMapping);
      const banner: BannerData = info.has_gcode
        ? {
            variant: 'warn',
            title: 'This 3MF already contains G-code.',
            message: 'Print as-is, or pick settings below and Preview to re-slice.',
          }
        : { variant: 'info', title: 'File parsed — slicing required.' };
      // Process-parameter editor: clear any prior overrides. Baseline is
      // resolved by the useEffect watching `settings.process` once the
      // import propagates the new setting_id.
      resetAllProcessOverrides();
      // Only commit if this import is still the current one. A Cancel
      // click (or a fresh pick) clears/replaces importIdRef.
      if (importIdRef.current !== importId) return;
      setState({ kind: 'imported', file, info, banner });
    } catch (err) {
      if (importIdRef.current !== importId) return;
      toast.error(`Failed to parse 3MF: ${(err as Error).message}`);
      setState({ kind: 'empty' });
    }
  }, [requestPrinterId, plateTypesQuery.data, activePrinterDefaultPlate, resetAllProcessOverrides, setProcessSheetOpen]);

  const rehydrateFromJob = useCallback(
    async (jobId: string) => {
      setProcessSheetOpen(false);
      const importId = `reprint-${jobId}`;
      importIdRef.current = importId;
      setState({ kind: 'importing', file: new File([], 'reprint.3mf'), importId });
      try {
        const config = await fetchReprintConfig(jobId);
        if (config.printer_id) setActivePrinterId(config.printer_id);
        const res = await fetch(sliceJobInputUrl(jobId));
        if (!res.ok) throw new Error(`couldn't load the original file (${res.status})`);
        const blob = await res.blob();
        const file = new File([blob], config.filename || 'reprint.3mf', {
          type: 'application/octet-stream',
        });
        const info = await parse3mf(file);
        if (importIdRef.current !== importId) return;

        setSelectedPlateId(config.plate_id);
        setSettings((prev) => ({
          machine: config.machine_profile || prev.machine,
          process: config.process_profile || prev.process,
          plateType: config.plate_type || prev.plateType,
          copies: config.copies ?? 1,
        }));
        // Suppress the one-time resolve-for-machine auto-apply so entering the
        // editable state later doesn't clobber the restored process/plate-type.
        appliedForMachineRef.current = config.machine_profile;
        // Likewise suppress the printer-default plate seed — the reprint's stored
        // plate is authoritative.
        appliedPrinterPlateRef.current = `${config.printer_id ?? ''}|${config.machine_profile}`;
        resetAllProcessOverrides();
        for (const [key, value] of Object.entries(config.process_overrides ?? {})) {
          setProcessOverride(key, value);
        }
        resetAllFilamentOverrides();
        for (const [slotStr, keys] of Object.entries(config.filament_overrides ?? {})) {
          const slot = Number(slotStr);
          for (const [key, value] of Object.entries(keys)) {
            setFilamentOverride(slot, key, value);
          }
        }
        setFilamentMapping(buildReprintMapping(config.filament_profiles, info));

        if (config.has_output) {
          setState({
            kind: 'previewReady',
            file,
            info,
            jobId,
            transfer: config.settings_transfer ?? null,
            estimate: config.estimate ?? null,
          });
        } else {
          setState({
            kind: 'imported',
            file,
            info,
            banner: {
              variant: 'warn',
              title: 'Previous slice is no longer available.',
              message: 'Settings were restored — Preview or Print to slice again.',
            },
          });
        }
      } catch (err) {
        if (importIdRef.current !== importId) return;
        toast.error(`Couldn't open job for reprint: ${(err as Error).message}`);
        setState({ kind: 'empty' });
      }
    },
    [
      setActivePrinterId,
      setProcessSheetOpen,
      setSelectedPlateId,
      setSettings,
      resetAllProcessOverrides,
      setProcessOverride,
      resetAllFilamentOverrides,
      setFilamentOverride,
      setFilamentMapping,
      setState,
    ],
  );

  const [searchParams, setSearchParams] = useSearchParams();
  const reprintJobId = searchParams.get('reprint');
  const reprintHandledRef = useRef<string | null>(null);
  useEffect(() => {
    if (!reprintJobId) return;
    if (reprintHandledRef.current === reprintJobId) return;
    reprintHandledRef.current = reprintJobId;
    void rehydrateFromJob(reprintJobId);
    // Strip the param so a refresh/back doesn't re-trigger the rehydrate.
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('reprint');
        return next;
      },
      { replace: true },
    );
  }, [reprintJobId, rehydrateFromJob, setSearchParams]);

  const importStl = useCallback(
    async (file: File) => {
      if (!effectiveMachine || !effectiveProcess) {
        toast.error('Pick a machine and process before importing STL.');
        return;
      }
      setProcessSheetOpen(false);
      // Reuse importId so a fresh STL pick mid-preview supersedes the prior one.
      const importId = `stl-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      importIdRef.current = importId;
      setState({ kind: 'importing', file, importId });
      try {
        const scene = await createStlDraft({
          file,
          machineProfile: effectiveMachine,
          processProfile: effectiveProcess,
          plateType: settings.plateType || undefined,
          autoOrient: false,
          arrange: true,
          center: true,
        });
        if (importIdRef.current !== importId) return;
        setState({
          kind: 'stlPreview',
          file,
          scene,
          autoOrient: false,
          applyingAction: null,
          previewPngDataUrl: null,
        });
      } catch (err) {
        if (importIdRef.current !== importId) return;
        toast.error(`Failed to import STL: ${(err as Error).message}`);
        setState({ kind: 'empty' });
      }
    },
    [effectiveMachine, effectiveProcess, settings.plateType, setProcessSheetOpen, setState],
  );

  const onDropFile = useCallback(
    (file: File) => {
      const lower = file.name.toLowerCase();
      if (lower.endsWith('.stl')) void importStl(file);
      else void importFile(file);
    },
    [importFile, importStl],
  );
  const { dragging } = useDropZone({
    accept: ['.3mf', '.stl'],
    onFile: onDropFile,
    enabled: ddEnabled,
  });

  function clearImport() {
    sliceAbortRef.current?.abort();
    sliceAbortRef.current = null;
    importIdRef.current = null;
    setState({ kind: 'empty' });
    setFilamentMapping({});
    resetAllProcessOverrides();
    setProcessSheetOpen(false);
  }

  function buildFilamentProfilesPayload(
    info: ThreeMFInfo,
  ): Record<string, { profile_setting_id: string; tray_slot: number } | string> {
    const out: Record<string, { profile_setting_id: string; tray_slot: number } | string> = {};
    // Payload keys are positions in `info.filaments` (the slicer/backend
    // build dense, positional `filament_settings_ids` lists). `filament.index`
    // is the 3MF's authored *slot* number, which can be sparse — e.g. a
    // single-filament 3MF authored on AMS slot 1 has `info.filaments=[{index:1}]`,
    // and a slot-keyed payload `{"1": ...}` would be rejected against the
    // length-1 project list.
    const isPositionUsed = makeIsPositionUsed(info, selectedPlateId);
    info.filaments.forEach((filament, position) => {
      if (!isPositionUsed(filament)) return;
      const traySlot = filamentMapping[filament.index];
      if (traySlot == null || traySlot < 0) return;
      const tray = trays.find((t) => t.slot === traySlot);
      const settingId = tray?.matched_filament?.setting_id ?? '';
      if (!settingId) return;
      out[String(position)] = { profile_setting_id: settingId, tray_slot: traySlot };
    });
    // Resolver-provided fallback for any declared slot not already in `out`.
    // Covers two cases: (a) "unused" slots the UI hides — same-alias swap on
    // a cross-machine slice; (b) "used" slots the user left on `Skip` — at
    // least carry through a machine-compatible name instead of the 3MF's
    // authored one for printer X. `r.slot` from the resolver is positional
    // (the position in the `filament_names` we sent), matching `out`'s keys.
    const resolved = resolveQuery.data?.filaments ?? [];
    for (const r of resolved) {
      const key = String(r.slot);
      if (key in out) continue;
      if (!r.setting_id || r.match === 'unchanged' || r.match === 'none') continue;
      const filamentAtPos = info.filaments[r.slot];
      if (filamentAtPos && isPositionUsed(filamentAtPos)) {
        const userMapping = filamentMapping[filamentAtPos.index];
        if (userMapping != null && userMapping >= 0) continue;
      }
      out[key] = r.setting_id;
    }
    return out;
  }

  async function startSlicing(
    file: File,
    info: ThreeMFInfo,
    preview: boolean,
    source?: {
      inputToken?: string;
      filename?: string;
      previewPngDataUrl?: string | null;
    },
  ) {
    if (!settings.machine || !settings.process) {
      toast.error('Pick a machine and process before slicing.');
      return;
    }

    // Guard the action buttons until the state machine leaves `imported`/
    // `previewReady` (set false on the error path and once we reach `slicing`).
    setIsSubmitting(true);
    sliceAbortRef.current?.abort();
    const ctrl = new AbortController();
    sliceAbortRef.current = ctrl;

    let job;
    try {
      job = await submitSliceJob({
        file: source?.inputToken ? undefined : file,
        inputToken: source?.inputToken,
        sourceFilename: source?.filename,
        thumbnailPngDataUrl: source?.previewPngDataUrl ?? undefined,
        printerId: requestPrinterId ?? undefined,
        plateId: selectedPlateId,
        machineProfile: settings.machine,
        processProfile: settings.process,
        filamentProfiles: buildFilamentProfilesPayload(info),
        plateType: settings.plateType || undefined,
        autoPrint: false,
        processOverrides,
        filamentOverrides,
        copies: settings.copies,
      });
    } catch (err) {
      setState({
        kind: 'imported',
        file,
        info,
        sourceInputToken: source?.inputToken,
        sourceFilename: source?.filename,
        sourcePreviewPngDataUrl: source?.previewPngDataUrl,
        banner: {
          variant: 'error',
          title: 'Slicing failed to start',
          details: (err as Error).message,
        },
      });
      setIsSubmitting(false);
      return;
    }

    queryClient.invalidateQueries({ queryKey: ['slice-jobs'] });
    setIsSubmitting(false);
    setState({
      kind: 'slicing',
      file,
      info,
      jobId: job.job_id,
      percent: job.progress,
      statusLine: job.phase ?? job.status,
      isPreview: preview,
      sourceInputToken: source?.inputToken,
      sourceFilename: source?.filename,
      sourcePreviewPngDataUrl: source?.previewPngDataUrl,
    });

    // Poll the job until terminal. The slice-jobs list at the bottom of the
    // page also polls (every 2s); this loop is the single-job driver that
    // transitions our local state machine.
    while (!ctrl.signal.aborted) {
      await new Promise((r) => setTimeout(r, 1000));
      if (ctrl.signal.aborted) return;
      let current;
      try {
        current = await fetchSliceJob(job.job_id);
      } catch (err) {
        if (ctrl.signal.aborted) return;
        setState({
          kind: 'imported',
          file,
          info,
          banner: {
            variant: 'error',
            title: 'Lost track of slice job',
            details: (err as Error).message,
          },
        });
        return;
      }
      setState((cur) =>
        cur.kind === 'slicing' && cur.jobId === current.job_id
          ? {
              ...cur,
              percent: current.progress,
              statusLine: current.phase ?? current.status,
            }
          : cur,
      );

      if (current.status === 'failed') {
        setState({
          kind: 'imported',
          file,
          info,
          banner: {
            variant: 'error',
            title: 'Slicing failed',
            details: current.error ?? 'Unknown error',
          },
        });
        return;
      }
      if (current.status === 'cancelled') {
        setState({ kind: 'imported', file, info, banner: undefined });
        return;
      }
      if (current.status === 'ready') {
        notifyDroppedOverrides(processOverrides, current.settings_transfer?.process_overrides_applied ?? undefined);
        notifyDroppedFilamentOverrides(
          filamentOverrides,
          current.settings_transfer?.filament_overrides_applied ?? undefined,
        );
        if (preview) {
          setState({
            kind: 'previewReady',
            file,
            info,
            jobId: current.job_id,
            transfer: current.settings_transfer ?? null,
            estimate: current.estimate ?? null,
          });
          return;
        }
        // Print intent — kick off the printer upload via /api/print {job_id}
        await startPrintUploadFromJob(current.job_id, file, info, current.estimate ?? null);
        return;
      }
    }
  }

  async function startPrintUploadFromJob(
    jobId: string,
    file: File,
    info: ThreeMFInfo,
    estimate: PrintEstimate | null,
  ) {
    let resp;
    try {
      resp = await printFromJob(jobId, requestPrinterId ?? undefined);
    } catch (err) {
      setState({
        kind: 'imported',
        file,
        info,
        banner: {
          variant: 'error',
          title: 'Could not start print',
          details: (err as Error).message,
        },
      });
      return;
    }
    queryClient.invalidateQueries({ queryKey: ['slice-jobs'] });

    if (!resp.upload_id) {
      const printerName = activePrinterName ?? resp.printer_id;
      toast.success(`Print started on ${printerName}`);
      if (hasPrintEstimate(resp.estimate ?? estimate)) {
        setState({ kind: 'sent', printerName, estimate: resp.estimate ?? estimate, jobId });
      } else {
        setState({ kind: 'sent', printerName, estimate: null, jobId });
        navigate('/');
      }
      return;
    }
    const uploadId = resp.upload_id;
    setState({ kind: 'uploading', file, info, uploadId, percent: 0 });

    while (true) {
      await new Promise((r) => setTimeout(r, 500));
      let progress;
      try {
        progress = await getUploadState(uploadId);
      } catch (err) {
        setState({
          kind: 'imported',
          file,
          info,
          banner: {
            variant: 'error',
            title: 'Upload tracking failed',
            details: (err as Error).message,
          },
        });
        return;
      }
      setState((cur) =>
        cur.kind === 'uploading' && cur.uploadId === uploadId
          ? { ...cur, percent: progress.progress }
          : cur,
      );
      if (progress.status === 'completed') {
        const printerName = activePrinterName ?? resp.printer_id;
        toast.success(`Print started on ${printerName}`);
        if (hasPrintEstimate(resp.estimate ?? estimate)) {
          setState({ kind: 'sent', printerName, estimate: resp.estimate ?? estimate, jobId });
        } else {
          setState({ kind: 'sent', printerName, estimate: null, jobId });
          navigate('/');
        }
        return;
      }
      if (progress.status === 'cancelled') {
        setState({ kind: 'imported', file, info, banner: undefined });
        return;
      }
      if (progress.status === 'failed') {
        setState({
          kind: 'imported',
          file,
          info,
          banner: {
            variant: 'error',
            title: 'Upload failed',
            details: progress.error ?? 'Unknown error',
          },
        });
        return;
      }
    }
  }

  async function confirmPrint() {
    if (state.kind !== 'previewReady') return;
    setIsSubmitting(true);
    try {
      await startPrintUploadFromJob(state.jobId, state.file, state.info, state.estimate);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function applyStlLayoutAction(action: StlLayoutAction) {
    if (state.kind !== 'stlPreview') return;
    const previous = state;
    setState({ ...previous, applyingAction: action });
    try {
      const scene = await layoutStlDraft(previous.scene.draft_token, action);
      setState((cur) =>
        cur.kind === 'stlPreview' && cur.scene.draft_token === previous.scene.draft_token
          ? { ...cur, scene, applyingAction: null, banner: undefined }
          : cur,
      );
    } catch (err) {
      setState((cur) =>
        cur.kind === 'stlPreview' && cur.scene.draft_token === previous.scene.draft_token
          ? {
              ...cur,
              applyingAction: null,
              banner: {
                variant: 'error',
                title: 'Layout failed',
                details: (err as Error).message,
              },
            }
          : cur,
      );
    }
  }

  async function acceptStlPreview() {
    if (state.kind !== 'stlPreview') return;
    const draftToken = state.scene.draft_token;
    const previewPngDataUrl = state.previewPngDataUrl;
    try {
      const materialized = await materializeStlDraft(draftToken, {
        thumbnailPngDataUrl: previewPngDataUrl,
      });
      // The materialized project carries the slicer's authoritative machine /
      // process IDs — surface those in the form so the user sees the same
      // settings the slicer will receive.
      setSelectedPlateId(materialized.info.plates[0]?.id ?? 1);
      resetAllProcessOverrides();
      setSettings((prev) => ({
        machine: materialized.info.printer.printer_settings_id || effectiveMachine,
        process: materialized.info.print_profile.print_settings_id || effectiveProcess,
        plateType: prev.plateType,
        copies: prev.copies,
      }));
      setFilamentMapping({});
      setState({
        kind: 'imported',
        file: new File([], materialized.filename, { type: 'application/octet-stream' }),
        info: materialized.info,
        sourceInputToken: materialized.input_token,
        sourceFilename: materialized.filename,
        sourcePreviewPngDataUrl: previewPngDataUrl,
        banner: { variant: 'info', title: 'File parsed — slicing required.' },
      });
    } catch (err) {
      setState((cur) =>
        cur.kind === 'stlPreview' && cur.scene.draft_token === draftToken
          ? {
              ...cur,
              banner: {
                variant: 'error',
                title: 'Could not create 3MF',
                details: (err as Error).message,
              },
            }
          : cur,
      );
    }
  }

  function cancelSlicing() {
    if (state.kind !== 'slicing') return;
    const jobId = state.jobId;
    sliceAbortRef.current?.abort();
    sliceAbortRef.current = null;
    setState({ kind: 'imported', file: state.file, info: state.info, banner: undefined });
    // Best-effort: also tell the gateway to drop the job.
    void cancelSliceJob(jobId)
      .catch(() => {
        // Job may already be terminal — ignore.
      })
      .finally(() => {
        queryClient.invalidateQueries({ queryKey: ['slice-jobs'] });
      });
  }

  async function cancelUploading() {
    if (state.kind !== 'uploading') return;
    if (!state.uploadId) {
      setState({ kind: 'imported', file: state.file, info: state.info, banner: undefined });
      return;
    }
    try {
      await cancelUpload(state.uploadId);
      // Polling loop in startPrintUploadFromJob will see the cancelled status.
    } catch (err) {
      toast.error(`Cancel failed: ${(err as Error).message}`);
    }
  }

  /**
   * Direct-print path for 3MFs that already contain G-code: skip slicing,
   * POST the file to /api/print, then poll /api/uploads/{id} for FTP progress.
   */
  async function startGcodePrint(file: File, info: ThreeMFInfo) {
    setIsSubmitting(true);
    try {
      const resp = await printGcodeFile(
        file,
        requestPrinterId ?? undefined,
        buildFilamentProfilesPayload(info),
        selectedPlateId,
      );
      if (!resp.upload_id) {
        // Synchronous success (no upload tracker created — rare).
        toast.success(`Print started on ${activePrinterName ?? resp.printer_id}`);
        if (hasPrintEstimate(resp.estimate)) {
          setState({
            kind: 'sent',
            printerName: activePrinterName ?? resp.printer_id,
            estimate: resp.estimate,
            jobId: null,
          });
        } else {
          setState({ kind: 'sent', printerName: activePrinterName ?? resp.printer_id, estimate: null, jobId: null });
          navigate('/');
        }
        return;
      }
      const uploadId = resp.upload_id;
      setState({ kind: 'uploading', file, info, uploadId, percent: 0 });

      // Poll until terminal.
      while (true) {
        await new Promise((r) => setTimeout(r, 500));
        let progress;
        try {
          progress = await getUploadState(uploadId);
        } catch (err) {
          setState({
            kind: 'imported',
            file,
            info,
            banner: { variant: 'error', title: 'Upload tracking failed', details: (err as Error).message },
          });
          return;
        }
        setState((cur) =>
          cur.kind === 'uploading' && cur.uploadId === uploadId
            ? { ...cur, percent: progress.progress }
            : cur,
        );
        if (progress.status === 'completed') {
          toast.success(`Print started on ${activePrinterName ?? resp.printer_id}`);
          if (hasPrintEstimate(resp.estimate)) {
            setState({
              kind: 'sent',
              printerName: activePrinterName ?? resp.printer_id,
              estimate: resp.estimate,
              jobId: null,
            });
          } else {
            setState({ kind: 'sent', printerName: activePrinterName ?? resp.printer_id, estimate: null, jobId: null });
            navigate('/');
          }
          return;
        }
        if (progress.status === 'cancelled') {
          setState({ kind: 'imported', file, info, banner: undefined });
          return;
        }
        if (progress.status === 'failed') {
          setState({
            kind: 'imported',
            file,
            info,
            banner: { variant: 'error', title: 'Upload failed', details: progress.error ?? 'Unknown error' },
          });
          return;
        }
      }
    } catch (err) {
      toast.error(`Print failed: ${(err as Error).message}`);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function downloadPreview() {
    if (state.kind !== 'previewReady') return;
    try {
      const res = await fetch(sliceJobOutputUrl(state.jobId));
      if (!res.ok) {
        let detail = res.statusText;
        try {
          const body = (await res.json()) as { detail?: string };
          if (body?.detail) detail = body.detail;
        } catch {
          // not JSON
        }
        throw new Error(detail);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const baseName = state.file.name.replace(/\.3mf$/i, '');
      a.download = `${baseName}_sliced.3mf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(`Download failed: ${(err as Error).message}`);
    }
  }

  // Build options for the select rows.
  // 'importing' has no parsed info yet; 'empty'/'sent' never had one.
  const fileMachineSettingId =
    state.kind === 'imported' ||
    state.kind === 'slicing' ||
    state.kind === 'previewReady' ||
    state.kind === 'uploading'
      ? state.info.printer.printer_settings_id || null
      : null;

  // Slicer profiles can include entries with empty `setting_id` (some
  // vendors ship process variants without a unique id). Radix Select rejects
  // empty-value items, so filter them out — they wouldn't be selectable
  // server-side either.
  const machineOptions: SettingOption[] = useMemo(() => {
    const base: SettingOption[] = (machinesQuery.data ?? [])
      .filter((m) => m.setting_id)
      .map((m) => ({ value: m.setting_id, label: m.name }));
    // Only add a "from file — different printer" row when the file's
    // printer_settings_id (which carries the slicer profile *name*) doesn't
    // match any catalog entry by setting_id OR by name. The translation
    // useEffect above handles the name→setting_id swap once the catalog
    // loads, so this branch is reserved for genuine cross-printer files.
    const inCatalog =
      !!fileMachineSettingId &&
      (machinesQuery.data ?? []).some(
        (m) => m.setting_id === fileMachineSettingId || m.name === fileMachineSettingId,
      );
    if (fileMachineSettingId && !inCatalog) {
      base.unshift({ value: fileMachineSettingId, label: fileMachineSettingId, fromFileMismatch: true });
    }
    return base;
  }, [machinesQuery.data, fileMachineSettingId]);

  const processOptions: SettingOption[] = useMemo(() => {
    return (processesQuery.data ?? [])
      .filter((p) => p.setting_id)
      .map((p) => ({ value: p.setting_id, label: p.name }));
  }, [processesQuery.data]);

  const plateTypeOptions: SettingOption[] = useMemo(() => {
    return (plateTypesQuery.data ?? [])
      .filter((p) => p.value)
      .map((p) => ({ value: p.value, label: p.label }));
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

      {state.kind === 'importing' && (
        <ImportingCard filename={state.file.name} onCancel={clearImport} />
      )}

      {state.kind === 'stlPreview' && (
        <Suspense fallback={<ImportingCard filename={state.file.name} onCancel={clearImport} />}>
          <StlPreviewCard
            filename={state.file.name}
            scene={state.scene}
            applyingAction={state.applyingAction}
            banner={state.banner}
            onAction={applyStlLayoutAction}
            onAccept={acceptStlPreview}
            onCancel={clearImport}
            onPreviewPng={(previewPngDataUrl) => {
              setState((cur) =>
                cur.kind === 'stlPreview' && cur.scene.draft_token === state.scene.draft_token
                  ? { ...cur, previewPngDataUrl }
                  : cur,
              );
            }}
          />
        </Suspense>
      )}

      {state.kind === 'sent' && (
        <PrintSentReceipt
          printerName={state.printerName}
          estimate={state.estimate}
          jobId={state.jobId}
          onDashboard={() => navigate('/')}
          onAnother={clearImport}
        />
      )}

      {(state.kind === 'slicing' || state.kind === 'uploading') && (
        <SlicingProgressCard
          title={state.kind === 'slicing' ? 'Slicing…' : 'Uploading to printer…'}
          statusLine={state.kind === 'slicing' ? state.statusLine : `${state.percent}%`}
          percent={state.percent}
          onCancel={state.kind === 'slicing' ? cancelSlicing : cancelUploading}
        />
      )}

      {(state.kind === 'imported' || state.kind === 'previewReady') && (
        <div className={cn('flex flex-col gap-5')}>
          <PlateCard
            filename={state.file.name}
            info={state.info}
            selectedPlateId={selectedPlateId}
            onSelectPlate={setSelectedPlateId}
            onClear={clearImport}
            disabled={state.kind === 'previewReady'}
          />
          <SlicingSettingsGroup
            settings={settings}
            onChange={setSettings}
            machineOptions={machineOptions}
            processOptions={processOptions}
            plateTypeOptions={plateTypeOptions}
            activeMachineModel={activePrinter?.machine_model || null}
            disabled={state.kind === 'previewReady'}
          />
          <ProcessParametersCard modifications={state.info.process_modifications ?? null} />
          {filamentSlots.length > 0 && <FilamentParametersCard slots={filamentSlots} />}
          <FilamentsGroup
            projectFilaments={state.info.filaments}
            usedFilamentIndices={
              state.info.plates.find((p) => p.id === selectedPlateId)?.used_filament_indices ?? null
            }
            trays={trays}
            mapping={filamentMapping}
            onChange={setFilamentMapping}
            disabled={state.kind === 'previewReady'}
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
              <PreviewThumbnail jobId={state.jobId} />
              <PrintEstimationCard estimate={state.estimate} />
              <SettingsTransferNote info={state.transfer} />
            </>
          )}
          <ActionButtons
            kind={state.kind}
            submitting={isSubmitting}
            onPreview={() => startSlicing(state.file, state.info, true, stateSourceFields(state))}
            onPrint={() =>
              state.kind === 'imported' && state.info.has_gcode
                ? startGcodePrint(state.file, state.info)
                : startSlicing(state.file, state.info, false, stateSourceFields(state))
            }
            onReslice={() => startSlicing(state.file, state.info, true, stateSourceFields(state))}
            onConfirmPrint={confirmPrint}
            onDownload={downloadPreview}
            onEdit={() =>
              state.kind === 'previewReady' &&
              setState({ kind: 'imported', file: state.file, info: state.info })
            }
          />
          <ProcessAllSheet modifications={state.info.process_modifications ?? null} />
        </div>
      )}
    </div>
  );
}

function stateSourceFields(state: PrintState): {
  inputToken?: string;
  filename?: string;
  previewPngDataUrl?: string | null;
} | undefined {
  if (
    state.kind === 'imported' ||
    state.kind === 'slicing' ||
    state.kind === 'previewReady' ||
    state.kind === 'uploading'
  ) {
    if (!state.sourceInputToken) return undefined;
    return {
      inputToken: state.sourceInputToken,
      filename: state.sourceFilename,
      previewPngDataUrl: state.sourcePreviewPngDataUrl,
    };
  }
  return undefined;
}

function PrintSentReceipt({
  printerName,
  estimate,
  jobId,
  onDashboard,
  onAnother,
}: {
  printerName: string | null;
  estimate: PrintEstimate | null;
  jobId: string | null;
  onDashboard: () => void;
  onAnother: () => void;
}) {
  return (
    <div className="flex flex-col gap-4 rounded-[24px] border border-line bg-surface-0 p-5 shadow-card">
      <InfoBanner
        variant="success"
        title={printerName ? `Print sent to ${printerName}` : 'Print sent'}
      />
      {jobId && <PreviewThumbnail jobId={jobId} />}
      <PrintEstimationCard estimate={estimate} />
      <div className="grid grid-cols-2 gap-2.5">
        <Button
          type="button"
          onClick={onAnother}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          Print another
        </Button>
        <Button
          type="button"
          onClick={onDashboard}
          className="rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11 text-[14px] font-semibold"
        >
          Dashboard
        </Button>
      </div>
    </div>
  );
}

function PreviewThumbnail({ jobId }: { jobId: string }) {
  const [hidden, setHidden] = useState(false);
  if (hidden) return null;
  return (
    <div className="flex justify-center">
      <img
        src={sliceJobThumbnailUrl(jobId)}
        alt=""
        aria-hidden
        onError={() => setHidden(true)}
        className="h-24 w-24 rounded-md border border-line bg-bg-1 object-contain"
        loading="lazy"
      />
    </div>
  );
}

function ActionButtons({
  kind,
  submitting,
  onPreview,
  onPrint,
  onReslice,
  onConfirmPrint,
  onDownload,
  onEdit,
}: {
  kind: 'imported' | 'previewReady';
  submitting: boolean;
  onPreview: () => void;
  onPrint: () => void;
  onReslice: () => void;
  onConfirmPrint: () => void;
  onDownload: () => void;
  onEdit: () => void;
}) {
  if (kind === 'imported') {
    return (
      <div className="grid grid-cols-2 gap-2.5">
        <Button
          type="button"
          onClick={onPreview}
          disabled={submitting}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          ◉ Preview
        </Button>
        <Button
          type="button"
          onClick={onPrint}
          disabled={submitting}
          className="rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11 text-[14px] font-semibold"
        >
          {submitting ? (
            <>
              <Loader2 className="w-4 h-4 mr-1.5 animate-spin" aria-hidden /> Starting…
            </>
          ) : (
            '⎙ Print'
          )}
        </Button>
      </div>
    );
  }
  // previewReady — Edit settings | Re-slice | Download 3MF, then Confirm Print.
  return (
    <div className="flex flex-col gap-2.5">
      <div className="grid grid-cols-3 gap-2.5">
        <Button
          type="button"
          onClick={onEdit}
          disabled={submitting}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          Edit settings
        </Button>
        <Button
          type="button"
          onClick={onReslice}
          disabled={submitting}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          <RotateCcw className="w-4 h-4 mr-1.5" aria-hidden /> Re-slice
        </Button>
        <Button
          type="button"
          onClick={onDownload}
          disabled={submitting}
          className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
        >
          Download 3MF
        </Button>
      </div>
      <Button
        type="button"
        onClick={onConfirmPrint}
        disabled={submitting}
        className="rounded-full bg-gradient-to-r from-accent-strong to-accent text-white border-0 h-11 text-[14px] font-semibold"
      >
        {submitting ? (
          <>
            <Loader2 className="w-4 h-4 mr-1.5 animate-spin" aria-hidden /> Starting…
          </>
        ) : (
          'Confirm Print'
        )}
      </Button>
    </div>
  );
}
