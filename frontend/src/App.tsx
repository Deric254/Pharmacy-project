import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { useAuthStore } from './auth/store'
import { useConfigStore } from './config/store'
import { setupApi } from './api/setup'
import { RequireAuth, RequirePermission } from './auth/guards'
import { AppShell } from './components/AppShell'
import { LoginPage } from './pages/LoginPage'
import { SetupPage } from './pages/SetupPage'
import { DashboardPage } from './pages/DashboardPage'
import { PosPage } from './pages/PosPage'
import { SalesPage } from './pages/SalesPage'
import { InventoryPage } from './pages/InventoryPage'
import { PurchasingPage } from './pages/PurchasingPage'
import { StockTakesPage } from './pages/StockTakesPage'
import { CustomersPage } from './pages/CustomersPage'
import { ReportsPage } from './pages/ReportsPage'
import { RolesPage } from './pages/RolesPage'
import { AuditLogPage } from './pages/AuditLogPage'
import { SettingsPage } from './pages/SettingsPage'
import { UsersPage } from './pages/UsersPage'
import { BackupsPage } from './pages/BackupsPage'
import { HelpPage } from './pages/HelpPage'
import { AiAssistantPage } from './pages/AiAssistantPage'

// Deliberately much shorter than the client's generous 30s default
// (meant for large Excel imports and AI calls). These two calls gate
// the very first thing anyone sees -- a real backend confirmed alive
// by Electron's own health check before this window was even shown
// should answer a same-machine loopback request in well under a
// second, not tens of seconds. Capping this short means a genuinely
// slow or stuck response resolves to a visible retry/login screen
// quickly, instead of leaving an indefinite blank screen that reads
// as "the app is broken" rather than "still starting."
const BOOTSTRAP_TIMEOUT_MS = 8000

type SetupCheck = 'checking' | 'setup_needed' | 'setup_done' | 'unreachable'

export function App() {
  const bootstrap = useAuthStore((s) => s.bootstrap)
  const loadConfig = useConfigStore((s) => s.load)
  const configStatus = useConfigStore((s) => s.status)
  const [setupCheck, setSetupCheck] = useState<SetupCheck>('checking')
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    let cancelled = false
    setSetupCheck('checking')
    void bootstrap()
    void loadConfig(BOOTSTRAP_TIMEOUT_MS)
    setupApi
      .status(BOOTSTRAP_TIMEOUT_MS)
      .then((s) => {
        if (!cancelled) setSetupCheck(s.needs_setup ? 'setup_needed' : 'setup_done')
      })
      // Never silently guess an answer here -- whether to show Setup
      // or Login is exactly the decision that must not be wrong. A
      // wrong guess doesn't just look bad, it actively breaks things:
      // guessing "no setup needed" when the server is actually just
      // unreachable shows a login screen for an account that may not
      // even exist yet, which fails again the moment it's used, for a
      // completely different, more confusing reason than the real
      // one. This state is shown honestly instead, with a real retry
      // that re-runs the same check rather than papering over it.
      .catch(() => {
        if (!cancelled) setSetupCheck('unreachable')
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootstrap, loadConfig, retryCount])

  // Branding (theme, name, logo) must be in place before first paint
  // of real content -- otherwise every business sees the same
  // hardcoded look for a flash, which is exactly what should never
  // happen again in this app. This is real, visible feedback, not a
  // bare empty div -- a genuinely slow or stuck backend response
  // used to render as pure blank nothing here, indistinguishable from
  // the app having failed to start at all.
  if (configStatus === 'loading' || setupCheck === 'checking') {
    return (
      <div className="grid min-h-screen place-items-center bg-paper">
        <div className="flex flex-col items-center gap-3 text-ink-soft">
          <span className="flex gap-1">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="inline-block h-2 w-2 animate-bounce rounded-full bg-brass"
                style={{ animationDelay: `${i * 0.15}s` }}
              />
            ))}
          </span>
          <span className="text-sm">Starting up…</span>
        </div>
      </div>
    )
  }

  if (setupCheck === 'unreachable') {
    return (
      <div className="grid min-h-screen place-items-center bg-paper px-4">
        <div className="w-full max-w-sm border border-rule bg-panel p-6 text-center">
          <p className="mb-1 font-display text-lg text-ink">Can&apos;t reach the server</p>
          <p className="mb-4 text-sm text-ink-soft">
            The app can&apos;t confirm it&apos;s connected right now. If this keeps happening,
            check <code className="text-xs">%LOCALAPPDATA%\PharmacyERP\logs\desktop.log</code>{' '}
            or restart the app.
          </p>
          <button
            onClick={() => setRetryCount((n) => n + 1)}
            className="w-full border border-rule px-4 py-2 text-sm text-ink hover:border-brass"
          >
            Try again
          </button>
        </div>
      </div>
    )
  }

  if (setupCheck === 'setup_needed') {
    return (
      <BrowserRouter>
        <SetupPage onComplete={() => setSetupCheck('setup_done')} />
      </BrowserRouter>
    )
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route
            path="pos"
            element={
              <RequirePermission code="sales.create">
                <PosPage />
              </RequirePermission>
            }
          />
          <Route
            path="sales"
            element={
              <RequirePermission code="sales.create">
                <SalesPage />
              </RequirePermission>
            }
          />
          <Route
            path="inventory"
            element={
              <RequirePermission code="inventory.view">
                <InventoryPage />
              </RequirePermission>
            }
          />
          <Route
            path="purchasing"
            element={
              <RequirePermission code="purchasing.create_po">
                <PurchasingPage />
              </RequirePermission>
            }
          />
          <Route
            path="stock-takes"
            element={
              <RequirePermission code="stocktake.perform">
                <StockTakesPage />
              </RequirePermission>
            }
          />
          <Route
            path="customers"
            element={
              <RequirePermission code="sales.create">
                <CustomersPage />
              </RequirePermission>
            }
          />
          <Route
            path="reports"
            element={
              <RequirePermission code="reports.view">
                <ReportsPage />
              </RequirePermission>
            }
          />
          <Route
            path="settings"
            element={
              <RequirePermission code="config.edit">
                <SettingsPage />
              </RequirePermission>
            }
          />
          <Route
            path="roles"
            element={
              <RequirePermission code="roles.manage">
                <RolesPage />
              </RequirePermission>
            }
          />
          <Route
            path="audit"
            element={
              <RequirePermission code="audit.view">
                <AuditLogPage />
              </RequirePermission>
            }
          />
          <Route
            path="users"
            element={
              <RequirePermission code="users.manage">
                <UsersPage />
              </RequirePermission>
            }
          />
          <Route
            path="backups"
            element={
              <RequirePermission code="backups.manage">
                <BackupsPage />
              </RequirePermission>
            }
          />
          <Route path="help" element={<HelpPage />} />
          <Route
            path="ai-assistant"
            element={
              <RequirePermission code="ai.use">
                <AiAssistantPage />
              </RequirePermission>
            }
          />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
