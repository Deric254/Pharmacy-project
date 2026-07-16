import { useEffect } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { useAuthStore } from './auth/store'
import { useConfigStore } from './config/store'
import { RequireAuth, RequirePermission } from './auth/guards'
import { AppShell } from './components/AppShell'
import { LoginPage } from './pages/LoginPage'
import { DashboardPage } from './pages/DashboardPage'
import { PosPage } from './pages/PosPage'
import { InventoryPage } from './pages/InventoryPage'
import { SettingsPage } from './pages/SettingsPage'
import { ComingSoonPage } from './pages/ComingSoonPage'

export function App() {
  const bootstrap = useAuthStore((s) => s.bootstrap)
  const loadConfig = useConfigStore((s) => s.load)
  const configStatus = useConfigStore((s) => s.status)

  useEffect(() => {
    void bootstrap()
    void loadConfig()
  }, [bootstrap, loadConfig])

  // Branding (theme, name, logo) must be in place before first paint
  // of real content -- otherwise every business sees the same
  // hardcoded look for a flash, which is exactly what should never
  // happen again in this app.
  if (configStatus === 'loading') {
    return <div className="min-h-screen bg-paper" />
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
                <ComingSoonPage title="Purchasing" />
              </RequirePermission>
            }
          />
          <Route
            path="stock-takes"
            element={
              <RequirePermission code="stocktake.perform">
                <ComingSoonPage title="Stock Takes" />
              </RequirePermission>
            }
          />
          <Route
            path="customers"
            element={
              <RequirePermission code="sales.create">
                <ComingSoonPage title="Customers" />
              </RequirePermission>
            }
          />
          <Route
            path="reports"
            element={
              <RequirePermission code="reports.view">
                <ComingSoonPage title="Reports" />
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
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
