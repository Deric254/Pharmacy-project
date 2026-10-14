import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PurchasingPage } from './PurchasingPage'
import { ApiError } from '../api/client'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { purchaseOrdersApi, suppliersApi } from '../api/domain'
import type {
  PurchaseOrderOut,
  SupplierKpiOut,
  SupplierOut,
  SupplierPaymentOut,
  UserOut,
} from '../types/api'
import type { BusinessConfigOut } from '../types/config'

vi.mock('../api/domain', () => ({
  purchaseOrdersApi: {
    list: vi.fn(),
    get: vi.fn(),
    quickPurchase: vi.fn(),
    reverse: vi.fn(),
    downloadImportTemplate: vi.fn(),
    importFromExcel: vi.fn(),
  },
  suppliersApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    recordPayment: vi.fn(),
    payments: vi.fn(),
    reversePayment: vi.fn(),
    kpis: vi.fn(),
  },
  productsApi: {
    list: vi.fn(),
  },
}))

const OWNER_USER: UserOut = {
  id: 1,
  full_name: 'Lucy Kangai',
  username: 'lucy',
  role_name: 'ChemistOwner',
  permissions: ['purchasing.approve_po'],
  is_active: true,
  must_change_password: false,
  terms_accepted: true,
}

const SUPPLIER: SupplierOut = {
  id: 1,
  name: 'MedSupply Ltd',
  contact_phone: null,
  contact_email: null,
  address: null,
  notes: null,
  created_at: '2026-01-01T00:00:00Z',
  balance_owed: 0,
}

const ZERO_KPIS: SupplierKpiOut = {
  start_date: '2026-01-01',
  end_date: '2026-01-31',
  total_purchased: 0,
  total_paid: 0,
  net_due: 0,
  active_supplier_count: 0,
}

function poWithItems(
  id: number,
  items: PurchaseOrderOut['items'],
  overrides: Partial<PurchaseOrderOut> = {},
): PurchaseOrderOut {
  return {
    id,
    supplier_id: 1,
    status: 'RECEIVED',
    created_by_user_id: 1,
    notes: null,
    created_at: '2026-01-01T00:00:00Z',
    received_at: '2026-01-01T00:00:00Z',
    reversed_at: null,
    reversal_reason: null,
    items,
    ...overrides,
  }
}

function seedStores() {
  useAuthStore.setState({ user: OWNER_USER, status: 'authenticated' })
  useConfigStore.setState({
    config: { timezone: 'Africa/Nairobi', currency: 'USD' } as BusinessConfigOut,
    status: 'ready',
  })
}

describe('PurchasingPage category display', () => {
  beforeEach(() => {
    vi.mocked(suppliersApi.list).mockResolvedValue([SUPPLIER])
    vi.mocked(suppliersApi.kpis).mockResolvedValue(ZERO_KPIS)
    seedStores()
  })

  it('shows the category next to each product in a purchase order\'s details', async () => {
    vi.mocked(purchaseOrdersApi.list).mockResolvedValue([
      poWithItems(1, [
        {
          id: 1,
          product_id: 1,
          product_name: 'Amoxicillin 500mg',
          category_name: 'Antibiotics',
          quantity_ordered: 10,
          unit_cost_expected: 5,
          quantity_received: 10,
          unit_cost_actual: 5,
          batch_id: 1,
        },
      ]),
    ])
    const user = userEvent.setup()
    render(<PurchasingPage />)

    await user.click(await screen.findByText('#1'))

    const row = (await screen.findByText('Amoxicillin 500mg')).closest('li')!
    expect(within(row).getByText('Antibiotics')).toBeInTheDocument()
  })

  it('shows "Uncategorised" for a product with no category', async () => {
    vi.mocked(purchaseOrdersApi.list).mockResolvedValue([
      poWithItems(1, [
        {
          id: 1,
          product_id: 1,
          product_name: 'Loose Item',
          category_name: null,
          quantity_ordered: 1,
          unit_cost_expected: 2,
          quantity_received: 1,
          unit_cost_actual: 2,
          batch_id: 1,
        },
      ]),
    ])
    const user = userEvent.setup()
    render(<PurchasingPage />)

    await user.click(await screen.findByText('#1'))

    const row = (await screen.findByText('Loose Item')).closest('li')!
    expect(within(row).getByText('Uncategorised')).toBeInTheDocument()
  })
})

