import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { DropZoneCard, DropOverlay } from '@/components/print/drop-zone';
import { PlateCard } from '@/components/print/plate-card';
import { SlicingSettingsGroup } from '@/components/print/slicing-settings-group';
import type { SettingOption } from '@/components/print/setting-row';
import { FilamentsGroup, type FilamentMapping } from '@/components/print/filaments-group';
import { ProcessParametersCard } from '@/components/print/process-parameters-card';
import { ProcessAllSheet } from '@/components/print/process-all-sheet';
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
  fetchSliceJob,
  submitSliceJob,
  sliceJobOutputUrl,
  sliceJobThumbnailUrl,
} from '@/lib/api/slice-jobs';
import { fetchProcessProfile } from '@/lib/api/process-options';
import { notifyDroppedOverrides } from '@/lib/process/drop-notice';
import { useDropZone } from '@/lib/use-drop-zone';
import { usePrinterContext } from '@/lib/printer-context';
import { usePrintContext, type BannerData } from '@/lib/print-context';
import type {
  AMSTray,
  PrintEstimate,
  ThreeMFInfo,
} from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { hasPrintEstimate } from '@/lib/print-estimate';

// PrintState and BannerData live in `lib/print-context.tsx` so the state
// machine and any in-flight slice/upload polling survive navigation away
// from /print (e.g. flipping to /jobs and back).

export default function PrintRoute() {
  const { activePrinterId } = usePrinterContext();
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
    setProcessBaseline,
    setProcessSheetOpen,
  } = usePrintContext();

  // Slicer catalogs — load once, don't refetch automatically.
  const machinesQuery = useQuery({
    queryKey: ['slicer', 'machines'],
    queryFn: getSlicerMachines,
    staleTime: Infinity,
  });
  // 3MF import seeds `settings.machine` with the printer's display name
  // (e.g. "Bambu Lab P2S 0.4 nozzle"); a separate effect rewrites it to a
  // setting_id once `machinesQuery` resolves. The slicer only accepts
  // setting_ids on `?machine=`, so wait for that translation before firing.
  const machineIsSettingId = !!settings.machine
    && !!machinesQuery.data?.some((m) => m.setting_id === settings.machine);
  const processesQuery = useQuery({
    queryKey: ['slicer', 'processes', settings.machine],
    queryFn: () => getSlicerProcesses(settings.machine || undefined),
    staleTime: Infinity,
    enabled: machineIsSettingId,
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
      if (resolved.plate_type && resolved.plate_type.resolved) {
        next.plateType = resolved.plate_type.resolved;
      }
      return next;
    });
    appliedForMachineRef.current = settings.machine;
  }, [resolveQuery.data, settings.machine, setSettings]);

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
        plateType: prev.plateType || matchedPlateType,
        copies: prev.copies,
      }));
      // Filament-tray defaults via backend matcher. Only ask about filaments
      // any object actually references — declared-but-unused slots get padded
      // server-side when the slice is submitted.
      const usedFilaments = info.filaments.some((f) => f.used)
        ? info.filaments.filter((f) => f.used)
        : info.filaments;
      const initialMapping: FilamentMapping = {};
      if (activePrinterId) {
        try {
          const matches = await getFilamentMatches(activePrinterId, usedFilaments);
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
  }, [activePrinterId, plateTypesQuery.data, resetAllProcessOverrides, setProcessSheetOpen]);

  const onDropFile = useCallback((file: File) => void importFile(file), [importFile]);
  const { dragging } = useDropZone({ accept: '.3mf', onFile: onDropFile, enabled: ddEnabled });

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
    const platUsed = info.plates.find((p) => p.id === selectedPlateId)?.used_filament_indices;
    const isPositionUsed = (filament: { index: number; used: boolean }) => {
      if (platUsed) return platUsed.includes(filament.index);
      if (info.filaments.some((f) => f.used)) return filament.used;
      return true;
    };
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

  async function startSlicing(file: File, info: ThreeMFInfo, preview: boolean) {
    if (!settings.machine || !settings.process) {
      toast.error('Pick a machine and process before slicing.');
      return;
    }

    sliceAbortRef.current?.abort();
    const ctrl = new AbortController();
    sliceAbortRef.current = ctrl;

    let job;
    try {
      job = await submitSliceJob({
        file,
        printerId: activePrinterId ?? undefined,
        plateId: selectedPlateId,
        machineProfile: settings.machine,
        processProfile: settings.process,
        filamentProfiles: buildFilamentProfilesPayload(info),
        plateType: settings.plateType || undefined,
        autoPrint: false,
        processOverrides,
        copies: settings.copies,
      });
    } catch (err) {
      setState({
        kind: 'imported',
        file,
        info,
        banner: {
          variant: 'error',
          title: 'Slicing failed to start',
          details: (err as Error).message,
        },
      });
      return;
    }

    queryClient.invalidateQueries({ queryKey: ['slice-jobs'] });
    setState({
      kind: 'slicing',
      file,
      info,
      jobId: job.job_id,
      percent: job.progress,
      statusLine: job.phase ?? job.status,
      isPreview: preview,
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
      resp = await printFromJob(jobId, activePrinterId ?? undefined);
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
    await startPrintUploadFromJob(state.jobId, state.file, state.info, state.estimate);
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
    try {
      const resp = await printGcodeFile(
        file,
        activePrinterId ?? undefined,
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
            onPreview={() => startSlicing(state.file, state.info, true)}
            onPrint={() =>
              state.kind === 'imported' && state.info.has_gcode
                ? startGcodePrint(state.file, state.info)
                : startSlicing(state.file, state.info, false)
            }
            onReslice={() => startSlicing(state.file, state.info, true)}
            onConfirmPrint={confirmPrint}
            onDownload={downloadPreview}
          />
          <ProcessAllSheet modifications={state.info.process_modifications ?? null} />
        </div>
      )}
    </div>
  );
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
  onPreview,
  onPrint,
  onReslice,
  onConfirmPrint,
  onDownload,
}: {
  kind: 'imported' | 'previewReady';
  onPreview: () => void;
  onPrint: () => void;
  onReslice: () => void;
  onConfirmPrint: () => void;
  onDownload: () => void;
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
      <Button
        type="button"
        onClick={onDownload}
        className="rounded-full bg-surface-1 hover:bg-surface-2 text-accent border-0 h-11 text-[14px] font-semibold"
      >
        Download 3MF
      </Button>
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
