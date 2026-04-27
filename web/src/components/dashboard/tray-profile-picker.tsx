import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ChevronDown, Loader2, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { listPrinters } from '@/lib/api/printers';
import { setAmsFilament } from '@/lib/api/printer-commands';
import {
  getSlicerFilaments,
  getSlicerMachines,
} from '@/lib/api/slicer-profiles';
import type { AMSTray, SlicerFilament } from '@/lib/api/types';
import { cn } from '@/lib/utils';

/**
 * Filament-profile picker for one AMS tray. Sends `ams_filament_setting`
 * over MQTT via the gateway when the user selects a filament — the printer
 * echoes the new tray state back through its `pushall` report and the
 * dashboard's AMS query picks up the change automatically.
 *
 * Two visual states:
 *  - Collapsed: a row showing the AMS-reported filament with a chevron.
 *  - Expanded: an inline cmdk Command palette with search + the AMS-
 *    assignable filament list filtered to this printer's machine.
 */
export function TrayProfilePicker({
  printerId,
  tray,
  trayLabel,
}: {
  printerId: string;
  tray: AMSTray;
  trayLabel: string;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');

  // Resolve the active printer's `machine_model` to a slicer machine
  // setting_id (e.g. "GM020") so the filament list can be filtered to
  // AMS-assignable profiles for THIS printer specifically.
  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: listPrinters,
  });
  const machinesQuery = useQuery({
    queryKey: ['slicer', 'machines'],
    queryFn: getSlicerMachines,
  });

  const machineSettingId = useMemo(() => {
    const printer = printersQuery.data?.printers.find((p) => p.id === printerId);
    const raw = printer?.machine_model?.trim();
    if (!raw) return null;
    // Settings UI's MachineModelCombobox stores the slicer `setting_id`
    // directly. Older configs may store the printer_model name or the full
    // machine name — match all three to be safe.
    const machines = machinesQuery.data ?? [];
    const matched =
      machines.find((m) => m.setting_id === raw) ||
      machines.find((m) => m.printer_model === raw) ||
      machines.find((m) => m.name === raw);
    return matched?.setting_id ?? null;
  }, [printersQuery.data, machinesQuery.data, printerId]);

  // Don't pull the (often 200+ entry) filament list until the picker is
  // actually opened. 5-min staleTime so reopening the picker is instant.
  const filamentsQuery = useQuery({
    queryKey: ['slicer', 'filaments', machineSettingId, 'ams-assignable'],
    queryFn: () =>
      getSlicerFilaments({
        machine: machineSettingId ?? undefined,
        amsAssignable: true,
      }),
    staleTime: 5 * 60_000,
    enabled: open && machineSettingId != null,
  });

  const filtered = useMemo<SlicerFilament[]>(() => {
    const data = filamentsQuery.data ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return data;
    return data.filter(
      (f) =>
        f.name.toLowerCase().includes(q) ||
        f.setting_id.toLowerCase().includes(q),
    );
  }, [search, filamentsQuery.data]);

  // Bambu's vt_tray uses reserved (ams_id=255, tray_id=254) values; AMS
  // bays use the actual `ams_id` and per-AMS `tray_id` (0..3). The gateway
  // reports both on AMSTray and we forward them as-is.
  const setMut = useMutation({
    mutationFn: (filament: SlicerFilament) =>
      setAmsFilament(printerId, tray.ams_id, tray.tray_id, {
        settingId: filament.setting_id,
        // Keep the spool's existing on-screen colour when present so a
        // profile change doesn't repaint the swatch unexpectedly. Tray
        // color from the gateway is "RRGGBBAA" with no leading "#".
        trayColor: tray.tray_color || undefined,
      }).then(() => filament),
    onSuccess: (filament) => {
      toast.success(`Assigned ${filament.name} to ${trayLabel}`);
      // Re-poll the AMS so the row updates with the new tray_info_idx.
      queryClient.invalidateQueries({ queryKey: ['ams', printerId] });
      closePicker();
    },
    onError: (err: Error) => {
      toast.error(`Couldn't assign filament: ${err.message}`);
    },
  });

  const matchedName = tray.matched_filament?.name ?? null;
  const displayName = matchedName ?? 'Unknown filament';

  function openPicker() {
    setSearch('');
    setOpen(true);
  }

  function closePicker() {
    setOpen(false);
    setSearch('');
  }

  if (!open) {
    return (
      <section className="flex flex-col gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
          Filament profile
        </div>
        <button
          type="button"
          onClick={openPicker}
          aria-label={`Change filament profile for ${trayLabel}`}
          aria-expanded={false}
          className={cn(
            'group flex items-center gap-3 min-h-[44px] rounded-md',
            'bg-card border border-border px-3 py-2.5 text-left',
            'hover:bg-bg-1 transition-colors',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          )}
        >
          <div className="min-w-0 flex-1">
            <div className="text-[14px] font-medium text-text-0 truncate">
              {displayName}
            </div>
            <div className="mt-0.5 text-[11px] text-text-2">
              {tray.matched_filament?.setting_id
                ? `${tray.matched_filament.setting_id} · tap to change`
                : 'Tap to assign a profile'}
            </div>
          </div>
          <ChevronDown
            className="h-4 w-4 text-text-2 shrink-0 transition-transform group-hover:text-text-1"
            aria-hidden
          />
        </button>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-2">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-text-2">
        Filament profile
      </div>
      <Command
        className="rounded-md border border-border bg-card"
        // Manual filtering: substring match across both `name` and
        // `setting_id` so users can search either form.
        shouldFilter={false}
      >
        <CommandInput
          aria-label="Search filaments"
          placeholder="Search filaments…"
          value={search}
          onValueChange={setSearch}
          autoFocus
        />
        <CommandList className="max-h-[50dvh]">
          {!machineSettingId && (
            <div className="flex items-center gap-1.5 px-3 py-4 text-[12px] text-text-2">
              {printersQuery.isLoading || machinesQuery.isLoading ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  Resolving printer…
                </>
              ) : (
                "Can't resolve this printer to a slicer machine profile."
              )}
            </div>
          )}

          {machineSettingId != null && filamentsQuery.isLoading && (
            <div className="flex flex-col gap-1.5 px-2 py-2">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          )}

          {filamentsQuery.isError && (
            <div className="flex flex-col items-center gap-2 px-3 py-6 text-center">
              <p className="text-[12px] text-danger">
                Couldn't load filaments.
              </p>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => filamentsQuery.refetch()}
                className="h-8 gap-1.5 text-text-1 hover:text-text-0"
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                Retry
              </Button>
            </div>
          )}

          {filamentsQuery.isSuccess &&
            filamentsQuery.data.length === 0 && (
              <CommandEmpty>
                No AMS-assignable filaments for this printer.
              </CommandEmpty>
            )}

          {filamentsQuery.isSuccess &&
            filamentsQuery.data.length > 0 &&
            filtered.length === 0 && (
              <CommandEmpty>No matching filaments.</CommandEmpty>
            )}

          {filtered.map((f) => {
            const selected = tray.matched_filament?.setting_id === f.setting_id;
            const submitting = setMut.isPending && setMut.variables?.setting_id === f.setting_id;
            return (
              <CommandItem
                key={f.setting_id}
                value={f.setting_id}
                onSelect={() => {
                  if (setMut.isPending) return;
                  setMut.mutate(f);
                }}
                aria-current={selected}
                disabled={setMut.isPending}
                className="gap-2 items-start"
              >
                <span
                  className={cn(
                    'flex h-4 w-4 items-center justify-center shrink-0 mt-0.5',
                    selected ? 'text-accent' : 'text-transparent',
                  )}
                  aria-hidden
                >
                  {submitting ? (
                    <Loader2 className="h-4 w-4 animate-spin text-accent" />
                  ) : (
                    <Check className="h-4 w-4" />
                  )}
                </span>
                <div className="flex flex-col min-w-0 flex-1">
                  <span className="text-[13px] truncate">{f.name}</span>
                  <span className="font-mono text-[10px] text-text-2 truncate">
                    {f.setting_id}
                  </span>
                </div>
              </CommandItem>
            );
          })}
        </CommandList>
        <div className="flex items-center justify-end border-t border-border px-2 py-1.5">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={closePicker}
            disabled={setMut.isPending}
            className="h-7 text-[12px] text-text-1 hover:text-text-0"
          >
            Cancel
          </Button>
        </div>
      </Command>
    </section>
  );
}
