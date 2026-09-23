import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ReportsPage } from './ReportsPage'
import { reportsApi } from '../api/reports'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { formatMoney } from '../lib/currency'
import type { ProfitByProductEntry, ProfitReportOut, StockTakeHistoryOut, UserOut } from '../types/api'
import type { BusinessConfigOut } from '../types/config'

vi.mock('../api/reports', () => ({
  reportsApi: {
    profit: vi.fn(),
    profitByProduct: vi.fn(),
    stockTakeHistory: vi.fn(),
  },
  downloadReportExport: vi.fn(),
}))

vi.mock('../lib/useSaleCompletedRefresh', () => ({
  useSaleCompletedRefresh: () => 0,
}))

const OWNER_USER: UserOut = {
  id: 1,
  full_name: 'Lucy Kangai',
  username: 'lucy',
  role_name: 'ChemistOwner',
  permissions: ['reports.view', 'reports.view_profit'],
  is_active: true,
  must_change_password: false,
  terms_accepted: true,
}

const TOTALS: ProfitReportOut = {
  start_date: '2026-08-22',
  end_date: '2026-09-21',
  total_revenue: 38348,
  total_cost: 32566,
  total_profit: 5782,
  profit_margin_percent: 15.1,
}

const BREAKDOWN: ProfitByProductEntry[] = [
  {
    product_id: 1,
    name: 'Amoxicillin 500mg',
    net_quantity_sold: 3,
    revenue: 30,
    cost: 12,
    profit: 18,
    profit_margin_percent: 60,
  },
  {
    product_id: 2,
    name: 'Damaged Syrup',
    net_quantity_sold: 0,
    revenue: 0,
    cost: 8,
    profit: -8,
    profit_margin_percent: null,
  },
]

const money = (value: number) => formatMoney(value, 'KES').replace(/\s+/g, ' ')

function renderProfitTab() {
  return render(
    <MemoryRouter initialEntries={['/reports?tab=profit']}>
      <ReportsPage />
    </MemoryRouter>,
  )
}

function renderStockTakesTab() {
  return render(
    <MemoryRouter initialEntries={['/reports?tab=stocktakes']}>
      <ReportsPage />
    </MemoryRouter>,
  )
}

describe('ReportsPage profit tab', () => {
  beforeEach(() => {
    vi.mocked(reportsApi.profit).mockReset()
    vi.mocked(reportsApi.profitByProduct).mockReset()
    useAuthStore.setState({ user: OWNER_USER, status: 'authenticated' })
    useConfigStore.setState({
      config: { timezone: 'Africa/Nairobi', currency: 'KES' } as BusinessConfigOut,
      status: 'ready',
    })
  })

  it('keeps the four totals and adds one row per product with revenue, cost, profit and margin', async () => {
    vi.mocked(reportsApi.profit).mockResolvedValue(TOTALS)
    vi.mocked(reportsApi.profitByProduct).mockResolvedValue(BREAKDOWN)
    renderProfitTab()

    expect(await screen.findByText(money(38348))).toBeInTheDocument()
    expect(screen.getByText(money(32566))).toBeInTheDocument()
    expect(screen.getByText(money(5782))).toBeInTheDocument()
    expect(screen.getByText('15.1%')).toBeInTheDocument()

    const good = (await screen.findByText('Amoxicillin 500mg')).closest('tr')!
    expect(within(good).getByText('3')).toBeInTheDocument()
    expect(within(good).getByText(money(30))).toBeInTheDocument()
    expect(within(good).getByText(money(12))).toBeInTheDocument()
    expect(within(good).getByText(money(18))).toBeInTheDocument()
    expect(within(good).getByText('60.0%')).toBeInTheDocument()
    expect(within(good).getByText(money(18))).not.toHaveClass('text-stamp-red')
  })

  it('shows a loss in red and a dash where there is no revenue to take a margin of', async () => {
    vi.mocked(reportsApi.profit).mockResolvedValue(TOTALS)
    vi.mocked(reportsApi.profitByProduct).mockResolvedValue(BREAKDOWN)
    renderProfitTab()

    const loss = (await screen.findByText('Damaged Syrup')).closest('tr')!
    expect(within(loss).getByText(money(-8))).toHaveClass('text-stamp-red')
    expect(within(loss).getByText('—')).toBeInTheDocument()
  })

  it('asks for the totals and the breakdown over the same date range', async () => {
    vi.mocked(reportsApi.profit).mockResolvedValue(TOTALS)
    vi.mocked(reportsApi.profitByProduct).mockResolvedValue(BREAKDOWN)
    renderProfitTab()

    await screen.findByText('Amoxicillin 500mg')
    expect(reportsApi.profit).toHaveBeenCalledTimes(1)
    expect(reportsApi.profitByProduct).toHaveBeenCalledTimes(1)
    expect(vi.mocked(reportsApi.profitByProduct).mock.calls[0]).toEqual(
      vi.mocked(reportsApi.profit).mock.calls[0],
    )
  })

  it('says so when nothing was sold in the period', async () => {
    vi.mocked(reportsApi.profit).mockResolvedValue({
      ...TOTALS,
      total_revenue: 0,
      total_cost: 0,
      total_profit: 0,
      profit_margin_percent: 0,
    })
    vi.mocked(reportsApi.profitByProduct).mockResolvedValue([])
    renderProfitTab()

    expect(await screen.findByText('No sales in this period.')).toBeInTheDocument()
  })

  it('shows an error instead of a half-loaded page if the breakdown cannot be loaded', async () => {
    vi.mocked(reportsApi.profit).mockResolvedValue(TOTALS)
    vi.mocked(reportsApi.profitByProduct).mockRejectedValue(new Error('boom'))
    renderProfitTab()

    expect(await screen.findByText('Could not load report.')).toBeInTheDocument()
    expect(screen.queryByText(money(38348))).not.toBeInTheDocument()
  })
})

