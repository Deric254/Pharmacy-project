import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { InventoryPage } from './InventoryPage'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { inventoryApi, productsApi, categoriesApi } from '../api/domain'
import type { ExpiringBatchOut, ProductOut, UserOut } from '../types/api'
import type { BusinessConfigOut } from '../types/config'

vi.mock('../api/domain', () => ({
  inventoryApi: {
    lowStock: vi.fn(),
    expiring: vi.fn(),
    valuation: vi.fn(),
    adjust: vi.fn(),
    reconcile: vi.fn(),
    writeOffExpired: vi.fn(),
    writeOffAllExpired: vi.fn(),
  },
  productsApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
  },
  categoriesApi: {
    list: vi.fn(),
    create: vi.fn(),
  },
}))

const OWNER_USER: UserOut = {
  id: 1,
  full_name: 'Lucy Kangai',
  username: 'lucy',
  role_name: 'ChemistOwner',
  permissions: ['inventory.adjust'],
  is_active: true,
  must_change_password: false,
  terms_accepted: true,
}

const EXPIRED_ITEM: ExpiringBatchOut = {
  batch_id: 101,
  product_id: 1,
  product_name: 'Amoxicillin 500mg',
  batch_number: 'AMX-1',
  expiry_date: '2020-01-01',
  days_remaining: -30,
  qty_remaining: 12,
}

const EXPIRING_SOON_ITEM: ExpiringBatchOut = {
  batch_id: 102,
  product_id: 2,
  product_name: 'Paracetamol 500mg',
  batch_number: 'PARA-2',
  expiry_date: '2029-01-01',
  days_remaining: 45,
  qty_remaining: 30,
}

function seedStores() {
  useAuthStore.setState({ user: OWNER_USER, status: 'authenticated' })
  useConfigStore.setState({
    config: { timezone: 'Africa/Nairobi' } as BusinessConfigOut,
    status: 'ready',
  })
}

describe('InventoryPage expired-stock write-off', () => {
  beforeEach(() => {
    vi.mocked(inventoryApi.lowStock).mockResolvedValue([])
    vi.mocked(inventoryApi.valuation).mockResolvedValue({ total_value: 0, by_product: [] } as never)
    vi.mocked(inventoryApi.reconcile).mockResolvedValue([])
    vi.mocked(inventoryApi.writeOffExpired).mockReset()
    vi.mocked(inventoryApi.writeOffAllExpired).mockReset()
    seedStores()
  })

  it('shows an EXPIRED badge and a per-row write-off button only for genuinely expired batches', async () => {
    vi.mocked(inventoryApi.expiring).mockResolvedValue([EXPIRED_ITEM, EXPIRING_SOON_ITEM])
    render(<InventoryPage />)

    const expiredRow = (await screen.findByText('Amoxicillin 500mg')).closest('div')!
    expect(within(expiredRow).getByText('Expired')).toBeInTheDocument()
    expect(within(expiredRow).getByRole('button', { name: 'Write off' })).toBeInTheDocument()

    const soonRow = screen.getByText('Paracetamol 500mg').closest('div')!
    expect(within(soonRow).queryByText('Expired')).not.toBeInTheDocument()
    expect(within(soonRow).queryByRole('button', { name: 'Write off' })).not.toBeInTheDocument()
    expect(within(soonRow).getByText('45d')).toBeInTheDocument()
  })

  it('the bulk "Write off all expired" button only appears when something is actually expired', async () => {
    vi.mocked(inventoryApi.expiring).mockResolvedValue([EXPIRING_SOON_ITEM])
    render(<InventoryPage />)

    await screen.findByText('Paracetamol 500mg')
    expect(screen.queryByRole('button', { name: /write off all expired/i })).not.toBeInTheDocument()
  })

  it('clicking the per-row write-off button calls the API for that exact batch and refreshes the list', async () => {
    vi.mocked(inventoryApi.expiring)
      .mockResolvedValueOnce([EXPIRED_ITEM])
      .mockResolvedValueOnce([]) 
    vi.mocked(inventoryApi.writeOffExpired).mockResolvedValue({
      batch_id: 101,
      quantity_written_off: 12,
      qty_remaining_after: 0,
    })
    const user = userEvent.setup()
    render(<InventoryPage />)

    await screen.findByText('Amoxicillin 500mg')
    await user.click(screen.getByRole('button', { name: 'Write off' }))

    await waitFor(() => expect(inventoryApi.writeOffExpired).toHaveBeenCalledWith(101))
    await waitFor(() => expect(screen.queryByText('Amoxicillin 500mg')).not.toBeInTheDocument())
  })

  it('clicking "Write off all expired" calls the bulk endpoint, not the per-batch one', async () => {
    vi.mocked(inventoryApi.expiring)
      .mockResolvedValueOnce([EXPIRED_ITEM])
      .mockResolvedValueOnce([])
    vi.mocked(inventoryApi.writeOffAllExpired).mockResolvedValue({
      batches_written_off: 1,
      total_quantity_written_off: 12,
      details: [{ batch_id: 101, quantity_written_off: 12, qty_remaining_after: 0 }],
    })
    const user = userEvent.setup()
    render(<InventoryPage />)

    await screen.findByText('Amoxicillin 500mg')
    await user.click(screen.getByRole('button', { name: /write off all expired/i }))

    await waitFor(() => expect(inventoryApi.writeOffAllExpired).toHaveBeenCalledTimes(1))
    expect(inventoryApi.writeOffExpired).not.toHaveBeenCalled()
  })

  it('a failed write-off shows an error and leaves the batch in the list', async () => {
    vi.mocked(inventoryApi.expiring).mockResolvedValue([EXPIRED_ITEM])
    vi.mocked(inventoryApi.writeOffExpired).mockRejectedValue(new Error('network down'))
    const user = userEvent.setup()
    render(<InventoryPage />)

    await screen.findByText('Amoxicillin 500mg')
    await user.click(screen.getByRole('button', { name: 'Write off' }))

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByText('Amoxicillin 500mg')).toBeInTheDocument()
  })
})

