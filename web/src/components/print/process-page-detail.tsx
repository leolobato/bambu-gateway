import { ProcessOptionRow } from './process-option-row';
import { effectiveValue, revertTarget } from '@/lib/process/effective-value';
import type {
  ProcessOptionsCatalogue, ProcessPage, ProcessModifications,
} from '@/lib/process/types';

interface Props {
  page: ProcessPage;
  catalogue: ProcessOptionsCatalogue;
  modifications: ProcessModifications | null;
  processOverrides: Record<string, string>;
  processBaseline: Record<string, string>;
  expandedKey: string | null;
  onToggleExpand(k: string): void;
  onCommit(k: string, v: string): void;
  onRevert(k: string): void;
}

export function ProcessPageDetail(props: Props) {
  const {
    page, catalogue, modifications, processOverrides, processBaseline,
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
              const value =
                effectiveValue(key, processOverrides, modifications, processBaseline, catalogue) ?? '';
              const revertTo =
                revertTarget(key, modifications, processBaseline, catalogue) ?? '';
              return (
                <ProcessOptionRow
                  key={key}
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
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
