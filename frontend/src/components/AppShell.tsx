import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { FloatingAiWidget } from './FloatingAiWidget'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { useUpdateCheck, type UpdateInfo } from '../lib/updateCheck'
import { formatRoleName } from '../lib/roleDisplay'
import { Logo } from './Logo'
import { Marquee } from './Marquee'

interface NavItem {
  to: string
  label: string
  permission: string | null
}

interface NavSection {
  label: string | null
  items: NavItem[]
}

const NAV_SECTIONS: NavSection[] = [
  {
    label: null, // Dashboard sits alone, above any section label
    items: [{ to: '/', label: 'Dashboard', permission: null }],
  },
  {
    label: 'Operations',
    items: [
      { to: '/pos', label: 'Point of Sale', permission: 'sales.create' },
      { to: '/sales', label: 'Sales', permission: 'sales.create' },
      { to: '/inventory', label: 'Inventory', permission: 'inventory.view' },
      { to: '/purchasing', label: 'Purchasing', permission: 'purchasing.create_po' },
      { to: '/stock-takes', label: 'Stock Takes', permission: 'stocktake.perform' },
      { to: '/customers', label: 'Customers', permission: 'sales.create' },
      { to: '/reports', label: 'Reports', permission: 'reports.view' },
      { to: '/ai-assistant', label: 'AI Settings', permission: 'ai.use' },
    ],
  },
  {
    label: 'Administration',
    items: [
      { to: '/settings', label: 'Settings', permission: 'config.edit' },
      { to: '/roles', label: 'Roles & Permissions', permission: 'roles.manage' },
      { to: '/users', label: 'Staff Accounts', permission: 'users.manage' },
      { to: '/audit', label: 'Audit Trail', permission: 'audit.view' },
      { to: '/backups', label: 'Backups', permission: 'backups.manage' },
      { to: '/help', label: 'Help', permission: null },
    ],
  },
]

export function AppShell() {
  const user = useAuthStore((s) => s.user)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const logout = useAuthStore((s) => s.logout)
  const businessName = useConfigStore((s) => s.config?.business_name ?? 'Pharmacy System')
  const slogan = useConfigStore((s) => s.config?.slogan ?? '')
  const { info: updateInfo } = useUpdateCheck()
  const location = useLocation()
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({})

  const visibleSections = NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter(
      (item) => item.permission === null || hasPermission(item.permission),
    ),
  })).filter((section) => section.items.length > 0)

  // Reveal the section containing the current page once, on
  // navigation -- not as a standing override. Otherwise, collapsing
  // the section you're currently in would silently do nothing, since
  // it would immediately be forced back open on the very next render.
  useEffect(() => {
    const activeSection = visibleSections.find((section) =>
      section.items.some(
        (item) =>
          location.pathname === item.to ||
          (item.to !== '/' && location.pathname.startsWith(`${item.to}/`)),
      ),
    )
    if (!activeSection?.label) return
    setCollapsedSections((prev) =>
      prev[activeSection.label as string] ? { ...prev, [activeSection.label as string]: false } : prev,
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname])

  return (
    <div className="flex h-screen bg-paper text-ink">
      <aside className="flex w-56 shrink-0 flex-col border-r border-rule bg-panel">
        <div className="border-b border-rule bg-panel px-4 py-3 text-ink">
          <div className="flex items-center gap-2">
            <Logo className="h-7 w-7 shrink-0" />
            <span className="truncate font-display text-base">{businessName}</span>
          </div>
          {slogan && <Marquee text={slogan} />}
        </div>
        <nav className="flex-1 space-y-3 overflow-y-auto p-2">
          {visibleSections.map((section) => {
            const sectionKey = section.label ?? 'root'
            const isCollapsed = Boolean(collapsedSections[sectionKey])

            return (
              <div key={sectionKey}>
                {section.label && (
                  <button
                    onClick={() =>
                      setCollapsedSections((prev) => ({
                        ...prev,
                        [sectionKey]: !prev[sectionKey],
                      }))
                    }
                    className="flex w-full items-center justify-between px-3 pb-1 text-xs font-bold uppercase tracking-wide text-ink hover:text-brass"
                  >
                    <span>{section.label}</span>
                    <span
                      className={`text-ink-soft transition-transform ${isCollapsed ? '-rotate-90' : ''}`}
                    >
                      ▾
                    </span>
                  </button>
                )}
                {!isCollapsed && (
                  <div className="space-y-0.5">
                    {section.items.map((item) => (
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
                  </div>
                )}
              </div>
            )
          })}
        </nav>
        <div className="border-t border-rule p-3">
          <p className="truncate text-sm font-medium">{user?.full_name}</p>
          <p className="text-xs text-ink-soft">
            {user?.role_name && formatRoleName(user.role_name)}
          </p>
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
      {hasPermission('ai.use') && <FloatingAiWidget />}
    </div>
  )
}

function UpdateBanner({ info }: { info: UpdateInfo }) {
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
