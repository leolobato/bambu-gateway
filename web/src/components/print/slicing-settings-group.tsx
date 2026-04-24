import { Card } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { SettingRow, type SettingOption } from '@/components/print/setting-row';

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
  disabled = false,
}: {
  settings: SlicingSettings;
  onChange: (next: SlicingSettings) => void;
  machineOptions: SettingOption[];
  processOptions: SettingOption[];
  plateTypeOptions: SettingOption[];
  disabled?: boolean;
}) {
  return (
    <section className="flex flex-col gap-1">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-text-2 px-1">
        Slicing settings
      </div>
      <Card className="px-4 bg-card border-border">
        <SettingRow
          label="Machine"
          value={settings.machine}
          options={machineOptions}
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
