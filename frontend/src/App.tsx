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
import { InventoryPage } from './pages/InventoryPage'
import { PurchasingPage } from './pages/PurchasingPage'
import { StockTakesPage } from './pages/StockTakesPage'
import { CustomersPage } from './pages/CustomersPage'
import { ReportsPage } from './pages/ReportsPage'
import { RolesPage } from './pages/RolesPage'
import { SettingsPage } from './pages/SettingsPage'
import { UsersPage } from './pages/UsersPage'
import { BackupsPage } from './pages/BackupsPage'
import { AiAssistantPage } from './pages/AiAssistantPage'

export function App() {
  const bootstrap = useAuthStore((s) => s.bootstrap)
  const loadConfig = useConfigStore((s) => s.load)
  const configStatus = useConfigStore((s) => s.status)
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null)

  useEffect(() => {
    void bootstrap()
    void loadConfig()
    setupApi
      .status()
      .then((s) => setNeedsSetup(s.needs_setup))
      // If this fails (offline, server not up yet), assume setup is
      // not needed rather than blocking the whole app on a check that
      // couldn't complete -- the normal login flow will surface the
      // real problem clearly enough on its own.
      .catch(() => setNeedsSetup(false))
  }, [bootstrap, loadConfig])

  // Branding (theme, name, logo) must be in place before first paint
  // of real content -- otherwise every business sees the same
  // hardcoded look for a flash, which is exactly what should never
  // happen again in this app.
  if (configStatus === 'loading' || needsSetup === null) {
    return <div className="min-h-screen bg-paper" />
  }

  if (needsSetup) {
    return (
      <BrowserRouter>
        <SetupPage onComplete={() => setNeedsSetup(false)} />
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
