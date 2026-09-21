import { Suspense, lazy, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { reportsApi, downloadReportExport } from '../api/reports'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { useCurrencyFormatter } from '../lib/currency'
import { businessToday, fallbackTimezone, subtractDays } from '../lib/businessDate'
import { useSaleCompletedRefresh } from '../lib/useSaleCompletedRefresh'
import { ApiError } from '../api/client'
import type {
  ExpiredStockReportOut,
  FastSlowMoversOut,
  ProductCoOccurrenceOut,
  ProfitByProductEntry,
  ProfitReportOut,
  ReceivingDiscrepancyReportOut,
  SalesSummaryOut,
  SeasonalTrendsOut,
  StockRunwayOut,
  StockTakeHistoryOut,
} from '../types/api'

const CoOccurrencePairsChart = lazy(() =>
  import('../components/charts/CoOccurrencePairsChart').then((m) => ({
    default: m.CoOccurrencePairsChart,
  })),
)
const SeasonalTrendsChart = lazy(() =>
  import('../components/charts/SeasonalTrendsChart').then((m) => ({
    default: m.SeasonalTrendsChart,
  })),
)

type Tab =
  | 'sales'
  | 'profit'
  | 'expired'
  | 'movers'
  | 'receiving'
  | 'stocktakes'
  | 'stockRunway'
  | 'coOccurrence'
  | 'seasonalTrends'

const TABS: { id: Tab; label: string; permission: string }[] = [
  { id: 'sales', label: 'Sales', permission: 'reports.view' },
  { id: 'profit', label: 'Profit', permission: 'reports.view_profit' },
  { id: 'expired', label: 'Expired Stock', permission: 'reports.view' },
  { id: 'movers', label: 'Fast/Slow Movers', permission: 'reports.view' },
  { id: 'receiving', label: 'Receiving Variance', permission: 'reports.view' },
  { id: 'stocktakes', label: 'Stock Take History', permission: 'reports.view' },
  { id: 'stockRunway', label: 'Stock Runway', permission: 'reports.view' },
  { id: 'coOccurrence', label: 'Frequently Bought Together', permission: 'reports.view' },
  { id: 'seasonalTrends', label: 'Seasonal Trends', permission: 'reports.view' },
]

// Falls back to the device's own timezone only if branding/config
// genuinely failed to load (see config/store.ts) -- matches that
// store's own established "never block the app" behavior, rather
// than introducing a second, different failure mode here.
function defaultDateRange(timezone: string) {
  const end = businessToday(timezone)
  return { start: subtractDays(end, 30), end }
}

export function ReportsPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const visibleTabs = TABS.filter((t) => hasPermission(t.permission))
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedTab = searchParams.get('tab') as Tab | null
  const initialTab =
    requestedTab && visibleTabs.some((t) => t.id === requestedTab)
      ? requestedTab
      : (visibleTabs[0]?.id ?? 'sales')
  const [tab, setTabState] = useState<Tab>(initialTab)

  function setTab(next: Tab) {
    setTabState(next)
    setSearchParams({ tab: next }, { replace: true })
  }

  return (
    <div className="p-6">
      <h1 className="mb-6 font-display text-2xl text-ink">Reports</h1>

      <div className="mb-6 flex flex-wrap gap-1 border-b border-rule">
        {visibleTabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-sm ${
              tab === t.id
                ? 'border-b-2 border-brass font-medium text-ink'
                : 'text-ink-soft hover:text-ink'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'sales' && <SalesReport />}
      {tab === 'profit' && <ProfitReport />}
      {tab === 'expired' && <ExpiredStockReport />}
      {tab === 'movers' && <MoversReport />}
      {tab === 'receiving' && <ReceivingReport />}
      {tab === 'stocktakes' && <StockTakeHistoryReport />}
      {tab === 'stockRunway' && <StockRunwayReport />}
      {tab === 'coOccurrence' && <CoOccurrenceReport />}
      {tab === 'seasonalTrends' && <SeasonalTrendsReport />}
    </div>
  )
}

function ExportButtons({
  path,
  query,
}: {
  path: string
  query: Record<string, string | number>
}) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handle(format: 'excel' | 'pdf') {
    setBusy(true)
    setError(null)
    try {
      await downloadReportExport(path, query, format)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Export failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mb-4 flex items-center gap-2">
      <button
        onClick={() => void handle('excel')}
        disabled={busy}
        className="border border-rule px-3 py-1 text-xs text-ink-soft hover:border-brass disabled:opacity-50"
      >
        Export Excel
      </button>
      <button
        onClick={() => void handle('pdf')}
        disabled={busy}
        className="border border-rule px-3 py-1 text-xs text-ink-soft hover:border-brass disabled:opacity-50"
      >
        Export PDF
      </button>
      {error && <span className="text-xs text-stamp-red">{error}</span>}
    </div>
  )
}

function DateRangeControls({
  start,
  end,
  onChange,
}: {
  start: string
  end: string
  onChange: (start: string, end: string) => void
}) {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
      <label className="flex items-center gap-1">
        From
        <input
          type="date"
          value={start}
          onChange={(e) => onChange(e.target.value, end)}
          className="border border-rule px-2 py-1"
        />
      </label>
      <label className="flex items-center gap-1">
        To
        <input
          type="date"
          value={end}
          onChange={(e) => onChange(start, e.target.value)}
          className="border border-rule px-2 py-1"
        />
      </label>
    </div>
  )
}