describe('PurchasingPage spend-by-category breakdown', () => {
  beforeEach(() => {
    vi.mocked(suppliersApi.list).mockResolvedValue([SUPPLIER])
    vi.mocked(suppliersApi.kpis).mockResolvedValue(ZERO_KPIS)
    seedStores()
  })

  it('shows a spend total per category across all purchase orders', async () => {
    vi.mocked(purchaseOrdersApi.list).mockResolvedValue([
      poWithItems(1, [
        {
          id: 1,
          product_id: 1,
          product_name: 'Amoxicillin 500mg',
          category_name: 'Antibiotics',
          quantity_ordered: 10,
          unit_cost_expected: 5,
          quantity_received: 10,
          unit_cost_actual: 5,
          batch_id: 1,
        },
      ]),
      poWithItems(2, [
        {
          id: 2,
          product_id: 2,
          product_name: 'More Amoxicillin',
          category_name: 'Antibiotics',
          quantity_ordered: 2,
          unit_cost_expected: 5,
          quantity_received: 2,
          unit_cost_actual: 5,
          batch_id: 2,
        },
      ]),
    ])
    render(<PurchasingPage />)

    await screen.findByText('Spend by category')
    const row = screen.getByText('Antibiotics').closest('tr')!
    expect(within(row).getByText('2 items')).toBeInTheDocument()
    expect(within(row).getByText('100%')).toBeInTheDocument()
    expect(within(row).getByText('$60.00')).toBeInTheDocument()
  })

  it('does not render the breakdown panel when there are no purchases yet', async () => {
    vi.mocked(purchaseOrdersApi.list).mockResolvedValue([])
    render(<PurchasingPage />)

    await waitFor(() => expect(purchaseOrdersApi.list).toHaveBeenCalled())
    expect(screen.queryByText('Spend by category')).not.toBeInTheDocument()
  })
})

describe('PurchasingPage supplier KPIs', () => {
  beforeEach(() => {
    vi.mocked(suppliersApi.list).mockResolvedValue([SUPPLIER])
    vi.mocked(suppliersApi.kpis).mockReset()
    vi.mocked(purchaseOrdersApi.list).mockResolvedValue([])
    seedStores()
  })

  it('shows purchased, paid and net due from the KPI endpoint', async () => {
    vi.mocked(suppliersApi.kpis).mockResolvedValue({
      start_date: '2026-09-01',
      end_date: '2026-09-30',
      total_purchased: 5000,
      total_paid: 3000,
      net_due: 2000,
      active_supplier_count: 2,
    })
    render(<PurchasingPage />)

    await screen.findByText('$5,000.00')
    expect(screen.getByText('$3,000.00')).toBeInTheDocument()
    expect(screen.getByText('$2,000.00')).toBeInTheDocument()
  })

  it('re-fetches KPIs when the date range preset changes', async () => {
    vi.mocked(suppliersApi.kpis).mockResolvedValue(ZERO_KPIS)
    const user = userEvent.setup()
    render(<PurchasingPage />)

    await waitFor(() => expect(suppliersApi.kpis).toHaveBeenCalledTimes(1))

    await user.click(screen.getByRole('button', { name: 'Today' }))

    await waitFor(() => expect(suppliersApi.kpis).toHaveBeenCalledTimes(2))
  })

  it('shows an error without crashing the page when the KPI request fails', async () => {
    vi.mocked(suppliersApi.kpis).mockRejectedValue(new Error('network down'))
    render(<PurchasingPage />)

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })
})

