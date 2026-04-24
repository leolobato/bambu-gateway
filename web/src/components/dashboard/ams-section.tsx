import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { TrayRow } from '@/components/tray-row';
import { Badge } from '@/components/ui/badge';
import { normalizeTrayColor } from '@/lib/filament-color';
import type { AMSResponse, AMSTray, AMSUnit } from '@/lib/api/types';

export function AmsSection({
  activePrinterId,
  ams,
  activeTrayId,
}: {
  activePrinterId: string;
  ams: AMSResponse;
  activeTrayId: number | null;
}) {
  if (ams.printer_id !== activePrinterId) {
    return (
      <p className="text-xs text-text-2">
        AMS data is only available for the default printer (backend follow-up planned).
      </p>
    );
  }

  return (
    <section className="flex flex-col gap-4">
      {ams.units.map((unit) => (
        <AmsUnitGroup
          key={unit.id}
          unit={unit}
          trays={ams.trays.filter((t) => t.ams_id === unit.id)}
          activeTrayId={activeTrayId}
        />
      ))}
      {ams.vt_tray && (
        <ExternalSpoolGroup tray={ams.vt_tray} activeTrayId={activeTrayId} />
      )}
    </section>
  );
}

function AmsUnitGroup({
  unit,
  trays,
  activeTrayId,
}: {
  unit: AMSUnit;
  trays: AMSTray[];
  activeTrayId: number | null;
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
            <AmsTrayRow key={`${tray.ams_id}-${tray.slot}`} tray={tray} activeTrayId={activeTrayId} />
          ))}
        </div>
      )}
    </div>
  );
}

function ExternalSpoolGroup({
  tray,
  activeTrayId,
}: {
  tray: AMSTray;
  activeTrayId: number | null;
}) {
  return (
    <div className="flex flex-col gap-2">
      <header className="flex items-center justify-between">
        <span className="text-base font-semibold text-white">External Spool</span>
      </header>
      <AmsTrayRow tray={tray} activeTrayId={activeTrayId} />
    </div>
  );
}

function AmsTrayRow({
  tray,
  activeTrayId,
}: {
  tray: AMSTray;
  activeTrayId: number | null;
}) {
  const color = normalizeTrayColor(tray.tray_color);
  const isEmpty = color == null && !tray.tray_type;
  const inUse = activeTrayId != null && activeTrayId === tray.tray_id;

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
    />
  );
}
