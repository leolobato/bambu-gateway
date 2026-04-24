import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { TrayRow } from '@/components/tray-row';
import { Badge } from '@/components/ui/badge';
import { TraySheet, type TraySheetSelection } from '@/components/dashboard/tray-sheet';
import { normalizeTrayColor } from '@/lib/filament-color';
import type { AMSResponse, AMSTray, AMSUnit } from '@/lib/api/types';

export function AmsSection({
  printerId,
  ams,
  activeTrayId,
}: {
  printerId: string;
  ams: AMSResponse;
  activeTrayId: number | null;
}) {
  const [selection, setSelection] = useState<TraySheetSelection | null>(null);

  if (ams.units.length === 0 && !ams.vt_tray) return null;

  return (
    <>
      <section className="flex flex-col gap-4">
        {ams.units.map((unit) => (
          <AmsUnitGroup
            key={unit.id}
            unit={unit}
            trays={ams.trays.filter((t) => t.ams_id === unit.id)}
            activeTrayId={activeTrayId}
            onSelectTray={(tray) =>
              setSelection({ tray, unit, label: `Tray ${tray.slot + 1}` })
            }
          />
        ))}
        {ams.vt_tray && (
          <ExternalSpoolGroup
            tray={ams.vt_tray}
            activeTrayId={activeTrayId}
            onSelect={(tray) =>
              setSelection({ tray, unit: null, label: 'External Spool' })
            }
          />
        )}
      </section>
      <TraySheet
        printerId={printerId}
        selection={selection}
        onClose={() => setSelection(null)}
      />
    </>
  );
}

function AmsUnitGroup({
  unit,
  trays,
  activeTrayId,
  onSelectTray,
}: {
  unit: AMSUnit;
  trays: AMSTray[];
  activeTrayId: number | null;
  onSelectTray: (tray: AMSTray) => void;
}) {
  const storageKey = `bg.ams-unit-${unit.id}.collapsed`;
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(storageKey) === '1';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, collapsed ? '1' : '0');
    } catch {
      // ignore
    }
  }, [collapsed, storageKey]);

  const sorted = [...trays].sort((a, b) => a.slot - b.slot);
  const headerLabel = `AMS ${unit.id + 1}`;

  return (
    <div className="flex flex-col gap-2">
      <header className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="flex items-center gap-1.5 text-base font-semibold text-white"
          aria-expanded={!collapsed}
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4 text-text-1" aria-hidden />
          ) : (
            <ChevronDown className="w-4 h-4 text-text-1" aria-hidden />
          )}
          {headerLabel}
        </button>
        {unit.humidity >= 0 && (
          <span className="text-xs text-text-1 font-mono tabular-nums">
            {unit.humidity}% RH
          </span>
        )}
      </header>
      {!collapsed && (
        <div className="flex flex-col gap-2">
          {sorted.map((tray) => (
            <AmsTrayRow
              key={`${tray.ams_id}-${tray.slot}`}
              tray={tray}
              activeTrayId={activeTrayId}
              onSelect={() => onSelectTray(tray)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ExternalSpoolGroup({
  tray,
  activeTrayId,
  onSelect,
}: {
  tray: AMSTray;
  activeTrayId: number | null;
  onSelect: (tray: AMSTray) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <header className="flex items-center justify-between">
        <span className="text-base font-semibold text-white">External Spool</span>
      </header>
      <AmsTrayRow tray={tray} activeTrayId={activeTrayId} onSelect={() => onSelect(tray)} />
    </div>
  );
}

function AmsTrayRow({
  tray,
  activeTrayId,
  onSelect,
}: {
  tray: AMSTray;
  activeTrayId: number | null;
  onSelect: () => void;
}) {
  const color = normalizeTrayColor(tray.tray_color);
  const isEmpty = color == null && !tray.tray_type;
  // `active_tray` from PrinterStatus is the global slot index (0..N for AMS
  // bays, 254 for the external spool), matching `tray.slot` set by the
  // gateway. Comparing against `tray_id` (per-AMS 0..3) silently breaks
  // multi-AMS setups because tray 0 of every unit would falsely match.
  const inUse = activeTrayId != null && activeTrayId === tray.slot;

  const subtitleParts: string[] = [];
  if (tray.tray_type) subtitleParts.push(tray.tray_type);
  if (tray.filament_id) subtitleParts.push(tray.filament_id);

  const filamentName =
    tray.matched_filament?.name ||
    tray.tray_sub_brands ||
    null;

  return (
    <TrayRow
      colorDot={color}
      title={`Tray ${tray.slot + 1}`}
      subtitle={subtitleParts.length > 0 ? subtitleParts.join(' · ') : undefined}
      body={
        isEmpty ? (
          <span className="italic text-text-2">Empty</span>
        ) : (
          filamentName ?? <span className="italic text-text-2">Unknown filament</span>
        )
      }
      right={inUse && <Badge className="bg-accent/15 text-accent border-transparent">In Use</Badge>}
      highlighted={inUse}
      onClick={onSelect}
    />
  );
}
