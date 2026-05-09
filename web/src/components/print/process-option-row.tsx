import { useState } from 'react';
import { ChevronRight, RotateCcw, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { displayValue } from '@/lib/process/effective-value';
import type { ProcessOption } from '@/lib/process/types';
import type { EditorProps } from './process-option-editor/types';
import { BoolEditor } from './process-option-editor/bool-editor';
import { NumberEditor } from './process-option-editor/number-editor';
import { EnumEditor } from './process-option-editor/enum-editor';
import { StringEditor } from './process-option-editor/string-editor';
import { SliderEditor } from './process-option-editor/slider-editor';
import { ColorEditor } from './process-option-editor/color-editor';
import { ReadonlyVectorEditor } from './process-option-editor/readonly-vector';

const READONLY_TYPES = new Set(['coPoint', 'coPoints', 'coPoint3', 'coBools', 'coNone']);

function pickEditor(option: ProcessOption): React.ComponentType<EditorProps> {
  if (READONLY_TYPES.has(option.type)) return ReadonlyVectorEditor;
  if (option.guiType === 'color') return ColorEditor;
  if (option.guiType === 'slider' && option.min !== null && option.max !== null) return SliderEditor;
  if (option.guiType === 'one_string') return StringEditor;
  switch (option.type) {
    case 'coBool': return BoolEditor;
    case 'coInt': case 'coInts':
    case 'coFloat': case 'coFloats':
    case 'coPercent': case 'coPercents':
    case 'coFloatOrPercent': case 'coFloatsOrPercents':
      return NumberEditor;
    case 'coEnum': return EnumEditor;
    case 'coString': case 'coStrings': return StringEditor;
    default: return ReadonlyVectorEditor;
  }
}

interface RowProps {
  option: ProcessOption;
  /** Effective value for display (resolver output). */
  value: string;
  /** What Revert restores to. */
  revertTo: string;
  /** True if the user has overridden this key. */
  isUserEdited: boolean;
  /** True if the file modified this key (independent of user edit). */
  isFileModified: boolean;
  /** Show tooltip caption under the label (All view only). */
  showTooltipCaption: boolean;
  isExpanded: boolean;
  onToggleExpand(): void;
  onCommit(next: string): void;
  onRevert(): void;
}

export function ProcessOptionRow(props: RowProps) {
  const {
    option, value, revertTo,
    isUserEdited, isFileModified, showTooltipCaption,
    isExpanded, onToggleExpand, onCommit, onRevert,
  } = props;

  const [valid, setValid] = useState(true);
  const [tooltipExpanded, setTooltipExpanded] = useState(false);

  const Editor = pickEditor(option);
  // Enum-aware label for the value summary and the revert footer; the
  // editor keeps receiving the raw `value` so commits round-trip exactly.
  const valueLabel = displayValue(value, option);
  const revertLabel = displayValue(revertTo, option);
  const dotClass = isUserEdited
    ? 'bg-orange-500'
    : isFileModified
      ? 'bg-sky-500'
      : '';
  const ariaDescription = isUserEdited
    ? 'edited by you'
    : isFileModified
      ? 'modified by file'
      : '';
  const isReadonly = READONLY_TYPES.has(option.type);

  return (
    <div className="border-b border-border/40 last:border-0">
      <button
        type="button"
        data-state={isExpanded ? 'open' : 'closed'}
        aria-expanded={isExpanded}
        aria-label={`${option.label}, ${valueLabel} ${option.sidetext}`}
        aria-description={ariaDescription || undefined}
        onClick={onToggleExpand}
        className={cn(
          'group flex w-full items-center gap-3 px-3 py-2.5 min-h-11 text-left',
          'hover:bg-accent/30 active:bg-accent/50',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          'transition-colors duration-fast',
        )}
      >
        <span
          aria-hidden="true"
          className={cn('flex w-3 items-center justify-center shrink-0')}
        >
          {dotClass && <span className={cn('size-2 rounded-full', dotClass)} />}
        </span>

        <span className="flex-1 min-w-0">
          <span className="block text-sm">{option.label}</span>
          {showTooltipCaption && option.tooltip && (
            <span className="block text-xs text-muted-foreground line-clamp-1">
              {option.tooltip}
            </span>
          )}
        </span>

        <span className="flex items-center gap-1 shrink-0">
          <span className="text-sm font-medium tabular-nums">{valueLabel}</span>
          {option.sidetext && (
            <span className="text-xs text-muted-foreground">{option.sidetext}</span>
          )}
          {!isReadonly && (
            <ChevronRight
              className={cn(
                'size-3 text-muted-foreground transition-transform duration-fast',
                isExpanded && 'rotate-90',
              )}
            />
          )}
        </span>
      </button>

      {/* CSS-grid expand trick — 0fr → 1fr animates row height. */}
      <div
        className={cn(
          'grid transition-[grid-template-rows] duration-base ease-standard motion-reduce:transition-none',
          isExpanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
        )}
      >
        <div className="overflow-hidden">
          {isExpanded && (
            <div className="px-3 py-3 pl-6 flex flex-col gap-2">
              {option.tooltip && (
                <p
                  className={cn(
                    'text-sm text-muted-foreground',
                    !tooltipExpanded && 'line-clamp-2',
                  )}
                >
                  {option.tooltip}
                </p>
              )}
              {option.tooltip && option.tooltip.length > 140 && (
                <Button
                  variant="link"
                  size="sm"
                  className="h-auto p-0 self-start"
                  onClick={() => setTooltipExpanded((v) => !v)}
                >
                  {tooltipExpanded ? 'Less' : 'More'}
                </Button>
              )}

              <Editor
                option={option}
                value={value}
                onCommit={onCommit}
                onValidityChange={setValid}
              />

              {(option.min !== null || option.max !== null) && (
                <p className="text-xs text-muted-foreground">
                  Range {option.min ?? '−∞'}–{option.max ?? '+∞'} {option.sidetext}
                </p>
              )}

              {!valid && (
                <p
                  role="alert"
                  className="flex items-center gap-1 text-xs text-destructive"
                >
                  <AlertTriangle className="size-3.5" />
                  Enter a valid {option.type.replace(/^co/, '').toLowerCase()} value
                </p>
              )}

              <div className="flex items-center justify-between pt-1">
                <p className="text-xs text-muted-foreground">
                  {isFileModified ? 'From file' : 'Default'}: <span className="tabular-nums">{revertLabel}</span> {option.sidetext}
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onRevert}
                  disabled={!isUserEdited}
                >
                  <RotateCcw className="size-3.5 mr-1" />
                  Revert
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
