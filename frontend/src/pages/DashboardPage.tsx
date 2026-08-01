import { useEffect, lazy, Suspense, useState } from 'react'
import { Link } from 'react-router-dom'
import { inventoryApi } from '../api/domain'
import { reportsApi } from '../api/reports'
import { useAuthStore } from '../auth/store'
import { useCurrencyFormatter } from '../lib/currency'
import { useViewedRangeStore } from '../lib/viewedRangeStore'
import type {
  ExpiringBatchOut,
  KpiDashboardOut,
  LowStockProductOut,
  RevenuePotentialOut,
  RevenueTrendOut,
  StockValuationOut,
  TopCustomerEntry,
} from '../types/api'
import { ApiError } from '../api/client'

const RevenueTrendChart = lazy(() =>
  import('../components/charts/RevenueTrendChart').then((m) => ({ default: m.RevenueTrendChart })),
)
const ProductRevenueChart = lazy(() =>
  import('../components/charts/ProductRevenueChart').then((m) => ({
    default: m.ProductRevenueChart,
  })),
)
const CustomerParetoChart = lazy(() =>
  import('../components/charts/CustomerParetoChart').then((m) => ({
    default: m.CustomerParetoChart,
  })),
)

type Preset = 'today' | 'week' | 'month' | 'custom'

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

function presetRange(preset: Preset): { start: string; end: string } {
  const today = new Date()
  const end = isoDate(today)
  if (preset === 'week') {
    const start = new Date(today)
    start.setDate(start.getDate() - 6)
    return { start: isoDate(start), end }
  }
  if (preset === 'month') {
    const start = new Date(today.getFullYear(), today.getMonth(), 1)
    return { start: isoDate(start), end }
  }
  return { start: end, end } // today
}