describe('PurchasingPage Excel import category summary', () => {
  beforeEach(() => {
    vi.mocked(suppliersApi.list).mockResolvedValue([SUPPLIER])
    vi.mocked(suppliersApi.kpis).mockResolvedValue(ZERO_KPIS)
    vi.mocked(purchaseOrdersApi.list).mockResolvedValue([])
    seedStores()
  })

  it('shows a per-category breakdown of what was just imported', async () => {
    vi.mocked(purchaseOrdersApi.importFromExcel).mockResolvedValue(
      poWithItems(1, [
        {
          id: 1,
          product_id: 1,
          product_name: 'Amoxicillin 500mg',
          category_name: 'Antibiotics',
          quantity_ordered: 10,
          unit_cost_expected: 5,
          quantity_received: 10,
          unit_cost_actual: 5,
          batch_id: 1,
        },
      ]),
    )
    const user = userEvent.setup()
    render(<PurchasingPage />)

    await user.click(await screen.findByRole('button', { name: 'Import from Excel' }))
    const file = new File(['dummy'], 'purchase.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    const fileInput = screen.getByLabelText('Choose file')
    await user.upload(fileInput, file)
    await user.click(screen.getByRole('button', { name: 'Import' }))

    await screen.findByText('Stock received')
    const row = screen.getByText('Antibiotics').closest('li')!
    expect(within(row).getByText('100% · $50.00')).toBeInTheDocument()
  })
})


describe('Supplier payments', () => {
  const OWED: SupplierOut = { ...SUPPLIER, balance_owed: 1000 }
  const PAYMENT: SupplierPaymentOut = {
    id: 7,
    amount: 400,
    notes: null,
    created_at: '2026-01-02T10:00:00Z',
    reversed: false,
    reversal_reason: null,
  }

  function seedFor(permissions: string[], supplier: SupplierOut = OWED) {
    vi.mocked(suppliersApi.list).mockResolvedValue([supplier])
    vi.mocked(suppliersApi.kpis).mockResolvedValue(ZERO_KPIS)
    vi.mocked(purchaseOrdersApi.list).mockResolvedValue([])
    vi.mocked(suppliersApi.payments).mockResolvedValue([PAYMENT])
    vi.mocked(suppliersApi.recordPayment).mockReset()
    vi.mocked(suppliersApi.reversePayment).mockReset()
    seedStores()
    useAuthStore.setState({ user: { ...OWNER_USER, permissions }, status: 'authenticated' })
  }

  async function openSuppliers() {
    const user = userEvent.setup()
    render(<PurchasingPage />)
    await user.click(await screen.findByRole('button', { name: 'Suppliers' }))
    return user
  }

  it('sends one payment with an idempotency key, even if Pay is clicked twice quickly', async () => {
    seedFor(['purchasing.approve_po'])
    let release: (value: SupplierOut) => void = () => {}
    vi.mocked(suppliersApi.recordPayment).mockReturnValue(
      new Promise<SupplierOut>((resolve) => {
        release = resolve
      }),
    )
    const user = await openSuppliers()

    await user.type(await screen.findByLabelText(/Payment amount for MedSupply/), '100')
    const pay = screen.getByRole('button', { name: 'Pay' })
    await user.click(pay)
    await user.click(pay)

    expect(suppliersApi.recordPayment).toHaveBeenCalledTimes(1)
    expect(vi.mocked(suppliersApi.recordPayment).mock.calls[0][1]).toMatchObject({
      amount: 100,
      idempotency_key: expect.stringMatching(/^[A-Za-z0-9_-]{8,64}$/),
    })
    expect(screen.getByRole('button', { name: 'Paying…' })).toBeDisabled()
    release({ ...OWED, balance_owed: 900 })
  })

  it('refuses more than the amount owed without calling the server', async () => {
    seedFor(['purchasing.approve_po'])
    const user = await openSuppliers()

    await user.type(await screen.findByLabelText(/Payment amount for MedSupply/), '1000.01')
    await user.click(screen.getByRole('button', { name: 'Pay' }))

    expect(await screen.findByText(/cannot exceed the amount owed/)).toBeInTheDocument()
    expect(suppliersApi.recordPayment).not.toHaveBeenCalled()
  })

  it('refuses amounts with more than two decimals or zero', async () => {
    seedFor(['purchasing.approve_po'])
    const user = await openSuppliers()
    const box = await screen.findByLabelText(/Payment amount for MedSupply/)

    await user.type(box, '10.005')
    await user.click(screen.getByRole('button', { name: 'Pay' }))
    expect(await screen.findByText(/at most 2 decimal places/)).toBeInTheDocument()

    await user.clear(box)
    await user.type(box, '0')
    await user.click(screen.getByRole('button', { name: 'Pay' }))
    expect(suppliersApi.recordPayment).not.toHaveBeenCalled()
  })

  it('reuses the same key when the same payment is retried after a network failure', async () => {
    seedFor(['purchasing.approve_po'])
    vi.mocked(suppliersApi.recordPayment)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ ...OWED, balance_owed: 900 })
    const user = await openSuppliers()

    await user.type(await screen.findByLabelText(/Payment amount for MedSupply/), '100')
    await user.click(screen.getByRole('button', { name: 'Pay' }))
    await screen.findByText(/Could not reach the server/)
    await user.click(screen.getByRole('button', { name: 'Pay' }))

    const calls = vi.mocked(suppliersApi.recordPayment).mock.calls
    expect(calls).toHaveLength(2)
    expect(calls[1][1].idempotency_key).toBe(calls[0][1].idempotency_key)
  })

  it('uses a new key after the server refuses a payment outright', async () => {
    seedFor(['purchasing.approve_po'])
    vi.mocked(suppliersApi.recordPayment)
      .mockRejectedValueOnce(new ApiError(409, 'Payment exceeds the amount owed', null))
      .mockResolvedValueOnce({ ...OWED, balance_owed: 900 })
    const user = await openSuppliers()

    await user.type(await screen.findByLabelText(/Payment amount for MedSupply/), '100')
    await user.click(screen.getByRole('button', { name: 'Pay' }))
    await screen.findByText('Payment exceeds the amount owed')
    await user.click(screen.getByRole('button', { name: 'Pay' }))

    const calls = vi.mocked(suppliersApi.recordPayment).mock.calls
    expect(calls[1][1].idempotency_key).not.toBe(calls[0][1].idempotency_key)
  })

  it('labels an overpaid supplier as credit and offers no Pay button', async () => {
    seedFor(['purchasing.approve_po'], { ...SUPPLIER, balance_owed: -2106000 })
    await openSuppliers()

    expect(await screen.findByText(/Overpaid \(credit\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pay' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Payments' })).toBeInTheDocument()
  })

  it('does not show Reverse to someone without the reversal permission', async () => {
    seedFor(['purchasing.approve_po'])
    const user = await openSuppliers()

    await user.click(await screen.findByRole('button', { name: 'Payments' }))
    await screen.findByText(/400/)

    expect(screen.queryByRole('button', { name: 'Reverse' })).not.toBeInTheDocument()
  })

  it('requires a reason, then reverses the payment', async () => {
    seedFor(['purchasing.approve_po', 'purchasing.reverse_payment'])
    vi.mocked(suppliersApi.reversePayment).mockResolvedValue(OWED)
    const user = await openSuppliers()

    await user.click(await screen.findByRole('button', { name: 'Payments' }))
    await user.click(await screen.findByRole('button', { name: 'Reverse' }))

    await user.click(screen.getByRole('button', { name: 'Confirm reversal' }))
    expect(await screen.findByText(/Give a reason/)).toBeInTheDocument()
    expect(suppliersApi.reversePayment).not.toHaveBeenCalled()

    await user.type(screen.getByLabelText(/Reason for reversing/), 'Typed 400 instead of 40')
    await user.click(screen.getByRole('button', { name: 'Confirm reversal' }))

    await waitFor(() =>
      expect(suppliersApi.reversePayment).toHaveBeenCalledWith(1, 7, {
        reason: 'Typed 400 instead of 40',
      }),
    )
  })

  it('shows a reversed payment as reversed, with its reason, and no second Reverse', async () => {
    seedFor(['purchasing.approve_po', 'purchasing.reverse_payment'])
    vi.mocked(suppliersApi.payments).mockResolvedValue([
      { ...PAYMENT, reversed: true, reversal_reason: 'Typed wrong amount' },
    ])
    const user = await openSuppliers()

    await user.click(await screen.findByRole('button', { name: 'Payments' }))

    expect(await screen.findByText(/Reversed: Typed wrong amount/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reverse' })).not.toBeInTheDocument()
  })
})


describe('Purchase reversal', () => {
  const LINE: PurchaseOrderOut['items'][number] = {
    id: 1,
    product_id: 1,
    product_name: 'Amoxicillin 500mg',
    category_name: 'Antibiotics',
    quantity_ordered: 10,
    unit_cost_expected: 8,
    quantity_received: 10,
    unit_cost_actual: 8,
    batch_id: 1,
  }

  function seedFor(permissions: string[], order: PurchaseOrderOut = poWithItems(1, [LINE])) {
    vi.mocked(suppliersApi.list).mockResolvedValue([SUPPLIER])
    vi.mocked(suppliersApi.kpis).mockResolvedValue(ZERO_KPIS)
    vi.mocked(purchaseOrdersApi.list).mockReset()
    vi.mocked(purchaseOrdersApi.list).mockResolvedValue([order])
    vi.mocked(purchaseOrdersApi.reverse).mockReset()
    seedStores()
    useAuthStore.setState({ user: { ...OWNER_USER, permissions }, status: 'authenticated' })
  }

  async function openPurchase() {
    const user = userEvent.setup()
    render(<PurchasingPage />)
    await user.click(await screen.findByText('#1'))
    return user
  }

  it('offers Reverse purchase only to someone with the permission', async () => {
    seedFor(['purchasing.create_po'])
    await openPurchase()

    await screen.findByText('Amoxicillin 500mg')

    expect(screen.queryByRole('button', { name: 'Reverse purchase' })).not.toBeInTheDocument()
  })

  it('does not offer Reverse purchase on one that is already reversed, and says why', async () => {
    seedFor(
      ['purchasing.reverse_purchase'],
      poWithItems(1, [LINE], {
        status: 'REVERSED',
        reversed_at: '2026-01-02T00:00:00Z',
        reversal_reason: 'Wrong supplier',
      }),
    )
    await openPurchase()

    expect(await screen.findByText(/Wrong supplier/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reverse purchase' })).not.toBeInTheDocument()
  })

  it('explains what will happen, requires a reason, then reverses and refreshes', async () => {
    seedFor(['purchasing.reverse_purchase'])
    vi.mocked(purchaseOrdersApi.reverse).mockResolvedValue(
      poWithItems(1, [LINE], { status: 'REVERSED' }),
    )
    const user = await openPurchase()

    await user.click(await screen.findByRole('button', { name: 'Reverse purchase' }))
    expect(screen.getByText(/removes this stock from inventory and cancels/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Confirm reversal' }))
    expect(await screen.findByText(/Give a reason/)).toBeInTheDocument()
    expect(purchaseOrdersApi.reverse).not.toHaveBeenCalled()

    await user.type(screen.getByLabelText(/Reason for reversing this purchase/), 'Wrong supplier')
    await user.click(screen.getByRole('button', { name: 'Confirm reversal' }))

    await waitFor(() =>
      expect(purchaseOrdersApi.reverse).toHaveBeenCalledWith(1, { reason: 'Wrong supplier' }),
    )
    await waitFor(() => expect(purchaseOrdersApi.list).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByText('Purchase order #1')).not.toBeInTheDocument())
  })

  it('shows the refusal from the server and keeps the purchase open for another try', async () => {
    seedFor(['purchasing.reverse_purchase'])
    vi.mocked(purchaseOrdersApi.reverse).mockRejectedValue(
      new ApiError(409, 'Some of batch B1 has already been sold or moved', null),
    )
    const user = await openPurchase()

    await user.click(await screen.findByRole('button', { name: 'Reverse purchase' }))
    await user.type(screen.getByLabelText(/Reason for reversing this purchase/), 'Wrong supplier')
    await user.click(screen.getByRole('button', { name: 'Confirm reversal' }))

    expect(await screen.findByText(/already been sold or moved/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm reversal' })).toBeEnabled()
    expect(purchaseOrdersApi.list).toHaveBeenCalledTimes(1)
  })

  it('sends only one request however fast Confirm is clicked', async () => {
    seedFor(['purchasing.reverse_purchase'])
    let release: (value: PurchaseOrderOut) => void = () => {}
    vi.mocked(purchaseOrdersApi.reverse).mockReturnValue(
      new Promise<PurchaseOrderOut>((resolve) => {
        release = resolve
      }),
    )
    const user = await openPurchase()

    await user.click(await screen.findByRole('button', { name: 'Reverse purchase' }))
    await user.type(screen.getByLabelText(/Reason for reversing this purchase/), 'Wrong supplier')
    const confirm = screen.getByRole('button', { name: 'Confirm reversal' })
    await user.click(confirm)
    await user.click(confirm)

    expect(purchaseOrdersApi.reverse).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Reversing…' })).toBeDisabled()
    release(poWithItems(1, [LINE], { status: 'REVERSED' }))
  })

  it('marks a reversed purchase in the list', async () => {
    seedFor(
      ['purchasing.reverse_purchase'],
      poWithItems(1, [LINE], { status: 'REVERSED', reversal_reason: 'Wrong supplier' }),
    )
    render(<PurchasingPage />)

    expect(await screen.findByText('REVERSED')).toBeInTheDocument()
  })
})
