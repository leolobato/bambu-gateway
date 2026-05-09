import { useMemo, useState } from 'react';
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetClose,
} from '@/components/ui/sheet';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ChevronLeft, ChevronRight, Search, RotateCcw, X } from 'lucide-react';
import { ProcessOptionRow } from './process-option-row';
import {
  useProcessOptions,
  useProcessLayout,
} from '@/lib/api/process-options';
import { effectiveValue, revertTarget } from '@/lib/process/effective-value';
import { usePrintContext } from '@/lib/print-context';
import type {
  ProcessLayout, ProcessOptionsCatalogue, ProcessPage, ProcessModifications,
} from '@/lib/process/types';
import { cn } from '@/lib/utils';

interface Props {
  modifications: ProcessModifications | null;
}

export function ProcessAllSheet({ modifications }: Props) {
  const {
    processSheetOpen, setProcessSheetOpen,
    processOverrides, setProcessOverride, revertProcessOverride,
    resetAllProcessOverrides, processBaseline,
  } = usePrintContext();

  const optionsQuery = useProcessOptions();
  const layoutQuery = useProcessLayout();
  const isLoading = optionsQuery.isLoading || layoutQuery.isLoading;
  const loadError = optionsQuery.error || layoutQuery.error;
  const catalogue = optionsQuery.data ?? null;
  const layout = layoutQuery.data ?? null;

  const [selectedPage, setSelectedPage] = useState<ProcessPage | null>(null);
  const [search, setSearch] = useState('');
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  // Reset drill-down when the sheet closes so it always reopens to the page list.
  function handleOpenChange(open: boolean) {
    setProcessSheetOpen(open);
    if (!open) {
      setSelectedPage(null);
      setSearch('');
      setExpandedKey(null);
    }
  }

  return (
    <Sheet open={processSheetOpen} onOpenChange={handleOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-[640px] lg:max-w-[720px] flex flex-col p-0"
      >
        <SheetHeader className="px-4 py-3 border-b border-border/40 flex-row items-center gap-2 space-y-0">
          {selectedPage && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setSelectedPage(null)}
              aria-label="Back to all pages"
            >
              <ChevronLeft className="size-4" />
            </Button>
          )}
          <SheetTitle className="text-base font-semibold flex-1 truncate">
            {selectedPage
              ? `Process settings / ${selectedPage.label}`
              : 'Process settings'}
          </SheetTitle>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => {
              if (window.confirm('Reset all process settings?')) {
                resetAllProcessOverrides();
              }
            }}
            disabled={Object.keys(processOverrides).length === 0}
            aria-label="Reset all"
          >
            <RotateCcw className="size-4" />
          </Button>
          <SheetClose asChild>
            <Button variant="ghost" size="icon" aria-label="Close">
              <X className="size-4" />
            </Button>
          </SheetClose>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="flex flex-col gap-2 p-4">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : loadError ? (
            <Alert variant="destructive" className="m-4">
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
          ) : !catalogue || !layout ? null : selectedPage ? (
            // Placeholder until Task 11 lands `ProcessPageDetail`.
            <div className="p-4 text-sm text-muted-foreground">Detail view coming next</div>
          ) : (
            <PageList
              layout={layout}
              catalogue={catalogue}
              modifications={modifications}
              processOverrides={processOverrides}
              processBaseline={processBaseline}
              search={search}
              setSearch={setSearch}
              expandedKey={expandedKey}
              onToggleExpand={(k) => setExpandedKey((prev) => (prev === k ? null : k))}
              onCommit={setProcessOverride}
              onRevert={revertProcessOverride}
              onPickPage={setSelectedPage}
            />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

/* ------------------------------------------------------------------ */

interface PageListProps {
  layout: ProcessLayout;
  catalogue: ProcessOptionsCatalogue;
  modifications: ProcessModifications | null;
  processOverrides: Record<string, string>;
  processBaseline: Record<string, string>;
  search: string;
  setSearch(s: string): void;
  expandedKey: string | null;
  onToggleExpand(k: string): void;
  onCommit(k: string, v: string): void;
  onRevert(k: string): void;
  onPickPage(p: ProcessPage): void;
}

function PageList(props: PageListProps) {
  const {
    layout, catalogue, modifications, processOverrides, processBaseline,
    search, setSearch, expandedKey, onToggleExpand, onCommit, onRevert, onPickPage,
  } = props;

  const editedPerPage = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const page of layout.pages) {
      let n = 0;
      for (const group of page.optgroups)
        for (const k of group.options)
          if (k in processOverrides) n++;
      counts[page.label] = n;
    }
    return counts;
  }, [layout, processOverrides]);

  const optionCountPerPage = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const page of layout.pages) {
      counts[page.label] = page.optgroups.reduce((sum, g) => sum + g.options.length, 0);
    }
    return counts;
  }, [layout]);

  const searchResults = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return null;
    const matches: Array<{ key: string; pageLabel: string }> = [];
    for (const page of layout.pages) {
      for (const group of page.optgroups) {
        for (const key of group.options) {
          const opt = catalogue.options[key];
          if (!opt) continue;
          if (
            opt.label.toLowerCase().includes(q) ||
            key.toLowerCase().includes(q)
          ) {
            matches.push({ key, pageLabel: page.label });
          }
        }
      }
    }
    return matches;
  }, [search, layout, catalogue]);

  return (
    <>
      <div className="px-4 py-3 border-b border-border/40">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search settings"
            className="pl-9"
            aria-label="Search settings"
          />
        </div>
      </div>

      {searchResults ? (
        <div className="divide-y divide-border/40">
          {searchResults.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">
              No matches for &ldquo;{search}&rdquo;.
            </p>
          ) : (
            searchResults.map(({ key, pageLabel }) => {
              const opt = catalogue.options[key];
              if (!opt) return null;
              const value =
                effectiveValue(key, processOverrides, modifications, processBaseline, catalogue) ?? '';
              const revertTo =
                revertTarget(key, modifications, processBaseline, catalogue) ?? '';
              return (
                <div key={key} className="relative">
                  <ProcessOptionRow
                    option={opt}
                    value={value}
                    revertTo={revertTo}
                    isUserEdited={key in processOverrides}
                    isFileModified={!!modifications?.values && key in modifications.values}
                    showTooltipCaption
                    isExpanded={expandedKey === key}
                    onToggleExpand={() => onToggleExpand(key)}
                    onCommit={(v) => onCommit(key, v)}
                    onRevert={() => onRevert(key)}
                  />
                  <span className="pointer-events-none absolute right-10 top-3 text-xs text-muted-foreground">
                    {pageLabel}
                  </span>
                </div>
              );
            })
          )}
        </div>
      ) : (
        <div className="divide-y divide-border/40">
          {layout.pages.map((page) => {
            const edited = editedPerPage[page.label] ?? 0;
            const total = optionCountPerPage[page.label] ?? 0;
            return (
              <button
                key={page.label}
                type="button"
                className={cn(
                  'flex w-full items-center gap-3 px-4 py-3 min-h-12 text-left',
                  'hover:bg-accent/30 active:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                )}
                onClick={() => onPickPage(page)}
              >
                <span className="flex-1 text-sm">{page.label}</span>
                <span className="text-xs text-muted-foreground">
                  {total} options
                  {edited > 0 && (
                    <>
                      {' · '}
                      <span className="text-orange-500 font-semibold">{edited} edited</span>
                    </>
                  )}
                </span>
                <ChevronRight className="size-3 text-muted-foreground" />
              </button>
            );
          })}
        </div>
      )}
    </>
  );
}
