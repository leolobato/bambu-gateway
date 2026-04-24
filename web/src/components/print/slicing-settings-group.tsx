import { Card } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { SettingRow, type SettingOption } from '@/components/print/setting-row';
import { MachinePicker } from '@/components/print/machine-picker';

export interface SlicingSettings {
  machine: string;
  process: string;
  plateType: string;
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
      </Card>
    </section>
  );
}