describe('ReportsPage stock take history tab', () => {
  beforeEach(() => {
    vi.mocked(reportsApi.stockTakeHistory).mockReset()
    useAuthStore.setState({ user: OWNER_USER, status: 'authenticated' })
    useConfigStore.setState({
      config: { timezone: 'Africa/Nairobi', currency: 'KES' } as BusinessConfigOut,
      status: 'ready',
    })
  })

  it('shows shrinkage and excess side by side, not shrinkage only', async () => {
    const history: StockTakeHistoryOut = {
      entries: [
        {
          stock_take_id: 15,
          started_at: '2026-09-23T00:00:00Z',
          closed_at: '2026-09-23T00:00:00Z',
          shrinkage_value: 0,
          shrinkage_percent: 0,
          excess_value: 8,
          excess_percent: 22.94,
          net_variance_value: 8,
        },
      ],
    }
    vi.mocked(reportsApi.stockTakeHistory).mockResolvedValue(history)
    renderStockTakesTab()

    const row = (await screen.findByText('#15')).closest('tr')!
    const cells = within(row).getAllByRole('cell')
    expect(cells).toHaveLength(8) // stock take, started, closed, shrinkage x2, excess x2, net
    expect(cells[3]).toHaveTextContent(money(0)) // shrinkage_value
    expect(cells[5]).toHaveTextContent(money(8)) // excess_value
    expect(cells[5]).toHaveClass('text-emerald-600')
    expect(cells[6]).toHaveTextContent('22.94%') // excess_percent
    // net_variance_value is positive (excess outweighs shrinkage): must not
    // render in the loss color used for a genuine net shortfall.
    expect(cells[7]).toHaveTextContent(money(8))
    expect(cells[7]).toHaveClass('text-emerald-600')
    expect(cells[7]).not.toHaveClass('text-stamp-red')
  })

  it('colors a net loss red so it reads the same as the old shrinkage-only column did', async () => {
    const history: StockTakeHistoryOut = {
      entries: [
        {
          stock_take_id: 2,
          started_at: '2026-08-05T00:00:00Z',
          closed_at: '2026-08-05T00:00:00Z',
          shrinkage_value: 6527,
          shrinkage_percent: 22.94,
          excess_value: 0,
          excess_percent: 0,
          net_variance_value: -6527,
        },
      ],
    }
    vi.mocked(reportsApi.stockTakeHistory).mockResolvedValue(history)
    renderStockTakesTab()

    const row = (await screen.findByText('#2')).closest('tr')!
    const cells = within(row).getAllByRole('cell')
    expect(cells[7]).toHaveTextContent(money(-6527))
    expect(cells[7]).toHaveClass('text-stamp-red')
  })
})
