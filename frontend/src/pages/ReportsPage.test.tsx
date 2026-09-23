import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ReportsPage } from './ReportsPage'
import { reportsApi } from '../api/reports'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { formatMoney } from '../lib/currency'
import type { ProfitByProductEntry, ProfitReportOut, UserOut } from '../types/api'
import type { BusinessConfigOut } from '../types/config'

vi.mock('../api/reports', () => ({
  reportsApi: {
    profit: vi.fn(),
    profitByProduct: vi.fn(),
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
