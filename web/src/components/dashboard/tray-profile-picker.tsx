import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
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
import {
  getSlicerFilaments,
  getSlicerMachines,
} from '@/lib/api/slicer-profiles';
import { useTrayProfileOverride } from '@/lib/tray-profile-overrides';
import type { AMSTray, SlicerFilament } from '@/lib/api/types';
import { cn } from '@/lib/utils';

/**
 * Filament-profile picker for one AMS tray. Two visual states:
 *  - Collapsed: a single row showing the active profile and an "Override"
 *    badge if the user has diverged from the AMS auto-match.
 *  - Expanded: an inline cmdk Command palette with search, a sticky "Keep
 *    default" row, and the AMS-assignable filament list.
 *
 * The selection is stored locally per-printer × per-slot via
 * `useTrayProfileOverride` and consumed by the print flow's
 * `buildFilamentProfilesPayload` to override `matched_filament.setting_id`.
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
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [override, setOverride] = useTrayProfileOverride(printerId, tray.slot);

  // Resolve the active printer's `machine_model` (e.g. "Bambu Lab A1 mini")
  // to a slicer machine setting_id (e.g. "GM020") so the filament list can
  // be filtered to AMS-assignable profiles for THIS printer specifically.
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
    // The Settings UI's MachineModelCombobox stores the slicer `setting_id`
    // directly (e.g. "GM020"), so try that first. Fall back to printer_model
    // ("Bambu Lab A1 mini") and full machine name ("Bambu Lab A1 mini 0.4
    // nozzle") for older configs hand-typed before the combobox existed.
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

  const overrideName = useMemo(() => {
    if (!override) return null;
    const f = filamentsQuery.data?.find((x) => x.setting_id === override);
    return f?.name ?? null;
  }, [override, filamentsQuery.data]);

  const matchedName = tray.matched_filament?.name ?? null;
  // Prefer the resolved name; while filaments are still loading we'd rather
  // show the raw setting_id than nothing.
  const displayName = override
    ? overrideName ?? override
    : matchedName ?? 'Default (auto-matched)';

  function pickFilament(f: SlicerFilament) {
    setOverride(f.setting_id);
    toast.success(`Profile set to ${f.name}`);
    closePicker();
  }

  function clearOverride() {
    setOverride(null);
    toast.success('Cleared override');
    closePicker();
  }

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
            {override ? (
              <div className="mt-0.5 flex items-center gap-1.5">
                <span className="rounded-full px-1.5 py-0.5 text-[10px] font-semibold bg-accent/15 text-accent border border-accent/40 shrink-0">
                  Override
                </span>
                {matchedName && (
                  <span
                    className="text-[11px] text-text-2 truncate min-w-0"
                    title={`AMS reports: ${matchedName}`}
                  >
                    AMS: {matchedName}
                  </span>
                )}
              </div>
            ) : (
              <div className="mt-0.5 text-[11px] text-text-2">
                Auto-matched from AMS
              </div>
            )}
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
        // Manual filtering: cmdk's default substring match uses each item's
        // `value` and is fine, but doing it ourselves lets us search both
        // `name` AND `setting_id` and keep the "Keep default" row visible
        // even when the search would otherwise hide it.
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
          <CommandItem
            value="__keep_default__"
            onSelect={clearOverride}
            aria-current={override == null}
            className="gap-2"
          >
            <CheckMark visible={override == null} />
            <div className="flex flex-col min-w-0">
              <span className="text-[13px] font-medium">
                Keep default (auto-match from AMS)
              </span>
              <span className="text-[11px] text-text-2">
                Use whatever the printer reports for this tray
              </span>
            </div>
          </CommandItem>

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
            const selected = override === f.setting_id;
            return (
              <CommandItem
                key={f.setting_id}
                value={f.setting_id}
                onSelect={() => pickFilament(f)}
                aria-current={selected}
                className="gap-2"
              >
                <CheckMark visible={selected} />
                <span className="flex-1 truncate text-[13px]">{f.name}</span>
                <span className="ml-auto pl-2 font-mono text-[10px] text-text-2 shrink-0">
                  {f.setting_id}
                </span>
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
            className="h-7 text-[12px] text-text-1 hover:text-text-0"
          >
            Cancel
          </Button>
        </div>
      </Command>
    </section>
  );
}

function CheckMark({ visible }: { visible: boolean }) {
  return (
    <span
      className={cn(
        'flex h-4 w-4 items-center justify-center shrink-0',
        visible ? 'text-accent' : 'text-transparent',
      )}
      aria-hidden
    >
      <Check className="h-4 w-4" />
    </span>
  );
}
