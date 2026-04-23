import { Button } from '@/components/ui/button';
import { Toaster } from '@/components/ui/sonner';
import { toast } from 'sonner';

export default function App() {
  return (
    <div className="min-h-dvh bg-bg-0 text-text-0 p-8 font-sans">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">
        Bambu Gateway
      </h1>
      <p className="text-text-1 text-sm mt-2">
        Foundation ready · <span className="text-accent">/beta</span>
      </p>

      <div className="mt-6 flex gap-3">
        <Button onClick={() => toast.success('Primary action fired')}>
          Primary
        </Button>
        <Button variant="secondary" onClick={() => toast('Secondary action')}>
          Secondary
        </Button>
        <Button variant="destructive" onClick={() => toast.error('Destructive action')}>
          Destructive
        </Button>
        <Button variant="ghost">Ghost</Button>
      </div>

      <Toaster />
    </div>
  );
}
