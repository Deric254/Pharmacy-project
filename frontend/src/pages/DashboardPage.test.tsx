import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DashboardPage } from './DashboardPage'
import { reportsApi } from '../api/reports'
import { inventoryApi } from '../api/domain'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { businessToday } from '../lib/businessDate'
import type {
  CategoryRevenueReportOut,
  KpiDashboardOut,
  TopProductEntry,
  UserOut,
} from '../types/api'
import type { BusinessConfigOut } from '../types/config'

const TIMEZONE = 'Africa/Nairobi'
const TODAY = businessToday(TIMEZONE)

vi.mock('../api/reports', () => ({
  reportsApi: {
    kpiDashboard: vi.fn(),
    revenueTrend: vi.fn(),
    topCustomers: vi.fn(),
    revenueByCategory: vi.fn(),
    topProductsInCategory: vi.fn(),
    revenuePotential: vi.fn(),
    salesByCashier: vi.fn(),
    stockRunway: vi.fn(),
    fastSlowMovers: vi.fn(),
    coOccurrence: vi.fn(),
    seasonalTrends: vi.fn(),
    expiredStock: vi.fn(),
  },
}))

vi.mock('../api/domain', () => ({
  inventoryApi: {
    lowStock: vi.fn(),
    expiring: vi.fn(),
    valuation: vi.fn(),
  },
}))

vi.mock('../lib/useSaleCompletedRefresh', () => ({
  useSaleCompletedRefresh: () => 0,
}))

// CategoryRevenueChart renders through recharts' ResponsiveContainer,
// which needs real layout (width/height) that jsdom doesn't provide --
// it would render zero bars in this environment regardless of data.
// Stubbing it out lets these tests verify what's actually
// DashboardPage's own responsibility: fetching the report, wiring the
// click-to-drill-down handler, and rendering the result -- not
// re-testing recharts' own rendering.
vi.mock('../components/charts/CategoryRevenueChart', () => ({
  CategoryRevenueChart: ({
    data,
    onSelectCategory,
  }: {
    data: CategoryRevenueReportOut['categories']
    onSelectCategory: (entry: CategoryRevenueReportOut['categories'][number]) => void
  }) => (
    <div>
      {data.map((entry) => (
        <button key={entry.category_name} onClick={() => onSelectCategory(entry)}>
          {entry.category_name}: {entry.revenue} ({entry.percent_of_total}%)
        </button>
      ))}
    </div>
  ),
}))

vi.mock('../components/charts/ProductRevenueChart', () => ({
  ProductRevenueChart: () => <div />,
}))
vi.mock('../components/charts/RevenueTrendChart', () => ({ RevenueTrendChart: () => <div /> }))
vi.mock('../components/charts/CustomerParetoChart', () => ({ CustomerParetoChart: () => <div /> }))

const OWNER_USER: UserOut = {
  id: 1,
  full_name: 'Lucy Kangai',
  username: 'lucy',
  role_name: 'ChemistOwner',
  permissions: ['reports.view', 'reports.view_profit', 'inventory.view'],
  is_active: true,
  must_change_password: false,
  terms_accepted: true,
}

const KPI: KpiDashboardOut = {
  start_date: TODAY,
  end_date: TODAY,
  revenue: 100,
  transaction_count: 5,
  average_basket: 20,
  revenue_change_percent: null,
  profit: 40,
  profit_margin_percent: 40,
  top_products: [],
  low_stock_count: 0,
  expiring_soon_count: 0,
}

const CATEGORY_REVENUE: CategoryRevenueReportOut = {
  start_date: TODAY,
  end_date: TODAY,
  total_revenue: 100,
  categories: [
    { category_id: 1, category_name: 'Antibiotics', quantity_sold: 5, revenue: 70, percent_of_total: 70 },
    { category_id: 2, category_name: 'Painkillers', quantity_sold: 3, revenue: 30, percent_of_total: 30 },
  ],
}

const ANTIBIOTIC_PRODUCTS: TopProductEntry[] = [
  { product_id: 1, name: 'Amoxicillin 500mg', quantity_sold: 5, revenue: 70 },
]

function seedStores() {
  useAuthStore.setState({ user: OWNER_USER, status: 'authenticated' })
  useConfigStore.setState({
    config: { timezone: TIMEZONE, currency: 'USD' } as BusinessConfigOut,
    status: 'ready',
  })
}

