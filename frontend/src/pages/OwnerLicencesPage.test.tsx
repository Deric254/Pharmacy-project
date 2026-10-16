import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { OwnerLicencesPage } from './OwnerLicencesPage'
import { licenceApi } from '../api/licence'
import { ownerLicencesApi, type LedgerCustomer, type LedgerOut } from '../api/ownerLicences'

vi.mock('../api/licence', () => ({ licenceApi: { state: vi.fn() } }))
vi.mock('../api/ownerLicences', () => ({
  ownerLicencesApi: {
    list: vi.fn(),
    issue: vi.fn(),
    setPayment: vi.fn(),
    setRetired: vi.fn(),
    remove: vi.fn(),
    exportCsv: vi.fn(),
  },
}))

function customer(over: Partial<LedgerCustomer>): LedgerCustomer {
  return {
    id: 'c1',
    name: 'Jane Doe',
    code: 'ABCD-EFGH-JKLM',
    phone: '',
    notes: '',
    monthly_fee: null,
    created: '2026-10-01',
    last_issued: '2026-10-01',
    expires: '2026-11-15',
    days_left: 30,
    status: 'active',
    retired: false,
    retired_on: null,
    active_computers: 1,
    licence_count: 1,
    latest_licence: 'tok',
    months: [{ month: '2026-10', paid: false }],
    paid_count: 0,
    unpaid_months: ['2026-10'],
    amount_outstanding: null,
    ...over,
  }
}

function ledger(customers: LedgerCustomer[]): LedgerOut {
  const live = customers.filter((c) => !c.retired)
  return {
    summary: {
      total_customers: live.length,
      retired: customers.length - live.length,
      active: live.length,
      expiring: 0,
      expired: 0,
      unpaid_months: 0,
      amount_outstanding: 0,
    },
    customers,
    current_month: '2026-10',
  }
}

async function fill(code: string, name: string) {
  render(<OwnerLicencesPage />)
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText(/activation code from the customer/i), code)
  if (name !== '') {
    const nameInput = screen.getByLabelText(/customer name/i)
    await user.clear(nameInput)
    await user.type(nameInput, name)
  }
  await user.type(screen.getByLabelText(/owner key/i), 'the-key')
  return user
}

const submit = () => screen.getByRole('button', { name: /generate licence/i })

describe('OwnerLicencesPage: a new code for a known name', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(licenceApi.state).mockResolvedValue({
      ok: true,
      kind: 'owner',
      message: '',
      install_id: 'X',
    } as never)
  })

  it('makes the owner choose migration or extra before generating', async () => {
    vi.mocked(ownerLicencesApi.list).mockResolvedValue(ledger([customer({})]))
    const user = await fill('WXYZ-2345-6789', 'jane  doe')
    expect(screen.getByText(/already has 1 active computer/i)).toBeInTheDocument()
    expect(submit()).toBeDisabled()

    await user.click(screen.getByLabelText(/migration/i))
    expect(submit()).toBeEnabled()
    vi.mocked(ownerLicencesApi.issue).mockResolvedValue({
      licence: 'L',
      user: 'Jane Doe',
      code: 'WXYZ23456789',
      expires: '2026-11-15',
      renewal: false,
      retired_code: 'ABCDEFGHJKLM',
      customer: customer({ id: 'c2' }),
    })
    await user.click(submit())
    await waitFor(() => expect(ownerLicencesApi.issue).toHaveBeenCalledTimes(1))
    expect(ownerLicencesApi.issue).toHaveBeenCalledWith(
      expect.objectContaining({ relation: 'migration', replaces: 'ABCD-EFGH-JKLM' }),
    )
    expect(await screen.findByText(/old computer \(ABCDEFGHJKLM\) is now retired/i)).toBeVisible()
  })

  it('an extra computer sends no replacement; several computers need a pick for migration', async () => {
    vi.mocked(ownerLicencesApi.list).mockResolvedValue(
      ledger([
        customer({ id: 'c1', active_computers: 2 }),
        customer({ id: 'c2', code: 'QRST-JKLM-NPQR', active_computers: 2 }),
      ]),
    )
    const user = await fill('WXYZ-2345-6789', 'Jane Doe')
    await user.click(screen.getByLabelText(/migration/i))
    expect(submit()).toBeDisabled() // which one is replaced?
    await user.selectOptions(
      screen.getByLabelText(/which computer is being replaced/i),
      'QRST-JKLM-NPQR',
    )
    expect(submit()).toBeEnabled()
    await user.click(screen.getByLabelText(/extra computer/i))
    expect(submit()).toBeEnabled()
    vi.mocked(ownerLicencesApi.issue).mockResolvedValue({
      licence: 'L',
      user: 'Jane Doe',
      code: 'WXYZ23456789',
      expires: '2026-11-15',
      renewal: false,
      retired_code: null,
      customer: customer({ id: 'c3' }),
    })
    await user.click(submit())
    await waitFor(() => expect(ownerLicencesApi.issue).toHaveBeenCalledTimes(1))
    expect(ownerLicencesApi.issue).toHaveBeenCalledWith(
      expect.objectContaining({ relation: 'extra', replaces: undefined }),
    )
  })

  it('a new name needs no choice, and a retired name does not count', async () => {
    vi.mocked(ownerLicencesApi.list).mockResolvedValue(
      ledger([customer({ retired: true, status: 'retired', retired_on: '2026-10-10' })]),
    )
    await fill('WXYZ-2345-6789', 'Jane Doe')
    expect(screen.queryByText(/already has/i)).not.toBeInTheDocument()
    expect(submit()).toBeEnabled()
  })

  it('refuses to renew a retired computer until it is restored', async () => {
    vi.mocked(ownerLicencesApi.list).mockResolvedValue(
      ledger([customer({ retired: true, status: 'retired', retired_on: '2026-10-10' })]),
    )
    await fill('ABCD-EFGH-JKLM', '')
    expect(await screen.findByText(/was retired on 2026-10-10/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /generate renewal/i })).toBeDisabled()
  })
})
