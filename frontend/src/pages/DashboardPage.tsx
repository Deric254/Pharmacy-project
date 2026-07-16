import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { inventoryApi } from '../api/domain'
import { useAuthStore } from '../auth/store'
import { useCurrencyFormatter } from '../lib/currency'
import type { ExpiringBatchOut, LowStockProductOut, StockValuationOut } from '../types/api'
import { ApiError } from '../api/client'

export function DashboardPage() {
  const user = useAuthStore((s) => s.user)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canSeeInventory = hasPermission('inventory.view')
  const formatCurrency = useCurrencyFormatter()

  const [lowStock, setLowStock] = useState<LowStockProductOut[] | null>(null)
  const [expiring, setExpiring] = useState<ExpiringBatchOut[] | null>(null)
  const [valuation, setValuation] = useState<StockValuationOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!canSeeInventory) return
    let cancelled = false
    Promise.all([inventoryApi.lowStock(), inventoryApi.expiring(30), inventoryApi.valuation()])
      .then(([low, exp, val]) => {
        if (cancelled) return
        setLowStock(low)
        setExpiring(exp)
        setValuation(val)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load dashboard data.')
      })
    return () => {
      cancelled = true
    }
  }, [canSeeInventory])

  return (
    <div className="p-6">
      <header className="mb-6">
        <p className="text-xs uppercase tracking-wide text-ink-soft">
          {new Date().toLocaleDateString(undefined, {
            weekday: 'long',
            year: 'numeric',
            month: 'long',
            day: 'numeric',
          })}
        </p>
        <h1 className="font-display text-2xl text-ink">Welcome back, {user?.full_name}</h1>
      </header>

      {error && (
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      {!canSeeInventory && (
        <p className="text-sm text-ink-soft">
          Your role doesn't include inventory visibility, so there's nothing to show here yet.
          Head to <Link to="/pos" className="underline">Point of Sale</Link> to ring up a sale.
        </p>
      )}

      {canSeeInventory && (
        <div className="grid gap-4 md:grid-cols-3">
          <div className="ledger-panel p-4">
            <h2 className="text-xs uppercase tracking-wide text-ink-soft">Stock value on hand</h2>
            <p className="figure mt-2 text-2xl text-ink">
              {valuation ? formatCurrency(valuation.total_value) : '…'}
            </p>
          </div>

          <div className="ledger-panel p-4">
            <h2 className="text-xs uppercase tracking-wide text-ink-soft">
              Low stock ({lowStock?.length ?? '…'})
            </h2>
            <ul className="mt-2 space-y-1">
              {lowStock?.slice(0, 5).map((item) => (
                <li key={item.product_id} className="ruled-row flex justify-between py-1 text-sm">
                  <span className="truncate">{item.name}</span>
                  <span className="figure text-stamp-red">{item.total_qty_available}</span>
                </li>
              ))}
              {lowStock?.length === 0 && (
                <li className="text-sm text-ink-soft">Nothing below reorder point.</li>
              )}
            </ul>
          </div>

          <div className="ledger-panel p-4">
            <h2 className="text-xs uppercase tracking-wide text-ink-soft">
              Expiring within 30 days ({expiring?.length ?? '…'})
            </h2>
            <ul className="mt-2 space-y-1">
              {expiring?.slice(0, 5).map((item) => (
                <li key={item.batch_id} className="ruled-row flex justify-between py-1 text-sm">
                  <span className="truncate">{item.product_name}</span>
                  <span className="figure text-stamp-red">{item.days_remaining}d</span>
                </li>
              ))}
              {expiring?.length === 0 && (
                <li className="text-sm text-ink-soft">Nothing expiring soon.</li>
              )}
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}
