import { NavLink, Outlet } from 'react-router-dom'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { useUpdateCheck } from '../lib/updateCheck'
import { Logo } from './Logo'

interface NavItem {
  to: string
  label: string
  permission: string | null
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Dashboard', permission: null },
  { to: '/pos', label: 'Point of Sale', permission: 'sales.create' },
  { to: '/inventory', label: 'Inventory', permission: 'inventory.view' },
  { to: '/purchasing', label: 'Purchasing', permission: 'purchasing.create_po' },
  { to: '/stock-takes', label: 'Stock Takes', permission: 'stocktake.perform' },
  { to: '/customers', label: 'Customers', permission: 'sales.create' },
  { to: '/reports', label: 'Reports', permission: 'reports.view' },
  { to: '/settings', label: 'Settings', permission: 'config.edit' },
  { to: '/roles', label: 'Roles & Permissions', permission: 'roles.manage' },
  { to: '/users', label: 'Staff Accounts', permission: 'users.manage' },
  { to: '/backups', label: 'Backups', permission: 'backups.manage' },
  { to: '/ai-assistant', label: 'AI Assistant', permission: 'ai.use' },
]

export function AppShell() {
  const user = useAuthStore((s) => s.user)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const logout = useAuthStore((s) => s.logout)
  const businessName = useConfigStore((s) => s.config?.business_name ?? 'Pharmacy System')
  const updateInfo = useUpdateCheck()

  const visibleItems = NAV_ITEMS.filter(
    (item) => item.permission === null || hasPermission(item.permission),
  )

  return (
    <div className="flex min-h-screen bg-paper text-ink">
      <aside className="flex w-56 shrink-0 flex-col border-r border-rule bg-panel">
        <div className="flex items-center gap-2 border-b border-rule px-4 py-4">
          <Logo className="h-7 w-7 shrink-0" />
          <span className="truncate font-display text-base">{businessName}</span>
        </div>
        <nav className="flex-1 space-y-0.5 p-2">
          {visibleItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `block px-3 py-2 text-sm ${
                  isActive
                    ? 'border-l-2 border-brass bg-paper font-medium text-ink'
                    : 'border-l-2 border-transparent text-ink-soft hover:bg-paper'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-rule p-3">
          <p className="truncate text-sm font-medium">{user?.full_name}</p>
          <p className="text-xs text-ink-soft">{user?.role_name}</p>
          <button
            onClick={() => void logout()}
            className="mt-2 text-xs text-stamp-red underline decoration-dotted underline-offset-2"
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        {updateInfo && <UpdateBanner info={updateInfo} />}
        <Outlet />
      </main>
    </div>
  )
}

function UpdateBanner({ info }: { info: ReturnType<typeof useUpdateCheck> }) {
  if (!info) return null
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-brass bg-brass-soft/30 px-4 py-2 text-sm">
      <span>
        A newer version is available:{' '}
        <span className="figure">
          {info.currentVersion} → {info.latestVersion}
        </span>
      </span>
      <a
        href={info.downloadUrl ?? info.releaseUrl}
        target="_blank"
        rel="noreferrer"
        className="border border-ink bg-ink px-3 py-1 text-paper"
      >
        {info.downloadUrl ? 'Download update' : 'View release'}
      </a>
    </div>
  )
}
