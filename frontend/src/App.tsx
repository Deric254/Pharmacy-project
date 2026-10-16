import { lazy, Suspense, useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { useAuthStore } from './auth/store'
import { useConfigStore } from './config/store'
import { setupApi } from './api/setup'
import { licenceApi, type LicenceState } from './api/licence'
import { ApiError, LICENCE_REQUIRED_EVENT } from './api/client'
import { RequireAuth, RequirePermission } from './auth/guards'
import { AppShell } from './components/AppShell'
import { LoginPage } from './pages/LoginPage'
import { SetupPage } from './pages/SetupPage'
import { LicencePage } from './pages/LicencePage'
import { LicenceRenewalNotice } from './components/LicenceRenewalNotice'

const DashboardPage = lazy(() =>
  import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage })),
)
const PosPage = lazy(() => import('./pages/PosPage').then((m) => ({ default: m.PosPage })))
const OwnerLicencesPage = lazy(() =>
  import('./pages/OwnerLicencesPage').then((m) => ({ default: m.OwnerLicencesPage })),
)
const SalesPage = lazy(() => import('./pages/SalesPage').then((m) => ({ default: m.SalesPage })))
const InventoryPage = lazy(() =>
  import('./pages/InventoryPage').then((m) => ({ default: m.InventoryPage })),
)
const StockMovementsPage = lazy(() =>
  import('./pages/StockMovementsPage').then((m) => ({ default: m.StockMovementsPage })),
)
const PurchasingPage = lazy(() =>
  import('./pages/PurchasingPage').then((m) => ({ default: m.PurchasingPage })),
)
const StockTakesPage = lazy(() =>
  import('./pages/StockTakesPage').then((m) => ({ default: m.StockTakesPage })),
)
const CustomersPage = lazy(() =>
  import('./pages/CustomersPage').then((m) => ({ default: m.CustomersPage })),
)
const ReportsPage = lazy(() =>
  import('./pages/ReportsPage').then((m) => ({ default: m.ReportsPage })),
)
const RolesPage = lazy(() => import('./pages/RolesPage').then((m) => ({ default: m.RolesPage })))
const AuditLogPage = lazy(() =>
  import('./pages/AuditLogPage').then((m) => ({ default: m.AuditLogPage })),
)
const SettingsPage = lazy(() =>
  import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })),
)
const UsersPage = lazy(() => import('./pages/UsersPage').then((m) => ({ default: m.UsersPage })))
const BackupsPage = lazy(() =>
  import('./pages/BackupsPage').then((m) => ({ default: m.BackupsPage })),
)
const HelpPage = lazy(() => import('./pages/HelpPage').then((m) => ({ default: m.HelpPage })))
const AiAssistantPage = lazy(() =>
  import('./pages/AiAssistantPage').then((m) => ({ default: m.AiAssistantPage })),
)

function PageLoadingFallback() {
  return (
    <div className="grid min-h-[50vh] place-items-center">
      <span className="flex gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="inline-block h-2 w-2 animate-bounce rounded-full bg-brass"
            style={{ animationDelay: `${i * 0.15}s` }}
          />
        ))}
      </span>
    </div>
  )
}

const BOOTSTRAP_TIMEOUT_MS = 8000

type SetupCheck = 'checking' | 'setup_needed' | 'setup_done' | 'unreachable'

export function App() {
  const bootstrap = useAuthStore((s) => s.bootstrap)
  const loadConfig = useConfigStore((s) => s.load)
  const configStatus = useConfigStore((s) => s.status)
  const [setupCheck, setSetupCheck] = useState<SetupCheck>('checking')
  const [retryCount, setRetryCount] = useState(0)
  const [licence, setLicence] = useState<LicenceState | null>(null)

  useEffect(() => {
    let cancelled = false
    setSetupCheck('checking')
    setLicence(null)
    licenceApi
      .state(BOOTSTRAP_TIMEOUT_MS)
      .then((s) => {
        if (!cancelled) setLicence(s)
      })
      .catch(() => {
        if (!cancelled) setSetupCheck('unreachable')
      })
    void bootstrap()
    void loadConfig(BOOTSTRAP_TIMEOUT_MS)
    setupApi
      .status(BOOTSTRAP_TIMEOUT_MS)
      .then((s) => {
        if (!cancelled) setSetupCheck(s.needs_setup ? 'setup_needed' : 'setup_done')
      })
      .catch((err: unknown) => {
        // A locked copy answers 402 here; the licence check above shows the activation screen.
        if (err instanceof ApiError && err.status === 402) return
        if (!cancelled) setSetupCheck('unreachable')
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootstrap, loadConfig, retryCount])

  // Any request the server refuses for want of a licence (e.g. it expired mid-session)
  // re-checks the state, which swaps the app for the activation screen.
  useEffect(() => {
    const recheck = () => {
      licenceApi
        .state()
        .then(setLicence)
        .catch(() => undefined)
    }
    window.addEventListener(LICENCE_REQUIRED_EVENT, recheck)
    return () => window.removeEventListener(LICENCE_REQUIRED_EVENT, recheck)
  }, [])

  if (licence !== null && !licence.ok) {
    return <LicencePage state={licence} onActivated={() => setRetryCount((n) => n + 1)} />
  }

  if (
    configStatus === 'loading' ||
    setupCheck === 'checking' ||
    (licence === null && setupCheck !== 'unreachable')
  ) {
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
    <>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            element={
              <RequireAuth>
                <Suspense fallback={<PageLoadingFallback />}>
                  <AppShell />
                </Suspense>
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
              path="inventory/movements"
              element={
                <RequirePermission code="inventory.adjust">
                  <StockMovementsPage />
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
              path="owner-licences"
              element={
                <RequirePermission code="roles.manage">
                  <OwnerLicencesPage />
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
      {licence && <LicenceRenewalNotice state={licence} onRenewed={setLicence} />}
    </>
  )
}
