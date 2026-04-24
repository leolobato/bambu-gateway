import { PrintersSection } from '@/components/settings/printers-section';
import { PushSection } from '@/components/settings/push-section';
import { AboutSection } from '@/components/settings/about-section';

export default function SettingsRoute() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-[28px] font-extrabold tracking-tight text-white">Settings</h1>
      </header>
      <PrintersSection />
      <PushSection />
      <AboutSection />
    </div>
  );
}
