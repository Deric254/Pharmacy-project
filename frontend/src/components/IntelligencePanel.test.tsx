import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { IntelligencePanel } from './IntelligencePanel'
import { reportsApi } from '../api/reports'
import { useConfigStore } from '../config/store'
import type { IntelligenceArea, IntelligenceInsight, IntelligenceReportOut } from '../types/api'
import type { BusinessConfigOut } from '../types/config'

vi.mock('../api/reports', () => ({
  reportsApi: { intelligence: vi.fn() },
}))

const PERIOD = { label: 'Last 30 days', start: '2026-10-21', end: '2026-11-19' }
const PREVIOUS = { label: 'Previous 30 days', start: '2026-09-21', end: '2026-10-20' }

const INSIGHT: IntelligenceInsight = {
  area: 'sales',
  kind: 'sales_change',
  title: 'Sales fell against the previous period',
  severity: 'problem',
  what_happened: 'Revenue was KES 60.00 against KES 120.00.',
  evidence: [{ label: 'Biggest driver: Widget', value: '-KES 70.00' }],
  period: PERIOD,
  comparison: {
    label: 'Revenue',
    period: PREVIOUS,
    current: 60,
    previous: 120,
    change_pct: -50,
    unit: 'money',
  },
  why_it_matters: 'A sustained drop reduces cash.',
  impact: { amount: -60, basis: 'Revenue difference between the two periods' },
  recommended_action: 'Look at the products driving the drop first.',
  confidence: { level: 'medium', limitations: ['The two periods differ in weekday mix.'] },
  follow_up: 'Re-check after the next 30 days.',
}

function area(partial: Partial<IntelligenceArea> & Pick<IntelligenceArea, 'id' | 'title'>) {
  return { status: 'full', kpis: [], insights: [], missing: [], notes: [], ...partial } as IntelligenceArea
}

function report(): IntelligenceReportOut {
  return {
    as_of: '2026-11-19',
    currency: 'KES',
    period: PERIOD,
    previous_period: PREVIOUS,
    areas: [
      area({
        id: 'sales',
        title: 'Sales Intelligence',
        kpis: [{ label: 'Revenue', value: 60, previous: 120, change_pct: -50, unit: 'money' }],
        insights: [INSIGHT],
      }),
      area({
        id: 'profitability',
        title: 'Profitability Intelligence',
        status: 'unavailable',
        missing: ['Profit figures need the View profit permission.'],
      }),
      area({
        id: 'cash_flow',
        title: 'Cash Flow Intelligence',
        status: 'partial',
        missing: ['Opening cash or bank balances are not recorded.'],
      }),
    ],
  }
}

describe('IntelligencePanel', () => {
  beforeEach(() => {
    vi.mocked(reportsApi.intelligence).mockReset()
    useConfigStore.setState({
      config: { timezone: 'Africa/Nairobi', currency: 'KES' } as BusinessConfigOut,
    })
  })

  it('shows every area with its status, and the full evidence once one is opened', async () => {
    vi.mocked(reportsApi.intelligence).mockResolvedValue(report())
    render(<IntelligencePanel />)

    expect(await screen.findByText('Sales Intelligence')).toBeInTheDocument()
    expect(screen.getByText(/1 finding/)).toBeInTheDocument()
    expect(screen.getByText(/Not available/)).toBeInTheDocument()
    expect(screen.getByText(/No findings · partial/)).toBeInTheDocument()
    // nothing is expanded until asked
    expect(screen.queryByText('Sales fell against the previous period')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Sales Intelligence/ }))
    expect(screen.getByText('Sales fell against the previous period')).toBeInTheDocument()
    expect(screen.getByText(/Suggested action:/)).toBeInTheDocument()
    expect(screen.getByText('Biggest driver: Widget')).toBeInTheDocument()
    expect(screen.getByText(/Revenue difference between the two periods/)).toBeInTheDocument()
    expect(screen.getByText(/The two periods differ in weekday mix/)).toBeInTheDocument()
    expect(screen.getByText(/Re-check after the next 30 days/)).toBeInTheDocument()
    expect(screen.getByText(/Compared with previous 30 days/)).toBeInTheDocument()
    expect(screen.getAllByText(/-50\.0%/)).toHaveLength(2) // the KPI tile and the comparison line
  })

  it('says why an area is unavailable or partial instead of leaving it blank', async () => {
    vi.mocked(reportsApi.intelligence).mockResolvedValue(report())
    render(<IntelligencePanel />)
    fireEvent.click(await screen.findByRole('button', { name: /Profitability Intelligence/ }))
    expect(screen.getByText(/Why it isn't available/)).toBeInTheDocument()
    expect(screen.getByText(/View profit permission/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Cash Flow Intelligence/ }))
    expect(screen.getByText(/What's missing/)).toBeInTheDocument()
    expect(screen.getByText(/Opening cash or bank balances/)).toBeInTheDocument()
  })

  it('clears the old figures while a new period loads and asks for that period', async () => {
    vi.mocked(reportsApi.intelligence).mockResolvedValueOnce(report())
    render(<IntelligencePanel />)
    await screen.findByText('Sales Intelligence')
    expect(reportsApi.intelligence).toHaveBeenLastCalledWith(30)

    vi.mocked(reportsApi.intelligence).mockReturnValueOnce(new Promise(() => {}))
    fireEvent.click(screen.getByRole('button', { name: '90 days' }))
    await waitFor(() => expect(reportsApi.intelligence).toHaveBeenLastCalledWith(90))
    expect(screen.queryByText('Sales Intelligence')).not.toBeInTheDocument()
    expect(screen.getByText('Loading…')).toBeInTheDocument()
  })

  it('shows a plain message, not a raw error, when it cannot load', async () => {
    vi.mocked(reportsApi.intelligence).mockRejectedValue(new Error('Request failed (500)'))
    render(<IntelligencePanel />)
    expect(await screen.findByText(/could not be loaded right now/)).toBeInTheDocument()
    expect(screen.queryByText(/500/)).not.toBeInTheDocument()
  })
})