function SalesReport() {
  const formatCurrency = useCurrencyFormatter()
  const timezone = useConfigStore((s) => s.config?.timezone) ?? fallbackTimezone()
  const [{ start, end }, setRange] = useState(() => defaultDateRange(timezone))
  const [groupBy, setGroupBy] = useState<'day' | 'month'>('day')
  const [data, setData] = useState<SalesSummaryOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const salesVersion = useSaleCompletedRefresh(true)

  useEffect(() => {
    setError(null)
    reportsApi
      .salesSummary(start, end, groupBy)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load report.'))
  }, [start, end, groupBy, salesVersion])

  return (
    <div>
      <DateRangeControls start={start} end={end} onChange={(s, e) => setRange({ start: s, end: e })} />
      <div className="mb-4 flex items-center gap-2 text-sm">
        <span className="text-ink-soft">Group by</span>
        <select
          value={groupBy}
          onChange={(e) => setGroupBy(e.target.value as 'day' | 'month')}
          className="border border-rule px-2 py-1"
        >
          <option value="day">Day</option>
          <option value="month">Month</option>
        </select>
      </div>
      <ExportButtons path="/reports/sales" query={{ start_date: start, end_date: end, group_by: groupBy }} />

      {error && <p className="text-sm text-stamp-red">{error}</p>}
      {data && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-4">
            <Stat label="Total revenue" value={formatCurrency(data.total_revenue)} />
            <Stat label="Total sales" value={String(data.total_sale_count)} />
          </div>
          <table className="w-full border border-rule text-sm">
            <thead>
              <tr className="border-b border-rule bg-panel text-left">
                <th className="px-3 py-2">Period</th>
                <th className="px-3 py-2">Sales</th>
                <th className="px-3 py-2">Revenue</th>
                <th className="px-3 py-2">Discount</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map((row) => (
                <tr key={row.period} className="ruled-row">
                  <td className="px-3 py-2">{row.period}</td>
                  <td className="figure px-3 py-2">{row.sale_count}</td>
                  <td className="figure px-3 py-2">{formatCurrency(row.total_revenue)}</td>
                  <td className="figure px-3 py-2">{formatCurrency(row.total_discount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

function ProfitPdfExportButton({ start, end }: { start: string; end: string }) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handle() {
    setBusy(true)
    setError(null)
    try {
      await reportsApi.profitLossPdf(start, end)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Export failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mb-4 flex items-center gap-2">
      <button
        onClick={() => void handle()}
        disabled={busy}
        className="border border-rule px-3 py-1 text-xs text-ink-soft hover:border-brass disabled:opacity-50"
      >
        Export PDF
      </button>
      {error && <p className="text-sm text-stamp-red">{error}</p>}
    </div>
  )
}

function ProfitReport() {
  const formatCurrency = useCurrencyFormatter()
  const timezone = useConfigStore((s) => s.config?.timezone) ?? fallbackTimezone()
  const [{ start, end }, setRange] = useState(() => defaultDateRange(timezone))
  const [data, setData] = useState<ProfitReportOut | null>(null)
  const [byProduct, setByProduct] = useState<ProfitByProductEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const salesVersion = useSaleCompletedRefresh(true)

  useEffect(() => {
    setError(null)
    Promise.all([reportsApi.profit(start, end), reportsApi.profitByProduct(start, end)])
      .then(([totals, products]) => {
        setData(totals)
        setByProduct(products)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load report.'))
  }, [start, end, salesVersion])

  return (
    <div>
      <DateRangeControls start={start} end={end} onChange={(s, e) => setRange({ start: s, end: e })} />
      <ProfitPdfExportButton start={start} end={end} />
      {error && <p className="text-sm text-stamp-red">{error}</p>}
      {data && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Revenue" value={formatCurrency(data.total_revenue)} />
          <Stat label="Cost" value={formatCurrency(data.total_cost)} />
          <Stat label="Profit" value={formatCurrency(data.total_profit)} accent />
          <Stat label="Margin" value={`${data.profit_margin_percent.toFixed(1)}%`} accent />
        </div>
      )}
      {byProduct && (
        <table className="mt-6 w-full text-sm">
          <thead>
            <tr className="border-b border-rule text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-3 py-2">Product</th>
              <th className="px-3 py-2">Units</th>
              <th className="px-3 py-2">Revenue</th>
              <th className="px-3 py-2">Cost</th>
              <th className="px-3 py-2">Profit</th>
              <th className="px-3 py-2">Margin</th>
            </tr>
          </thead>
          <tbody>
            {byProduct.map((e) => (
              <tr key={e.product_id} className="ruled-row">
                <td className="px-3 py-2">{e.name}</td>
                <td className="figure px-3 py-2">{e.net_quantity_sold}</td>
                <td className="figure px-3 py-2">{formatCurrency(e.revenue)}</td>
                <td className="figure px-3 py-2">{formatCurrency(e.cost)}</td>
                <td className={`figure px-3 py-2 ${e.profit < 0 ? 'text-stamp-red' : ''}`}>
                  {formatCurrency(e.profit)}
                </td>
                <td className="figure px-3 py-2">
                  {e.profit_margin_percent !== null ? `${e.profit_margin_percent.toFixed(1)}%` : '—'}
                </td>
              </tr>
            ))}
            {byProduct.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-center text-ink-soft">
                  No sales in this period.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}

function ExpiredStockReport() {
  const formatCurrency = useCurrencyFormatter()
  const [data, setData] = useState<ExpiredStockReportOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    reportsApi
      .expiredStock()
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load report.'))
  }, [])

  return (
    <div>
      <ExportButtons path="/reports/expired-stock" query={{}} />
      {error && <p className="text-sm text-stamp-red">{error}</p>}
      {data && (
        <>
          <div className="mb-3 flex items-center justify-between">
            <Stat label="Total value at cost" value={formatCurrency(data.total_value)} accent />
          </div>
          {data.recommendation && (
            <p className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/30 p-2 text-sm text-stamp-red">
              {data.recommendation}
            </p>
          )}
          <table className="w-full border border-rule text-sm">
            <thead>
              <tr className="border-b border-rule bg-panel text-left">
                <th className="px-3 py-2">Product</th>
                <th className="px-3 py-2">Batch</th>
                <th className="px-3 py-2">Expired</th>
                <th className="px-3 py-2">Qty</th>
                <th className="px-3 py-2">Value</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map((e) => (
                <tr key={e.batch_id} className="ruled-row">
                  <td className="px-3 py-2">{e.product_name}</td>
                  <td className="px-3 py-2">{e.batch_number}</td>
                  <td className="figure px-3 py-2 text-stamp-red">{e.days_expired}d ago</td>
                  <td className="figure px-3 py-2">{e.qty_remaining}</td>
                  <td className="figure px-3 py-2">{formatCurrency(e.value_at_cost)}</td>
                </tr>
              ))}
              {data.entries.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-4 text-center text-ink-soft">
                    No expired stock.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

function MoversReport() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<FastSlowMoversOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const salesVersion = useSaleCompletedRefresh(true)

  useEffect(() => {
    setError(null)
    reportsApi
      .fastSlowMovers(days, 10)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load report.'))
  }, [days, salesVersion])

  return (
    <div>
      <label className="mb-4 flex items-center gap-2 text-sm">
        Over the last
        <input
          type="number"
          min={1}
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="figure w-16 border border-rule px-2 py-1"
        />
        days
      </label>
      {error && <p className="text-sm text-stamp-red">{error}</p>}
      {data && (
        <div className="grid gap-4 sm:grid-cols-3">
          <MoverList title="Fast movers" entries={data.fast_movers.map((m) => `${m.name} (${m.quantity_sold})`)} />
          <MoverList title="Slow movers" entries={data.slow_movers.map((m) => `${m.name} (${m.quantity_sold})`)} />
          <MoverList title="Never sold" entries={data.never_sold.map((m) => m.name)} />
        </div>
      )}
    </div>
  )
}

function MoverList({ title, entries }: { title: string; entries: string[] }) {
  return (
    <div className="ledger-panel p-3">
      <h3 className="mb-2 text-xs uppercase tracking-wide text-ink-soft">{title}</h3>
      <ul className="space-y-1 text-sm">
        {entries.map((e, i) => (
          <li key={i} className="ruled-row py-1">
            {e}
          </li>
        ))}
        {entries.length === 0 && <li className="text-ink-soft">None</li>}
      </ul>
    </div>
  )
}

function ReceivingReport() {
  const [data, setData] = useState<ReceivingDiscrepancyReportOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    reportsApi
      .receivingDiscrepancies()
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load report.'))
  }, [])

  return (
    <div>
      {error && <p className="text-sm text-stamp-red">{error}</p>}
      {data?.recommendation && (
        <p className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/30 p-2 text-sm text-stamp-red">
          {data.recommendation}
        </p>
      )}
      {data && (
        <table className="w-full border border-rule text-sm">
          <thead>
            <tr className="border-b border-rule bg-panel text-left">
              <th className="px-3 py-2">PO</th>
              <th className="px-3 py-2">Product</th>
              <th className="px-3 py-2">Ordered</th>
              <th className="px-3 py-2">Received</th>
              <th className="px-3 py-2">Variance</th>
            </tr>
          </thead>
          <tbody>
            {data.entries.map((e) => (
              <tr key={e.item_id} className="ruled-row">
                <td className="px-3 py-2">#{e.purchase_order_id}</td>
                <td className="px-3 py-2">{e.product_name}</td>
                <td className="figure px-3 py-2">{e.quantity_ordered}</td>
                <td className="figure px-3 py-2">{e.quantity_received}</td>
                <td className={`figure px-3 py-2 ${e.variance < 0 ? 'text-stamp-red' : 'text-stamp-green'}`}>
                  {e.variance > 0 ? '+' : ''}
                  {e.variance}
                </td>
              </tr>
            ))}
            {data.entries.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-center text-ink-soft">
                  No receiving discrepancies.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}

function StockTakeHistoryReport() {
  const formatCurrency = useCurrencyFormatter()
  const [data, setData] = useState<StockTakeHistoryOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    reportsApi
      .stockTakeHistory()
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load report.'))
  }, [])

  return (
    <div>
      {error && <p className="text-sm text-stamp-red">{error}</p>}
      {data && (
        <table className="w-full border border-rule text-sm">
          <thead>
            <tr className="border-b border-rule bg-panel text-left">
              <th className="px-3 py-2">Stock take</th>
              <th className="px-3 py-2">Started</th>
              <th className="px-3 py-2">Closed</th>
              <th className="px-3 py-2">Shrinkage value</th>
              <th className="px-3 py-2">Shrinkage %</th>
            </tr>
          </thead>
          <tbody>
            {data.entries.map((e) => (
              <tr key={e.stock_take_id} className="ruled-row">
                <td className="px-3 py-2">#{e.stock_take_id}</td>
                <td className="px-3 py-2">{new Date(e.started_at).toLocaleDateString()}</td>
                <td className="px-3 py-2">
                  {e.closed_at ? new Date(e.closed_at).toLocaleDateString() : '—'}
                </td>
                <td className="figure px-3 py-2 text-stamp-red">
                  {formatCurrency(e.shrinkage_value)}
                </td>
                <td className="figure px-3 py-2">{e.shrinkage_percent.toFixed(2)}%</td>
              </tr>
            ))}
            {data.entries.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-center text-ink-soft">
                  No closed stock takes yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="ledger-panel p-3">
      <p className="text-xs uppercase tracking-wide text-ink-soft">{label}</p>
      <p className={`figure mt-1 text-xl ${accent ? 'text-brass' : 'text-ink'}`}>{value}</p>
    </div>
  )
}

function StockRunwayReport() {
  const [lookbackDays, setLookbackDays] = useState(30)
  const [data, setData] = useState<StockRunwayOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const salesVersion = useSaleCompletedRefresh(true)

  useEffect(() => {
    setError(null)
    reportsApi
      .stockRunway(lookbackDays)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load report.'))
  }, [lookbackDays, salesVersion])

  return (
    <div>
      <label className="mb-4 flex items-center gap-2 text-sm">
        Over the last
        <input
          type="number"
          min={1}
          value={lookbackDays}
          onChange={(e) => setLookbackDays(Number(e.target.value))}
          className="figure w-16 border border-rule px-2 py-1"
        />
        days
      </label>
      {error && <p className="text-sm text-stamp-red">{error}</p>}
      {data && (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-rule text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-3 py-2">Product</th>
              <th className="px-3 py-2">On hand</th>
              <th className="px-3 py-2">Sold in window</th>
              <th className="px-3 py-2">Avg/day</th>
              <th className="px-3 py-2">Days left</th>
            </tr>
          </thead>
          <tbody>
            {data.entries.map((e) => (
              <tr key={e.product_id} className="ruled-row">
                <td className="px-3 py-2">{e.name}</td>
                <td className="figure px-3 py-2">{e.qty_on_hand}</td>
                <td className="figure px-3 py-2">{e.units_sold_in_window}</td>
                <td className="figure px-3 py-2">{e.avg_daily_sales.toFixed(1)}</td>
                <td
                  className={`figure px-3 py-2 ${
                    e.days_remaining !== null && e.days_remaining <= 7 ? 'text-stamp-red' : ''
                  }`}
                >
                  {e.days_remaining !== null ? e.days_remaining : '—'}
                </td>
              </tr>
            ))}
            {data.entries.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-center text-ink-soft">
                  No sales in this window yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}

function CoOccurrenceReport() {
  const [days, setDays] = useState(90)
  const [data, setData] = useState<ProductCoOccurrenceOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const salesVersion = useSaleCompletedRefresh(true)

  useEffect(() => {
    setError(null)
    reportsApi
      .coOccurrence(days, 50)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load report.'))
  }, [days, salesVersion])

  return (
    <div>
      <label className="mb-4 flex items-center gap-2 text-sm">
        Over the last
        <input
          type="number"
          min={1}
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="figure w-16 border border-rule px-2 py-1"
        />
        days
      </label>
      {error && <p className="text-sm text-stamp-red">{error}</p>}
      {data && (
        <Suspense fallback={<p className="text-sm text-ink-soft">Loading chart…</p>}>
          <CoOccurrencePairsChart data={data.pairs} />
        </Suspense>
      )}
    </div>
  )
}

function SeasonalTrendsReport() {
  const [days, setDays] = useState(730)
  const [data, setData] = useState<SeasonalTrendsOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const salesVersion = useSaleCompletedRefresh(true)

  useEffect(() => {
    setError(null)
    reportsApi
      .seasonalTrends(days)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load report.'))
  }, [days, salesVersion])

  return (
    <div>
      <label className="mb-4 flex items-center gap-2 text-sm">
        Over the last
        <input
          type="number"
          min={1}
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="figure w-16 border border-rule px-2 py-1"
        />
        days
      </label>
      {error && <p className="text-sm text-stamp-red">{error}</p>}
      {data && !data.has_sufficient_history && (
        <p className="mb-3 text-sm text-ink-soft">
          Not enough sales history yet for a real seasonal pattern -- check back after a full
          year of data.
        </p>
      )}
      {data && (
        <Suspense fallback={<p className="text-sm text-ink-soft">Loading chart…</p>}>
          <SeasonalTrendsChart data={data.entries} />
        </Suspense>
      )}
    </div>
  )
}
