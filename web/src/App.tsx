import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from '@/components/app-shell';
import DashboardRoute from '@/routes/dashboard';
import CameraRoute from '@/routes/camera';
import PrintRoute from '@/routes/print';
import JobsRoute from '@/routes/jobs';
import SettingsRoute from '@/routes/settings';
import { Toaster } from '@/components/ui/sonner';
import { PrinterProvider } from '@/lib/printer-context';
import { PrintProvider } from '@/lib/print-context';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 4_000,
      refetchOnWindowFocus: true,
    },
  },
});

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardRoute /> },
      { path: 'camera', element: <CameraRoute /> },
      { path: 'print', element: <PrintRoute /> },
      { path: 'jobs', element: <JobsRoute /> },
      { path: 'settings', element: <SettingsRoute /> },
    ],
  },
]);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <PrinterProvider>
        <PrintProvider>
          <RouterProvider router={router} />
          <Toaster />
        </PrintProvider>
      </PrinterProvider>
    </QueryClientProvider>
  );
}
