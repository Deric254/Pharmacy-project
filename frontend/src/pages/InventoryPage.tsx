import { useEffect, useState, type FormEvent } from 'react'
import { inventoryApi, productsApi } from '../api/domain'
import { useAuthStore } from '../auth/store'
import { useCurrencyFormatter } from '../lib/currency'
import { ApiError, downloadExport } from '../api/client'
import { Modal } from '../components/Modal'
import type {
  AdjustmentReason,
  BatchOut,
  ExpiringBatchOut,
  LowStockProductOut,
  ProductCreate,
  ProductOut,
  ProductUpdate,
  ReconciliationIssueOut,
  StockValuationOut,
} from '../types/api'

const ADJUSTMENT_REASONS: AdjustmentReason[] = [
  'MISCOUNT',
  'DAMAGED',
  'EXPIRED',
  'THEFT_OR_LOSS',
  'DATA_ENTRY_ERROR',
  'OTHER',
]

export function InventoryPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canAdjust = hasPermission('inventory.adjust')
  const canManageProducts = hasPermission('products.manage')
  const formatCurrency = useCurrencyFormatter()

  const [lowStock, setLowStock] = useState<LowStockProductOut[]>([])
  const [expiring, setExpiring] = useState<ExpiringBatchOut[]>([])
  const [valuation, setValuation] = useState<StockValuationOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([inventoryApi.lowStock(), inventoryApi.expiring(60), inventoryApi.valuation()])
      .then(([low, exp, val]) => {
        if (cancelled) return
        setLowStock(low)
        setExpiring(exp)
        setValuation(val)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load inventory data.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  return (
    <div className="p-6">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="font-display text-2xl text-ink">Inventory</h1>
        <div className="flex gap-2">
          <button
            onClick={() => void downloadExport('/products', {}, 'excel')}
            className="border border-rule px-3 py-1 text-sm text-ink-soft hover:border-brass"
          >
            Export to Excel
          </button>
          <button
            onClick={() => setReloadKey((k) => k + 1)}
            className="border border-rule px-3 py-1 text-sm text-ink-soft hover:border-brass"
          >
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      <div className="mb-6 ledger-panel p-4">
        <h2 className="text-xs uppercase tracking-wide text-ink-soft">Stock value on hand</h2>
        <p className="figure mt-2 text-2xl text-ink">
          {valuation ? formatCurrency(valuation.total_value) : loading ? '…' : '—'}
        </p>
      </div>

      {canManageProducts && (
        <ProductManagementPanel onChanged={() => setReloadKey((k) => k + 1)} />
      )}

      {canAdjust && (
        <AdjustmentPanel onAdjusted={() => setReloadKey((k) => k + 1)} />
      )}

      {canAdjust && <ReconciliationPanel />}

      <div className="grid gap-6 lg:grid-cols-2">
        <section>
          <h2 className="mb-2 text-xs uppercase tracking-wide text-ink-soft">
            Low stock ({lowStock.length})
          </h2>
          <div className="ledger-panel divide-y divide-rule">
            {lowStock.map((item) => (
              <div key={item.product_id} className="flex justify-between px-3 py-2 text-sm">
                <span>{item.name}</span>
                <span className="figure text-stamp-red">
                  {item.total_qty_available} / {item.reorder_point}
                </span>
              </div>
            ))}
            {lowStock.length === 0 && !loading && (
              <p className="px-3 py-3 text-sm text-ink-soft">Nothing below reorder point.</p>
            )}
          </div>
        </section>

        <section>
          <h2 className="mb-2 text-xs uppercase tracking-wide text-ink-soft">
            Expiring within 60 days ({expiring.length})
          </h2>
          <div className="ledger-panel divide-y divide-rule">
            {expiring.map((item) => (
              <div key={item.batch_id} className="flex justify-between px-3 py-2 text-sm">
                <span className="truncate pr-2">
                  {item.product_name} <span className="text-ink-soft">· {item.batch_number}</span>
                </span>
                <span className="figure shrink-0 text-stamp-red">{item.days_remaining}d</span>
              </div>
            ))}
            {expiring.length === 0 && !loading && (
              <p className="px-3 py-3 text-sm text-ink-soft">Nothing expiring soon.</p>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

function AdjustmentPanel({ onAdjusted }: { onAdjusted: () => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ProductOut[]>([])
  const [selectedProduct, setSelectedProduct] = useState<ProductOut | null>(null)
  const [batches, setBatches] = useState<BatchOut[]>([])
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  async function handleSearch(e: FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    setError(null)
    try {
      setResults(await productsApi.list(query.trim()))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Search failed.')
    }
  }

  async function selectProduct(product: ProductOut) {
    setSelectedProduct(product)
    setResults([])
    setQuery('')
    setError(null)
    try {
      setBatches(await productsApi.batches(product.id))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load batches.')
    }
  }

  async function submitAdjustment(
    batchId: number,
    delta: number,
    reason: AdjustmentReason,
    notes: string,
  ) {
    setError(null)
    setSuccess(null)
    try {
      const result = await inventoryApi.adjust({
        batch_id: batchId,
        quantity_delta: delta,
        reason,
        notes: notes || null,
      })
      setSuccess(`Batch ${batchId} adjusted by ${delta > 0 ? '+' : ''}${delta}. New qty: ${result.qty_remaining_after}.`)
      if (selectedProduct) setBatches(await productsApi.batches(selectedProduct.id))
      onAdjusted()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Adjustment failed.')
    }
  }

  return (
    <div className="mb-6 ledger-panel p-4">
      <h2 className="mb-3 text-xs uppercase tracking-wide text-ink-soft">Adjust stock</h2>

      {!selectedProduct && (
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search product by name"
            className="flex-1 border border-rule bg-paper px-3 py-2 text-sm outline-none focus-visible:border-brass"
          />
          <button
            type="submit"
            className="border border-ink bg-ink px-4 py-2 text-sm text-paper"
          >
            Search
          </button>
        </form>
      )}

      {results.length > 0 && (
        <ul className="mt-2 divide-y divide-rule border border-rule">
          {results.map((p) => (
            <li key={p.id}>
              <button
                onClick={() => void selectProduct(p)}
                className="w-full px-3 py-2 text-left text-sm hover:bg-paper"
              >
                {p.name} <span className="text-ink-soft">({p.total_qty_available} on hand)</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && (
        <p role="alert" className="mt-3 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}
      {success && (
        <p className="mt-3 border border-stamp-green-soft bg-stamp-green-soft/40 px-3 py-2 text-sm text-stamp-green">
          {success}
        </p>
      )}

      {selectedProduct && (
        <div className="mt-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-medium">{selectedProduct.name}</p>
            <button
              onClick={() => {
                setSelectedProduct(null)
                setBatches([])
              }}
              className="text-xs text-ink-soft underline"
            >
              Change product
            </button>
          </div>
          <div className="space-y-2">
            {batches.map((batch) => (
              <BatchAdjustRow key={batch.id} batch={batch} onSubmit={submitAdjustment} />
            ))}
            {batches.length === 0 && (
              <p className="text-sm text-ink-soft">No batches for this product yet.</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function BatchAdjustRow({
  batch,
  onSubmit,
}: {
  batch: BatchOut
  onSubmit: (
    batchId: number,
    delta: number,
    reason: AdjustmentReason,
    notes: string,
  ) => Promise<void>
}) {
  const [delta, setDelta] = useState(0)
  const [reason, setReason] = useState<AdjustmentReason>('MISCOUNT')
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)

  return (
    <div className="ruled-row grid grid-cols-[1fr_auto] items-center gap-2 pb-2 text-sm">
      <div>
        <p>
          {batch.batch_number} <span className="text-ink-soft">· exp {batch.expiry_date}</span>
        </p>
        <p className="figure text-ink-soft">{batch.qty_remaining} remaining</p>
      </div>
      <div className="flex flex-wrap items-center justify-end gap-1">
        <input
          type="number"
          value={delta || ''}
          onChange={(e) => setDelta(Number(e.target.value))}
          placeholder="±qty"
          className="figure w-20 border border-rule bg-paper px-2 py-1"
        />
        <select
          value={reason}
          onChange={(e) => setReason(e.target.value as AdjustmentReason)}
          className="border border-rule bg-paper px-1 py-1 text-xs"
        >
          {ADJUSTMENT_REASONS.map((r) => (
            <option key={r} value={r}>
              {r.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
        <input
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="notes (optional)"
          className="w-28 border border-rule bg-paper px-2 py-1 text-xs"
        />
        <button
          disabled={delta === 0 || submitting}
          onClick={async () => {
            setSubmitting(true)
            await onSubmit(batch.id, delta, reason, notes)
            setDelta(0)
            setNotes('')
            setSubmitting(false)
          }}
          className="border border-ink bg-ink px-2 py-1 text-xs text-paper disabled:opacity-40"
        >
          Apply
        </button>
      </div>
    </div>
  )
}

function ReconciliationPanel() {
  const [issues, setIssues] = useState<ReconciliationIssueOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    inventoryApi
      .reconcile()
      .then(setIssues)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Reconciliation failed.'))
  }, [])

  if (error) return null // reconciliation is a bonus panel, not worth blocking the page on

  return (
    <div className="mb-6 ledger-panel p-4">
      <h2 className="mb-2 text-xs uppercase tracking-wide text-ink-soft">
        Ledger reconciliation {issues ? `(${issues.length} discrepancies)` : ''}
      </h2>
      {issues === null && <p className="text-sm text-ink-soft">Checking…</p>}
      {issues?.length === 0 && (
        <p className="text-sm text-stamp-green">
          Every batch's cached quantity matches its stock movement ledger.
        </p>
      )}
      {issues && issues.length > 0 && (
        <ul className="divide-y divide-rule">
          {issues.map((issue) => (
            <li key={issue.batch_id} className="flex justify-between py-1 text-sm">
              <span>Batch #{issue.batch_id}</span>
              <span className="figure text-stamp-red">
                cached {issue.qty_remaining} vs ledger {issue.ledger_sum} (
                {issue.discrepancy > 0 ? '+' : ''}
                {issue.discrepancy})
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ProductManagementPanel({ onChanged }: { onChanged: () => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ProductOut[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [editing, setEditing] = useState<ProductOut | null>(null)
  const [confirmDeactivate, setConfirmDeactivate] = useState<ProductOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function runSearch(q: string) {
    setError(null)
    try {
      setResults(await productsApi.list(q))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Search failed.')
    }
  }

  useEffect(() => {
    void runSearch('')
    // Only on mount -- subsequent searches are user-triggered via the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function refreshAfterChange() {
    void runSearch(query)
    onChanged()
  }

  return (
    <div className="mb-6 ledger-panel p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs uppercase tracking-wide text-ink-soft">Products</h2>
        <button
          onClick={() => setShowCreate(true)}
          className="border border-ink bg-ink px-3 py-1 text-xs text-paper"
        >
          New product
        </button>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          void runSearch(query)
        }}
        className="mb-3 flex gap-2"
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search products by name"
          className="flex-1 border border-rule bg-paper px-3 py-2 text-sm outline-none focus-visible:border-brass"
        />
        <button type="submit" className="border border-rule px-4 py-2 text-sm hover:border-brass">
          Search
        </button>
      </form>

      {error && (
        <p role="alert" className="mb-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      <ul className="divide-y divide-rule border border-rule">
        {results.map((p) => (
          <li key={p.id} className="flex items-center justify-between px-3 py-2 text-sm">
            <span className={p.is_active ? '' : 'text-ink-soft line-through'}>{p.name}</span>
            <div className="flex items-center gap-3">
              <button
                onClick={() => setEditing(p)}
                className="text-xs text-ink-soft underline decoration-dotted"
              >
                Edit
              </button>
              {p.is_active && (
                <button
                  onClick={() => setConfirmDeactivate(p)}
                  className="text-xs text-stamp-red underline decoration-dotted"
                >
                  Deactivate
                </button>
              )}
            </div>
          </li>
        ))}
        {results.length === 0 && (
          <li className="px-3 py-4 text-center text-sm text-ink-soft">
            No products match. Try a different search, or add a new one.
          </li>
        )}
      </ul>

      {showCreate && (
        <ProductFormModal
          product={null}
          onClose={() => setShowCreate(false)}
          onSaved={() => {
            setShowCreate(false)
            refreshAfterChange()
          }}
        />
      )}
      {editing && (
        <ProductFormModal
          product={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            refreshAfterChange()
          }}
        />
      )}
      {confirmDeactivate && (
        <ConfirmDeactivateProductModal
          product={confirmDeactivate}
          onClose={() => setConfirmDeactivate(null)}
          onConfirmed={() => {
            setConfirmDeactivate(null)
            refreshAfterChange()
          }}
        />
      )}
    </div>
  )
}

function ProductFormModal({
  product,
  onClose,
  onSaved,
}: {
  product: ProductOut | null
  onClose: () => void
  onSaved: () => void
}) {
  const isEdit = product !== null
  const [name, setName] = useState(product?.name ?? '')
  const [barcode, setBarcode] = useState(product?.barcode ?? '')
  const [unit, setUnit] = useState(product?.unit ?? 'unit')
  const [reorderPoint, setReorderPoint] = useState(product?.reorder_point ?? 10)
  const [price, setPrice] = useState(product?.default_selling_price ?? 0)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      if (isEdit) {
        const payload: ProductUpdate = {
          name,
          barcode: barcode || null,
          unit,
          reorder_point: reorderPoint,
          default_selling_price: price,
        }
        await productsApi.update(product.id, payload)
      } else {
        const payload: ProductCreate = {
          name,
          barcode: barcode || null,
          unit,
          reorder_point: reorderPoint,
          default_selling_price: price,
        }
        await productsApi.create(payload)
      }
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save this product.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={isEdit ? 'Edit product' : 'New product'} onClose={onClose}>
      <form onSubmit={(e) => void handleSubmit(e)} className="space-y-3">
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            autoFocus
            className="mt-1 w-full border border-rule bg-paper px-3 py-2"
          />
        </label>
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">
            Barcode (optional)
          </span>
          <input
            value={barcode}
            onChange={(e) => setBarcode(e.target.value)}
            className="mt-1 w-full border border-rule bg-paper px-3 py-2"
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="block text-xs uppercase tracking-wide text-ink-soft">Unit</span>
            <input
              value={unit}
              onChange={(e) => setUnit(e.target.value)}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="block text-xs uppercase tracking-wide text-ink-soft">
              Reorder point
            </span>
            <input
              type="number"
              min={0}
              value={reorderPoint}
              onChange={(e) => setReorderPoint(Number(e.target.value))}
              className="figure mt-1 w-full border border-rule bg-paper px-3 py-2"
            />
          </label>
        </div>
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">
            Selling price
          </span>
          <input
            type="number"
            min={0}
            step={0.01}
            value={price}
            onChange={(e) => setPrice(Number(e.target.value))}
            className="figure mt-1 w-full border border-rule bg-paper px-3 py-2"
          />
        </label>

        <p className="text-xs text-ink-soft">
          This only creates the product record. Stock is always added through Purchasing, so
          every unit on the shelf can be traced back to a real order and supplier.
        </p>

        {error && <p className="text-sm text-stamp-red">{error}</p>}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="border border-rule px-4 py-2 text-sm">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50"
          >
            {submitting ? 'Saving…' : isEdit ? 'Save changes' : 'Create product'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

function ConfirmDeactivateProductModal({
  product,
  onClose,
  onConfirmed,
}: {
  product: ProductOut
  onClose: () => void
  onConfirmed: () => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleConfirm() {
    setBusy(true)
    setError(null)
    try {
      await productsApi.deactivate(product.id)
      onConfirmed()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not deactivate this product.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Deactivate this product?" onClose={onClose}>
      <p className="text-sm text-ink-soft">
        <span className="font-medium text-ink">{product.name}</span> will no longer be sellable
        or orderable, but its full sales and stock history stays intact — nothing is deleted.
      </p>
      {error && <p className="mt-3 text-sm text-stamp-red">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className="border border-rule px-4 py-2 text-sm">
          Cancel
        </button>
        <button
          onClick={() => void handleConfirm()}
          disabled={busy}
          className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50"
        >
          {busy ? 'Deactivating…' : 'Deactivate'}
        </button>
      </div>
    </Modal>
  )
}