const PRODUCT_MANAGER_USER: UserOut = {
  id: 2,
  full_name: 'Priya Shah',
  username: 'priya',
  role_name: 'ChemistOwner',
  permissions: ['products.manage'],
  is_active: true,
  must_change_password: false,
  terms_accepted: true,
}

const SAMPLE_PRODUCT: ProductOut = {
  id: 1,
  name: 'Amoxicillin 500mg',
  barcode: null,
  unit: 'unit',
  category_id: null,
  category_name: null,
  reorder_point: 10,
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  total_qty_available: 0,
  current_cost: null,
  current_selling_price: null,
  margin_amount: null,
  margin_percent: null,
  markup_percent: null,
}

describe('InventoryPage product category assignment', () => {
  beforeEach(() => {
    vi.mocked(inventoryApi.lowStock).mockResolvedValue([])
    vi.mocked(inventoryApi.expiring).mockResolvedValue([])
    vi.mocked(inventoryApi.valuation).mockResolvedValue({ total_value: 0, by_product: [] } as never)
    vi.mocked(inventoryApi.reconcile).mockResolvedValue([])
    vi.mocked(productsApi.list).mockResolvedValue([])
    vi.mocked(productsApi.create).mockReset()
    vi.mocked(categoriesApi.list).mockResolvedValue([
      { id: 1, name: 'Antibiotics' },
      { id: 2, name: 'Painkillers' },
    ])
    vi.mocked(categoriesApi.create).mockReset()
    useAuthStore.setState({ user: PRODUCT_MANAGER_USER, status: 'authenticated' })
    useConfigStore.setState({
      config: { timezone: 'Africa/Nairobi' } as BusinessConfigOut,
      status: 'ready',
    })
  })

  it('lists existing categories in the new-product form', async () => {
    const user = userEvent.setup()
    render(<InventoryPage />)

    await user.click(await screen.findByRole('button', { name: 'New product' }))

    const select = await screen.findByRole('combobox')
    expect(within(select).getByRole('option', { name: 'Antibiotics' })).toBeInTheDocument()
    expect(within(select).getByRole('option', { name: 'Painkillers' })).toBeInTheDocument()
  })

  it('creating a product sends the chosen category_id', async () => {
    vi.mocked(productsApi.create).mockResolvedValue(SAMPLE_PRODUCT)
    const user = userEvent.setup()
    render(<InventoryPage />)

    await user.click(await screen.findByRole('button', { name: 'New product' }))
    await user.type(screen.getByLabelText('Name'), 'Amoxicillin 500mg')
    const select = await screen.findByRole('combobox')
    await user.selectOptions(select, 'Antibiotics')
    await user.click(screen.getByRole('button', { name: 'Create product' }))

    await waitFor(() =>
      expect(productsApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ category_id: 1 }),
      ),
    )
  })

  it('a product with no category selected is created with category_id null', async () => {
    vi.mocked(productsApi.create).mockResolvedValue(SAMPLE_PRODUCT)
    const user = userEvent.setup()
    render(<InventoryPage />)

    await user.click(await screen.findByRole('button', { name: 'New product' }))
    await user.type(screen.getByLabelText('Name'), 'Uncategorised Item')
    await user.click(screen.getByRole('button', { name: 'Create product' }))

    await waitFor(() =>
      expect(productsApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ category_id: null }),
      ),
    )
  })

  it('adding a new category inline selects it and includes it in the next create call', async () => {
    vi.mocked(categoriesApi.create).mockResolvedValue({ id: 3, name: 'Vitamins' })
    vi.mocked(productsApi.create).mockResolvedValue(SAMPLE_PRODUCT)
    const user = userEvent.setup()
    render(<InventoryPage />)

    await user.click(await screen.findByRole('button', { name: 'New product' }))
    await user.type(screen.getByLabelText('Name'), 'Vitamin C 500mg')
    await user.click(screen.getByRole('button', { name: '+ New' }))
    await user.type(screen.getByPlaceholderText('e.g. Antibiotics'), 'Vitamins')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    await waitFor(() => expect(categoriesApi.create).toHaveBeenCalledWith({ name: 'Vitamins' }))

    await user.click(screen.getByRole('button', { name: 'Create product' }))

    await waitFor(() =>
      expect(productsApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ category_id: 3 }),
      ),
    )
  })

  it('shows an error when the category list fails to load, without breaking the form', async () => {
    vi.mocked(categoriesApi.list).mockRejectedValue(new Error('network down'))
    const user = userEvent.setup()
    render(<InventoryPage />)

    await user.click(await screen.findByRole('button', { name: 'New product' }))

    await waitFor(() =>
      expect(screen.getByText('Could not load categories.')).toBeInTheDocument(),
    )
    expect(screen.getByLabelText('Name')).toBeInTheDocument()
  })
})
