import { useMemo, useState } from 'react';
import {
  Layers, ChevronRight, SlidersHorizontal,
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { ProcessOptionRow } from './process-option-row';
import { FilamentAllSheet } from './filament-all-sheet';
import {
  useFilamentOptions,
  useFilamentLayout,
  useFilamentBaseline,
} from '@/lib/api/filament-options';
import { effectiveValue, revertTarget } from '@/lib/process/effective-value';
import { usePrintContext } from '@/lib/print-context';

export interface FilamentSlot {
  slot: number;
  label: string;
  settingId: string;
}

interface Props {
  slots: FilamentSlot[];
}

export function FilamentParametersCard({ slots }: Props) {
  const {
    filamentOverrides, setFilamentOverride, revertFilamentOverride,
  } = usePrintContext();

  const [activeSlot, setActiveSlot] = useState<number>(slots[0]?.slot ?? 0);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  // Keep the active slot valid when the slot list changes (e.g. plate switch).
  const active = slots.find((s) => s.slot === activeSlot) ?? slots[0];
  const activeSlotId = active?.slot ?? 0;

  const optionsQuery = useFilamentOptions();
  const layoutQuery = useFilamentLayout();
  const baselineQuery = useFilamentBaseline(active?.settingId || undefined);

  const isLoading = optionsQuery.isLoading || layoutQuery.isLoading;
  const loadError = optionsQuery.error || layoutQuery.error;
  const catalogue = optionsQuery.data ?? null;
  const baseline = baselineQuery.data ?? {};
  const overrides = filamentOverrides[activeSlotId] ?? {};

  // v1 has no file-modified filament keys — rows are the user-edited keys only.
  const rowKeys = useMemo(() => Object.keys(overrides), [overrides]);
  const modifiedCount = rowKeys.length;

  // Map each option key to its parent page label so we can group rows
  // under the same uppercase headers used in the All-sheet drill-down.
  const layout = layoutQuery.data ?? null;
  const pageByKey = useMemo(() => {
    const map = new Map<string, string>();
    for (const page of layout?.pages ?? []) {
      for (const group of page.optgroups) {
        for (const k of group.options) map.set(k, page.label);
      }
    }
    return map;
  }, [layout]);

  // Group rowKeys by parent page in layout order; keys the layout doesn't
  // know about land in a trailing 'Other' bucket so nothing is dropped.
  const groupedRows = useMemo(() => {
    const buckets = new Map<string, string[]>();
    for (const key of rowKeys) {
      const page = pageByKey.get(key) ?? 'Other';
      const arr = buckets.get(page);
      if (arr) arr.push(key);
      else buckets.set(page, [key]);
    }
    const ordered: Array<{ page: string; keys: string[] }> = [];
    for (const p of layout?.pages ?? []) {
      const keys = buckets.get(p.label);
      if (keys && keys.length > 0) ordered.push({ page: p.label, keys });
    }
    const others = buckets.get('Other');
    if (others && others.length > 0) ordered.push({ page: 'Other', keys: others });
    return ordered;
  }, [rowKeys, pageByKey, layout]);

  const slotLabel = (s: FilamentSlot) => `${s.slot} · ${s.label}`;

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 mb-3">
        <Layers className="size-3.5 text-accent" aria-hidden />
        <span className="text-base font-semibold">Filament settings</span>
        {modifiedCount > 0 && (
          <Badge variant="secondary" className="ml-auto">
            {modifiedCount} modified
          </Badge>
        )}
      </div>

      {/* Slot selector — dropdown when there are multiple used slots,
          a static label when there's only one. */}
      <div className="mb-3">
        {slots.length > 1 ? (
          <Select
            value={String(activeSlotId)}
            onValueChange={(v) => {
              setActiveSlot(Number(v));
              setExpandedKey(null);
            }}
          >
            <SelectTrigger aria-label="Filament slot">
              <SelectValue>{active ? slotLabel(active) : ''}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {slots.map((s) => (
                <SelectItem key={s.slot} value={String(s.slot)}>
                  {slotLabel(s)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <p className="text-sm text-muted-foreground">
            {active ? slotLabel(active) : ''}
          </p>
        )}
      </div>

      {isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : loadError ? (
        <Alert variant="destructive">
          <AlertDescription className="flex items-center justify-between gap-2">
            <span>Couldn't load filament settings — Retry</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void optionsQuery.refetch();
                void layoutQuery.refetch();
              }}
            >
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : modifiedCount === 0 ? (
        <div className="flex flex-col items-center gap-2 py-4 text-center">
          <SlidersHorizontal className="size-7 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            No customizations from default profile
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {groupedRows.map(({ page, keys }) => (
            <section key={page}>
              <h3 className="text-xs font-semibold tracking-wide uppercase text-muted-foreground pb-2">
                {page}
              </h3>
              <div className="rounded-lg border border-border/40 overflow-hidden">
                {keys.map((key) => {
                  const option = catalogue?.options[key];
                  if (!option) {
                    // Catalogue missing this key — degraded read-only row.
                    return (
                      <div key={key} className="px-3 py-2.5 text-sm font-mono">
                        {key}: {overrides[key] ?? '?'}
                      </div>
                    );
                  }
                  const value = effectiveValue(key, overrides, null, baseline, catalogue) ?? '';
                  const revertTo = revertTarget(key, null, baseline, catalogue) ?? '';
                  return (
                    <ProcessOptionRow
                      key={key}
                      option={option}
                      value={value}
                      revertTo={revertTo}
                      isUserEdited={key in overrides}
                      isFileModified={false}
                      showTooltipCaption={false}
                      isExpanded={expandedKey === key}
                      onToggleExpand={() =>
                        setExpandedKey((prev) => (prev === key ? null : key))
                      }
                      onCommit={(next) => setFilamentOverride(activeSlotId, key, next)}
                      onRevert={() => revertFilamentOverride(activeSlotId, key)}
                    />
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}

      <Button
        variant="secondary"
        className="mt-3 w-full"
        onClick={() => setSheetOpen(true)}
      >
        Show all settings
        <ChevronRight className="size-3.5 ml-1" aria-hidden />
      </Button>

      {active && (
        <FilamentAllSheet
          slot={activeSlotId}
          settingId={active.settingId}
          label={active.label}
          open={sheetOpen}
          onOpenChange={setSheetOpen}
        />
      )}
    </Card>
  );
}