function mockEverythingHarmless() {
  vi.mocked(reportsApi.kpiDashboard).mockResolvedValue(KPI)
  vi.mocked(reportsApi.revenueTrend).mockResolvedValue({ granularity: 'day', points: [] })
  vi.mocked(reportsApi.topCustomers).mockResolvedValue({ entries: [], total_revenue: 0 })
  vi.mocked(reportsApi.revenueByCategory).mockResolvedValue(CATEGORY_REVENUE)
  vi.mocked(reportsApi.topProductsInCategory).mockResolvedValue([])
  vi.mocked(reportsApi.revenuePotential).mockResolvedValue({
    total_potential_revenue: 0,
    total_potential_cost: 0,
    total_potential_gross_profit: 0,
    overall_margin_percent: null,
    by_product: [],
    caveat: '',
  })
  vi.mocked(reportsApi.salesByCashier).mockResolvedValue({
    start_date: TODAY,
    end_date: TODAY,
    entries: [],
  })
  vi.mocked(reportsApi.stockRunway).mockResolvedValue({ lookback_days: 30, entries: [], caveat: '' })
  vi.mocked(reportsApi.fastSlowMovers).mockResolvedValue({
    period_days: 30,
    fast_movers: [],
    slow_movers: [],
    never_sold: [],
  })
  vi.mocked(reportsApi.coOccurrence).mockResolvedValue({ lookback_days: 90, pairs: [] })
  vi.mocked(reportsApi.seasonalTrends).mockResolvedValue({
    lookback_days: 730,
    entries: [],
    has_sufficient_history: false,
  })
  vi.mocked(reportsApi.expiredStock).mockResolvedValue({
    entries: [],
    total_value: 0,
    recommendation: '',
  })
  vi.mocked(inventoryApi.lowStock).mockResolvedValue([])
  vi.mocked(inventoryApi.expiring).mockResolvedValue([])
  vi.mocked(inventoryApi.valuation).mockResolvedValue({ total_value: 0, by_product: [] })
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  )
}

describe('DashboardPage revenue by category', () => {
  beforeEach(() => {
    seedStores()
    mockEverythingHarmless()
  })

  it('shows each category with its revenue and percent of total', async () => {
    renderDashboard()

    await screen.findByText(/Antibiotics: 70 \(70%\)/)
    expect(screen.getByText(/Painkillers: 30 \(30%\)/)).toBeInTheDocument()
  })

  it('clicking a category drills into its top products', async () => {
    vi.mocked(reportsApi.topProductsInCategory).mockResolvedValue(ANTIBIOTIC_PRODUCTS)
    const user = userEvent.setup()
    renderDashboard()

    await user.click(await screen.findByText(/Antibiotics: 70/))

    await screen.findByText('Top products in Antibiotics')
    expect(screen.getByText('Amoxicillin 500mg')).toBeInTheDocument()
    expect(reportsApi.topProductsInCategory).toHaveBeenCalledWith(
      TODAY,
      TODAY,
      { id: 1 },
      10,
    )
  })

  it('drilling into the uncategorised bucket passes uncategorised: true, not category_id', async () => {
    const uncategorisedReport: CategoryRevenueReportOut = {
      ...CATEGORY_REVENUE,
      categories: [
        { category_id: null, category_name: 'Uncategorised', quantity_sold: 1, revenue: 10, percent_of_total: 100 },
      ],
    }
    vi.mocked(reportsApi.revenueByCategory).mockResolvedValue(uncategorisedReport)
    vi.mocked(reportsApi.topProductsInCategory).mockResolvedValue([])
    const user = userEvent.setup()
    renderDashboard()

    await user.click(await screen.findByText(/Uncategorised: 10/))

    await waitFor(() =>
      expect(reportsApi.topProductsInCategory).toHaveBeenCalledWith(
        TODAY,
        TODAY,
        { uncategorised: true },
        10,
      ),
    )
  })

  it('clearing the drill-down hides the product list', async () => {
    vi.mocked(reportsApi.topProductsInCategory).mockResolvedValue(ANTIBIOTIC_PRODUCTS)
    const user = userEvent.setup()
    renderDashboard()

    await user.click(await screen.findByText(/Antibiotics: 70/))
    await screen.findByText('Amoxicillin 500mg')

    await user.click(screen.getByRole('button', { name: 'Clear' }))

    expect(screen.queryByText('Amoxicillin 500mg')).not.toBeInTheDocument()
  })

  it('shows an error if the drill-down request fails, without crashing the page', async () => {
    vi.mocked(reportsApi.topProductsInCategory).mockRejectedValue(new Error('network down'))
    const user = userEvent.setup()
    renderDashboard()

    await user.click(await screen.findByText(/Antibiotics: 70/))

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })
})
