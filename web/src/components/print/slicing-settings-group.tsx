import { Minus, Plus } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { SettingRow, type SettingOption } from '@/components/print/setting-row';
import { MachinePicker } from '@/components/print/machine-picker';

export interface SlicingSettings {
  machine: string;
  process: string;
  plateType: string;
  copies: number;
}

export function SlicingSettingsGroup({
  settings,
  onChange,
  machineOptions,
  processOptions,
  plateTypeOptions,
  activeMachineModel,
  disabled = false,
}: {
  settings: SlicingSettings;
  onChange: (next: SlicingSettings) => void;
  machineOptions: SettingOption[];
  processOptions: SettingOption[];
  plateTypeOptions: SettingOption[];
  /** machine_model of the currently-selected printer; pinned to top of the picker. */
  activeMachineModel: string | null;
  disabled?: boolean;
}) {
  function setCopies(raw: number) {
    const clamped = Math.min(100, Math.max(1, Math.round(raw)));
    onChange({ ...settings, copies: clamped });
  }

  return (
    <section className="flex flex-col gap-1">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-text-2 px-1">
        Slicing settings
      </div>
      <Card className="px-4 bg-card border-border">
        <MachinePicker
          label="Machine"
          value={settings.machine}
          options={machineOptions}
          activeMachineModel={activeMachineModel}
          onChange={(machine) => onChange({ ...settings, machine })}
          disabled={disabled}
        />
        <Separator className="bg-border" />
        <SettingRow
          label="Process"
          value={settings.process}
          options={processOptions}
          onChange={(process) => onChange({ ...settings, process })}
          disabled={disabled}
        />
        <Separator className="bg-border" />
        <SettingRow
          label="Plate type"
          value={settings.plateType}
          options={plateTypeOptions}
          onChange={(plateType) => onChange({ ...settings, plateType })}
          disabled={disabled}
        />
        <Separator className="bg-border" />
        <div className="flex items-center justify-between gap-4 py-3">
          <span className="text-sm text-text-0">Copies</span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="Decrease copies"
              disabled={disabled || settings.copies <= 1}
              onClick={() => setCopies(settings.copies - 1)}
              className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-transparent text-text-1 hover:bg-surface-1 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Minus className="h-3.5 w-3.5" />
            </button>
            <input
              type="number"
              inputMode="numeric"
              min={1}
              max={100}
              step={1}
              value={settings.copies}
              disabled={disabled}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10);
                if (!Number.isNaN(v)) setCopies(v);
              }}
              onBlur={(e) => {
                const v = parseInt(e.target.value, 10);
                setCopies(Number.isNaN(v) ? 1 : v);
              }}
              className="w-12 rounded-md border border-border bg-transparent text-center text-sm tabular-nums text-text-1 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-40 py-1"
              aria-label="Copies"
            />
            <button
              type="button"
              aria-label="Increase copies"
              disabled={disabled || settings.copies >= 100}
              onClick={() => setCopies(settings.copies + 1)}
              className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-transparent text-text-1 hover:bg-surface-1 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </Card>
    </section>
  );
}
