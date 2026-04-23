import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from '@/components/app-shell';
import DashboardRoute from '@/routes/dashboard';
import PrintRoute from '@/routes/print';
import SettingsRoute from '@/routes/settings';
import { Toaster } from '@/components/ui/sonner';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 4_000,
      refetchOnWindowFocus: true,
    },
  },
});

const router = createBrowserRouter(
  [
    {
      path: '/',
      element: <AppShell />,
      children: [
        { index: true, element: <DashboardRoute /> },
        { path: 'print', element: <PrintRoute /> },
        { path: 'settings', element: <SettingsRoute /> },
      ],
    },
  ],
  { basename: '/beta' },
);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>
  );
}
