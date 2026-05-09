import { useMemo, useState } from 'react';
import {
  Settings2, ChevronRight, SlidersHorizontal,
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ProcessOptionRow } from './process-option-row';
import {
  useProcessOptions,
  useProcessLayout,
} from '@/lib/api/process-options';
import { effectiveValue, revertTarget } from '@/lib/process/effective-value';
import { usePrintContext } from '@/lib/print-context';
import type { ProcessModifications } from '@/lib/process/types';

interface Props {
  modifications: ProcessModifications | null;
}

export function ProcessParametersCard({ modifications }: Props) {
  const {
    processOverrides, setProcessOverride, revertProcessOverride,
    processBaseline, setProcessSheetOpen,
  } = usePrintContext();

  const optionsQuery = useProcessOptions();
  const layoutQuery = useProcessLayout();
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const isLoading = optionsQuery.isLoading || layoutQuery.isLoading;
  const loadError = optionsQuery.error || layoutQuery.error;
  const catalogue = optionsQuery.data ?? null;

  // Modified rows = file-modified ∪ user-edited keys (per spec — user edits should appear here too).
  const rowKeys = useMemo(() => {
    const fromFile = modifications?.modifiedKeys ?? [];
    const fromUser = Object.keys(processOverrides);
    const merged: string[] = [...fromFile];
    for (const k of fromUser) if (!merged.includes(k)) merged.push(k);
    return merged;
  }, [modifications, processOverrides]);

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

  return (
    <Card className="p-4">
      <button
        type="button"
        onClick={() => setProcessSheetOpen(true)}
        className="flex w-full items-center gap-2 mb-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        aria-label="Open process settings"
      >
        <Settings2 className="size-3.5 text-accent" />
        <span className="text-base font-semibold">Process settings</span>
        <span className="ml-auto flex items-center gap-2">
          {modifiedCount > 0 && (
            <Badge variant="secondary">{modifiedCount} modified</Badge>
          )}
          <ChevronRight className="size-3 text-muted-foreground" />
        </span>
      </button>

      {isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : loadError ? (
        <Alert variant="destructive">
          <AlertDescription className="flex items-center justify-between gap-2">
            <span>Couldn't load process settings — Retry</span>
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
                        {key}: {modifications?.values[key] ?? processOverrides[key] ?? '?'}
                      </div>
                    );
                  }
                  const value = effectiveValue(key, processOverrides, modifications, processBaseline, catalogue) ?? '';
                  const revertTo = revertTarget(key, modifications, processBaseline, catalogue) ?? '';
                  const isUserEdited = key in processOverrides;
                  const isFileModified = !!modifications?.values && key in modifications.values;
                  return (
                    <ProcessOptionRow
                      key={key}
                      option={option}
                      value={value}
                      revertTo={revertTo}
                      isUserEdited={isUserEdited}
                      isFileModified={isFileModified}
                      showTooltipCaption={false}
                      isExpanded={expandedKey === key}
                      onToggleExpand={() =>
                        setExpandedKey((prev) => (prev === key ? null : key))
                      }
                      onCommit={(next) => setProcessOverride(key, next)}
                      onRevert={() => revertProcessOverride(key)}
                    />
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </Card>
  );
}
