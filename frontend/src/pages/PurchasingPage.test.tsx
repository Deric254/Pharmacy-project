import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PurchasingPage } from './PurchasingPage'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { purchaseOrdersApi, suppliersApi } from '../api/domain'
import type { PurchaseOrderOut, SupplierOut, UserOut } from '../types/api'
import type { BusinessConfigOut } from '../types/config'

vi.mock('../api/domain', () => ({
  purchaseOrdersApi: {
    list: vi.fn(),
    get: vi.fn(),
    quickPurchase: vi.fn(),
    downloadImportTemplate: vi.fn(),
    importFromExcel: vi.fn(),
  },
  suppliersApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    recordPayment: vi.fn(),
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
    sent_at: '2026-01-01T00:00:00Z',
    in_transit_at: '2026-01-01T00:00:00Z',
    received_at: '2026-01-01T00:00:00Z',
    reconciled_at: null,
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
    expect(within(row).getByText('$60.00')).toBeInTheDocument()
  })

  it('does not render the breakdown panel when there are no purchases yet', async () => {
    vi.mocked(purchaseOrdersApi.list).mockResolvedValue([])
    render(<PurchasingPage />)

    await waitFor(() => expect(purchaseOrdersApi.list).toHaveBeenCalled())
    expect(screen.queryByText('Spend by category')).not.toBeInTheDocument()
  })
})
