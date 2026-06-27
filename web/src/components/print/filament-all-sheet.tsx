import { useMemo, useState } from 'react';
import {
  Sheet, SheetContent, SheetHeader, SheetTitle,
} from '@/components/ui/sheet';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader,
  AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { ChevronLeft, ChevronRight, Search, RotateCcw } from 'lucide-react';
import { ProcessOptionRow } from './process-option-row';
import {
  useFilamentOptions,
  useFilamentLayout,
  useFilamentBaseline,
} from '@/lib/api/filament-options';
import { effectiveValue, revertTarget } from '@/lib/process/effective-value';
import { usePrintContext } from '@/lib/print-context';
import type {
  ProcessLayout, ProcessOptionsCatalogue, ProcessPage,
} from '@/lib/process/types';
import { cn } from '@/lib/utils';

interface Props {
  /** Slicer position whose overrides this sheet edits. */
  slot: number;
  /** Selected filament setting id for the slot — drives the baseline. */
  settingId: string;
  /** Display label of the active slot, shown in the header. */
  label: string;
  open: boolean;
  onOpenChange(open: boolean): void;
}

export function FilamentAllSheet({ slot, settingId, label, open, onOpenChange }: Props) {
  const {
    filamentOverrides, setFilamentOverride, revertFilamentOverride,
    resetAllFilamentOverrides,
  } = usePrintContext();

  const optionsQuery = useFilamentOptions();
  const layoutQuery = useFilamentLayout();
  const baselineQuery = useFilamentBaseline(settingId || undefined);
  const isLoading = optionsQuery.isLoading || layoutQuery.isLoading;
  const loadError = optionsQuery.error || layoutQuery.error;
  const catalogue = optionsQuery.data ?? null;
  const layout = layoutQuery.data ?? null;
  const baseline = baselineQuery.data ?? {};
  const overrides = filamentOverrides[slot] ?? {};

  const [selectedPage, setSelectedPage] = useState<ProcessPage | null>(null);
  const [search, setSearch] = useState('');
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  // Reset drill-down when the sheet closes so it always reopens to the page list.
  function handleOpenChange(next: boolean) {
    onOpenChange(next);
    if (!next) {
      setSelectedPage(null);
      setSearch('');
      setExpandedKey(null);
    }
  }

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-[640px] lg:max-w-[720px] flex flex-col p-0"
      >
        <SheetHeader className="px-4 py-3 pr-10 border-b border-border/40 flex-row items-center gap-2 space-y-0">
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
              ? `Filament settings / ${label} / ${selectedPage.label}`
              : `Filament settings / ${label}`}
          </SheetTitle>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                disabled={Object.keys(overrides).length === 0}
                aria-label="Reset all"
              >
                <RotateCcw className="size-4" />
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Reset all filament settings?</AlertDialogTitle>
                <AlertDialogDescription>
                  Every filament override you've made for this 3MF will be cleared.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={resetAllFilamentOverrides}>Reset</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
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
          ) : !catalogue || !layout ? null : selectedPage ? (
            <FilamentPageDetail
              page={selectedPage}
              catalogue={catalogue}
              overrides={overrides}
              baseline={baseline}
              expandedKey={expandedKey}
              onToggleExpand={(k) => setExpandedKey((prev) => (prev === k ? null : k))}
              onCommit={(k, v) => setFilamentOverride(slot, k, v)}
              onRevert={(k) => revertFilamentOverride(slot, k)}
            />
          ) : (
            <PageList
              layout={layout}
              catalogue={catalogue}
              overrides={overrides}
              baseline={baseline}
              search={search}
              setSearch={setSearch}
              expandedKey={expandedKey}
              onToggleExpand={(k) => setExpandedKey((prev) => (prev === k ? null : k))}
              onCommit={(k, v) => setFilamentOverride(slot, k, v)}
              onRevert={(k) => revertFilamentOverride(slot, k)}
              onPickPage={setSelectedPage}
            />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

/* ------------------------------------------------------------------ */

interface PageDetailProps {
  page: ProcessPage;
  catalogue: ProcessOptionsCatalogue;
  overrides: Record<string, string>;
  baseline: Record<string, string>;
  expandedKey: string | null;
  onToggleExpand(k: string): void;
  onCommit(k: string, v: string): void;
  onRevert(k: string): void;
}

function FilamentPageDetail(props: PageDetailProps) {
  const {
    page, catalogue, overrides, baseline,
    expandedKey, onToggleExpand, onCommit, onRevert,
  } = props;

  return (
    <div className="px-4 py-3">
      {page.optgroups.map((group) => (
        <section key={group.label} className="mb-4">
          <h3 className="text-xs font-semibold tracking-wide uppercase text-muted-foreground pb-2 pt-4">
            {group.label}
          </h3>
          <div className="rounded-lg border border-border/40 overflow-hidden">
            {group.options.map((key) => {
              const opt = catalogue.options[key];
              if (!opt) return null;
              // v1 has no file-modified filament keys, so modifications is null.
              const value =
                effectiveValue(key, overrides, null, baseline, catalogue) ?? '';
              const revertTo =
                revertTarget(key, null, baseline, catalogue) ?? '';
              return (
                <ProcessOptionRow
                  key={key}
                  option={opt}
                  value={value}
                  revertTo={revertTo}
                  isUserEdited={key in overrides}
                  isFileModified={false}
                  showTooltipCaption
                  isExpanded={expandedKey === key}
                  onToggleExpand={() => onToggleExpand(key)}
                  onCommit={(v) => onCommit(key, v)}
                  onRevert={() => onRevert(key)}
                />
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */

interface PageListProps {
  layout: ProcessLayout;
  catalogue: ProcessOptionsCatalogue;
  overrides: Record<string, string>;
  baseline: Record<string, string>;
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
    layout, catalogue, overrides, baseline,
    search, setSearch, expandedKey, onToggleExpand, onCommit, onRevert, onPickPage,
  } = props;

  const editedPerPage = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const page of layout.pages) {
      let n = 0;
      for (const group of page.optgroups)
        for (const k of group.options)
          if (k in overrides) n++;
      counts[page.label] = n;
    }
    return counts;
  }, [layout, overrides]);

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
    // Grouped per page in layout order so the rendered list mirrors the
    // page-detail surface — section header + bordered row block per page.
    const groups: Array<{ pageLabel: string; keys: string[] }> = [];
    for (const page of layout.pages) {
      const keys: string[] = [];
      for (const group of page.optgroups) {
        for (const key of group.options) {
          const opt = catalogue.options[key];
          if (!opt) continue;
          if (
            opt.label.toLowerCase().includes(q) ||
            key.toLowerCase().includes(q)
          ) {
            keys.push(key);
          }
        }
      }
      if (keys.length > 0) groups.push({ pageLabel: page.label, keys });
    }
    return groups;
  }, [search, layout, catalogue]);

  const totalSearchMatches = searchResults?.reduce((sum, g) => sum + g.keys.length, 0) ?? 0;

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
        totalSearchMatches === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">
            No matches for &ldquo;{search}&rdquo;.
          </p>
        ) : (
          <div className="px-4 py-3">
            {searchResults.map(({ pageLabel, keys }) => (
              <section key={pageLabel} className="mb-4">
                <h3 className="text-xs font-semibold tracking-wide uppercase text-muted-foreground pb-2 pt-4">
                  {pageLabel}
                </h3>
                <div className="rounded-lg border border-border/40 overflow-hidden">
                  {keys.map((key) => {
                    const opt = catalogue.options[key];
                    if (!opt) return null;
                    const value =
                      effectiveValue(key, overrides, null, baseline, catalogue) ?? '';
                    const revertTo =
                      revertTarget(key, null, baseline, catalogue) ?? '';
                    return (
                      <ProcessOptionRow
                        key={key}
                        option={opt}
                        value={value}
                        revertTo={revertTo}
                        isUserEdited={key in overrides}
                        isFileModified={false}
                        showTooltipCaption
                        isExpanded={expandedKey === key}
                        onToggleExpand={() => onToggleExpand(key)}
                        onCommit={(v) => onCommit(key, v)}
                        onRevert={() => onRevert(key)}
                      />
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        )
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
