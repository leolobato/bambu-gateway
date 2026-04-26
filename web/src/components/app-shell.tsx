import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { cn } from '@/lib/utils';

export function AppShell() {
  const location = useLocation();
  const onSettings = location.pathname === '/settings';

  return (
    <div className="min-h-dvh bg-bg-0 text-text-0 font-sans">
      <header className="flex items-center justify-between px-6 py-3.5 border-b border-border">
        {/* Brand */}
        <div className="flex items-center gap-2.5 text-[15px] font-bold text-white">
          <div className="w-2.5 h-2.5 rounded-[3px] bg-gradient-to-br from-accent to-accent-strong" />
          Bambu Gateway
        </div>

        {/* Tabs — dimmed on /settings so the Settings pill looks active. Tabs stay
            clickable/keyboard-reachable so users can navigate back without a second click. */}
        <nav
          className={cn(
            'flex gap-1 bg-bg-1 border border-border rounded-full p-[3px] transition-opacity duration-fast',
            onSettings && 'opacity-50',
          )}
        >
          <TabLink to="/">Dashboard</TabLink>
          <TabLink to="/print">Print</TabLink>
          <TabLink to="/jobs">Jobs</TabLink>
        </nav>

        {/* Settings */}
        <NavLink
          to="/settings"
          className={({ isActive }) =>
            cn(
              'flex items-center gap-1.5 px-3.5 py-1.5 rounded-full border border-border bg-bg-1 text-[13px] text-text-1 transition-colors duration-fast',
              !isActive && 'hover:text-white hover:bg-surface-1',
              isActive && 'text-white',
            )
          }
        >
          <span aria-hidden>⚙</span> Settings
        </NavLink>
      </header>

      <main className="max-w-[720px] mx-auto px-4 sm:px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}

function TabLink({ to, children }: { to: string; children: React.ReactNode }) {
  // `end` on the Dashboard tab (to === '/') so NavLink only marks it active at
  // exactly '/', not on every subroute (/, /print, /settings all start with '/').
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        cn(
          'px-4 py-1.5 rounded-full text-[13px] font-medium transition-colors duration-fast',
          isActive
            ? 'bg-surface-3 text-white font-semibold'
            : 'text-text-1 hover:text-white',
        )
      }
    >
      {children}
    </NavLink>
  );
}