export function DashboardPage() {
  const user = useAuthStore((s) => s.user)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canSeeInventory = hasPermission('inventory.view')
  const canSeeReports = hasPermission('reports.view')
  const canSeeProfit = hasPermission('reports.view_profit')
  const formatCurrency = useCurrencyFormatter()

  const [preset, setPreset] = useState<Preset>('today')
  const [range, setRange] = useState(presetRange('today'))
  const setViewedRange = useViewedRangeStore((s) => s.setViewedRange)

  useEffect(() => {
    setViewedRange({ start: range.start, end: range.end })
    return () => setViewedRange(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range])
  const [kpi, setKpi] = useState<KpiDashboardOut | null>(null)
  const [lowStock, setLowStock] = useState<LowStockProductOut[] | null>(null)
  const [expiring, setExpiring] = useState<ExpiringBatchOut[] | null>(null)
  const [valuation, setValuation] = useState<StockValuationOut | null>(null)
  const [revenuePotential, setRevenuePotential] = useState<RevenuePotentialOut | null>(null)
  const [revenueTrend, setRevenueTrend] = useState<RevenueTrendOut | null>(null)
  const [topCustomers, setTopCustomers] = useState<TopCustomerEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  function applyPreset(next: Preset) {
    setPreset(next)
    if (next !== 'custom') setRange(presetRange(next))
  }

  useEffect(() => {
    if (!canSeeReports) return
    let cancelled = false
    reportsApi
      .kpiDashboard(range.start, range.end)
      .then((data) => {
        if (!cancelled) setKpi(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Could not load KPIs.')
      })
    return () => {
      cancelled = true
    }
  }, [canSeeReports, range])

  useEffect(() => {
    if (!canSeeReports) return
    let cancelled = false
    Promise.all([
      reportsApi.revenueTrend(range.start, range.end),
      reportsApi.topCustomers(range.start, range.end, 10),
    ])
      .then(([trend, customers]) => {
        if (cancelled) return
        setRevenueTrend(trend)
        setTopCustomers(customers.entries)
      })
      .catch(() => {
        // Charts are supplementary to the KPI figures above --
        // if they fail to load, the numbers still stand on their own.
      })
    return () => {
      cancelled = true
    }
  }, [canSeeReports, range])

  useEffect(() => {
    if (!canSeeProfit) return
    let cancelled = false
    reportsApi
      .revenuePotential()
      .then((data) => {
        if (!cancelled) setRevenuePotential(data)
      })
      .catch(() => {
        // Non-critical for the dashboard as a whole -- just omit this
        // one card rather than surface an error for a secondary figure.
      })
    return () => {
      cancelled = true
    }
  }, [canSeeProfit])

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
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load dashboard data.')
      })
    return () => {
      cancelled = true
    }
  }, [canSeeInventory])

  return (
    <div className="p-6">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-wide text-ink-soft">
            {new Date().toLocaleDateString(undefined, {
              weekday: 'long',
              year: 'numeric',
              month: 'long',
              day: 'numeric',
            })}
          </p>
          <h1 className="font-display text-2xl text-ink">Welcome back, {user?.full_name}</h1>
        </div>

        {canSeeReports && (
          <div className="flex items-center gap-2">
            {(['today', 'week', 'month'] as const).map((p) => (
              <button
                key={p}
                onClick={() => applyPreset(p)}
                className={`border px-3 py-1.5 text-xs uppercase tracking-wide ${
                  preset === p
                    ? 'border-ink bg-ink text-paper'
                    : 'border-rule text-ink-soft hover:border-brass'
                }`}
              >
                {p === 'today' ? 'Today' : p === 'week' ? 'Last 7 days' : 'This month'}
              </button>
            ))}
            <input
              type="date"
              value={range.start}
              onChange={(e) => {
                setPreset('custom')
                setRange((r) => ({ ...r, start: e.target.value }))
              }}
              className="border border-rule bg-paper px-2 py-1.5 text-xs"
            />
            <span className="text-xs text-ink-soft">to</span>
            <input
              type="date"
              value={range.end}
              onChange={(e) => {
                setPreset('custom')
                setRange((r) => ({ ...r, end: e.target.value }))
              }}
              className="border border-rule bg-paper px-2 py-1.5 text-xs"
            />
          </div>
        )}
      </header>

      {error && (
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      {!canSeeInventory && !canSeeReports && (
        <p className="text-sm text-ink-soft">
          Your role doesn't include reporting visibility, so there's nothing to show here yet.
          Head to <Link to="/pos" className="underline">Point of Sale</Link> to ring up a sale.
        </p>
      )}

      {canSeeReports && (
        <div className="mb-6 grid gap-4 md:grid-cols-4">
          <div className="ledger-panel p-4">
            <h2 className="text-xs uppercase tracking-wide text-ink-soft">Revenue</h2>
            <p className="figure mt-2 text-2xl text-ink">
              {kpi ? formatCurrency(kpi.revenue) : '…'}
            </p>
            {kpi?.revenue_change_percent !== null && kpi?.revenue_change_percent !== undefined && (
              <p
                className={`figure mt-1 text-xs ${
                  kpi.revenue_change_percent >= 0 ? 'text-stamp-green' : 'text-stamp-red'
                }`}
              >
                {kpi.revenue_change_percent >= 0 ? '▲' : '▼'}{' '}
                {Math.abs(kpi.revenue_change_percent).toFixed(1)}% vs prior period
              </p>
            )}
          </div>

          <div className="ledger-panel p-4">
            <h2 className="text-xs uppercase tracking-wide text-ink-soft">Transactions</h2>
            <p className="figure mt-2 text-2xl text-ink">{kpi ? kpi.transaction_count : '…'}</p>
            <p className="figure mt-1 text-xs text-ink-soft">
              avg basket {kpi ? formatCurrency(kpi.average_basket) : '…'}
            </p>
          </div>

          <div className="ledger-panel p-4">
            <h2 className="text-xs uppercase tracking-wide text-ink-soft">Profit</h2>
            <p className="figure mt-2 text-2xl text-ink">
              {kpi?.profit !== null && kpi?.profit !== undefined
                ? formatCurrency(kpi.profit)
                : '—'}
            </p>
            <p className="figure mt-1 text-xs text-ink-soft">
              {kpi?.profit_margin_percent !== null && kpi?.profit_margin_percent !== undefined
                ? `${kpi.profit_margin_percent.toFixed(1)}% margin`
                : 'Not visible to your role'}
            </p>
          </div>

          <div className="ledger-panel p-4">
            <h2 className="text-xs uppercase tracking-wide text-ink-soft">Needs attention</h2>
            <p className="figure mt-2 text-2xl text-ink">
              {kpi ? kpi.low_stock_count + kpi.expiring_soon_count : '…'}
            </p>
            <p className="text-xs text-ink-soft">
              {kpi?.low_stock_count ?? '…'} low stock · {kpi?.expiring_soon_count ?? '…'} expiring
            </p>
          </div>
        </div>
      )}

      {canSeeReports && revenueTrend && (
        <div className="mb-6 ledger-panel p-4">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-ink-soft">Revenue trend</h2>
          <Suspense fallback={<p className="text-sm text-ink-soft">Loading chart…</p>}>
            <RevenueTrendChart data={revenueTrend} />
          </Suspense>
        </div>
      )}

      {canSeeReports && kpi && kpi.top_products.length > 0 && (
        <div className="mb-6 ledger-panel p-4">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-ink-soft">
            Revenue by product
          </h2>
          <Suspense fallback={<p className="text-sm text-ink-soft">Loading chart…</p>}>
            <ProductRevenueChart data={kpi.top_products} />
          </Suspense>
        </div>
      )}

      {canSeeReports && topCustomers && topCustomers.length > 0 && (
        <div className="mb-6 ledger-panel p-4">
          <h2 className="mb-1 text-xs uppercase tracking-wide text-ink-soft">
            Customer revenue (Pareto)
          </h2>
          <p className="mb-2 text-xs text-ink-soft">
            Bars show each customer's revenue; the red line is the running share of total
            revenue, so you can see at a glance how much comes from how few customers.
          </p>
          <Suspense fallback={<p className="text-sm text-ink-soft">Loading chart…</p>}>
            <CustomerParetoChart data={topCustomers} />
          </Suspense>
        </div>
      )}

      {canSeeReports && kpi && kpi.top_products.length > 0 && (
        <div className="mb-6 ledger-panel p-4">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-ink-soft">
            Top products in this period
          </h2>
          <ul className="divide-y divide-rule">
            {kpi.top_products.map((p) => (
              <li key={p.product_id} className="flex justify-between py-1.5 text-sm">
                <span className="truncate pr-2">{p.name}</span>
                <span className="figure text-ink-soft">
                  {p.quantity_sold} sold · {formatCurrency(p.revenue)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {canSeeProfit && revenuePotential && revenuePotential.by_product.length > 0 && (
        <div className="mb-6 ledger-panel p-4">
          <h2 className="text-xs uppercase tracking-wide text-ink-soft">
            If everything in stock sold today
          </h2>
          <div className="mt-2 grid grid-cols-3 gap-4">
            <div>
              <p className="text-xs text-ink-soft">Revenue</p>
              <p className="figure text-xl text-ink">
                {formatCurrency(revenuePotential.total_potential_revenue)}
              </p>
            </div>
            <div>
              <p className="text-xs text-ink-soft">Gross profit</p>
              <p className="figure text-xl text-ink">
                {formatCurrency(revenuePotential.total_potential_gross_profit)}
              </p>
            </div>
            <div>
              <p className="text-xs text-ink-soft">Margin</p>
              <p className="figure text-xl text-ink">
                {revenuePotential.overall_margin_percent !== null
                  ? `${revenuePotential.overall_margin_percent.toFixed(1)}%`
                  : '—'}
              </p>
            </div>
          </div>
          <p className="mt-3 text-xs italic text-ink-soft">{revenuePotential.caveat}</p>
        </div>
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
