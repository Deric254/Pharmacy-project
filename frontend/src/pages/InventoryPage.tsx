import { useEffect, useState, type FormEvent } from 'react'
import { inventoryApi, productsApi } from '../api/domain'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { useCurrencyFormatter } from '../lib/currency'
import { businessToday, fallbackTimezone } from '../lib/businessDate'
import { ApiError, downloadExport } from '../api/client'
import { Modal } from '../components/Modal'
import type {
  AdjustmentReason,
  BatchOut,
  ExpiringBatchOut,
  ImportRowError,
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
  const canRepriceBatches = hasPermission('batches.reprice')
  const canCorrectCost = hasPermission('batches.correct_cost')
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
        <AdjustmentPanel
          onAdjusted={() => setReloadKey((k) => k + 1)}
          canReprice={canRepriceBatches}
          canCorrectCost={canCorrectCost}
        />
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

function AdjustmentPanel({
  onAdjusted,
  canReprice,
  canCorrectCost,
}: {
  onAdjusted: () => void
  canReprice: boolean
  canCorrectCost: boolean
}) {
  const timezone = useConfigStore((s) => s.config?.timezone) ?? fallbackTimezone()
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

  async function updateBatchPrice(batchId: number, sellingPrice: number) {
    if (!selectedProduct) return
    setError(null)
    try {
      await productsApi.updateBatch(selectedProduct.id, batchId, sellingPrice)
      setBatches(await productsApi.batches(selectedProduct.id))
      onAdjusted()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update batch price.')
    }
  }

  async function correctBatchCost(batchId: number, costPrice: number, reason: string) {
    if (!selectedProduct) return
    setError(null)
    try {
      await productsApi.correctBatchCost(selectedProduct.id, batchId, costPrice, reason)
      setBatches(await productsApi.batches(selectedProduct.id))
      onAdjusted()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not correct batch cost.')
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
            {(() => {
              // Mirrors the backend's FEFO selection (expiry order,
              // qty_remaining > 0, not expired) closely enough for
              // display purposes -- batches already arrive sorted by
              // expiry_date from the API, matching select_batches_fefo.
              // The one thing this can't see is a batch mid stock-take
              // lock (BatchOut doesn't expose that), so this is "the
              // batch that will sell next once any active count on it
              // finishes" rather than a byte-for-byte guarantee.
              const today = businessToday(timezone)
              const fefoNextId = batches.find(
                (b) => b.qty_remaining > 0 && b.expiry_date >= today,
              )?.id
              return batches.map((batch) => (
                <BatchAdjustRow
                  // Local price/markup state is seeded once on mount
                  // (useState's initializer only runs once per key).
                  // Folding selling_price into the key means a real
                  // server-side price change -- from another user, or
                  // from a refetch after adjusting a *different*
                  // batch's quantity -- forces a clean remount instead
                  // of leaving this row's draft compared against a
                  // stale baseline, which could otherwise let "Save"
                  // silently overwrite someone else's concurrent edit.
                  key={`${batch.id}-${batch.selling_price ?? 'null'}-${batch.cost_price}`}
                  batch={batch}
                  onSubmit={submitAdjustment}
                  onPriceChange={updateBatchPrice}
                  onCostCorrect={correctBatchCost}
                  sellsNext={batch.id === fefoNextId}
                  canReprice={canReprice}
                  canCorrectCost={canCorrectCost}
                />
              ))
            })()}
            {batches.length === 0 && (
              <p className="text-sm text-ink-soft">No batches for this product yet.</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function BatchPriceRow({
  batch,
  sellsNext,
  onPriceChange,
  onCostCorrect,
  canReprice,
  canCorrectCost,
}: {
  batch: BatchOut
  sellsNext: boolean
  onPriceChange: (batchId: number, sellingPrice: number) => Promise<void>
  onCostCorrect: (batchId: number, costPrice: number, reason: string) => Promise<void>
  canReprice: boolean
  canCorrectCost: boolean
}) {
  const [sellingPrice, setSellingPrice] = useState(batch.selling_price ?? 0)
  const [markupPercent, setMarkupPercent] = useState(
    batch.cost_price > 0 ? ((sellingPrice - batch.cost_price) / batch.cost_price) * 100 : 0,
  )
  const [savingPrice, setSavingPrice] = useState(false)
  // Free to correct at any time, on any batch -- see BatchService.
  // correct_cost_price's own comment for why this is safe: a sale's
  // recorded cost is frozen the moment it happens (SaleItem.unit_cost),
  // so a correction here can only ever affect this batch's remaining
  // valuation and future sales, never a past, already-recorded one.
  const [correctingCost, setCorrectingCost] = useState(false)
  const [costDraft, setCostDraft] = useState(batch.cost_price)
  const [costReason, setCostReason] = useState('')
  const [savingCost, setSavingCost] = useState(false)

  return (
    <div className="ruled-row grid grid-cols-[1fr_auto] items-center gap-2 pb-2 text-sm">
      <div>
        <p>
          {batch.batch_number} <span className="text-ink-soft">· exp {batch.expiry_date}</span>
          {sellsNext ? (
            <span className="ml-2 border border-stamp-green-soft bg-stamp-green-soft/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-stamp-green">
              Sells next
            </span>
          ) : null}
        </p>
        <p className="figure text-ink-soft">{batch.qty_remaining} remaining</p>
        {canCorrectCost && !correctingCost && (
          <p className="figure text-ink-soft">
            Buy {batch.cost_price.toFixed(2)}{' '}
            <button
              onClick={() => {
                setCostDraft(batch.cost_price)
                setCostReason('')
                setCorrectingCost(true)
              }}
              className="ml-1 text-[10px] uppercase tracking-wide text-ink-soft underline"
            >
              Correct
            </button>
          </p>
        )}
        {!canCorrectCost && !correctingCost && (
          <p className="figure text-ink-soft">Buy {batch.cost_price.toFixed(2)}</p>
        )}
        {correctingCost && (
          <div className="mt-1 flex flex-col gap-1 border border-rule bg-paper p-2">
            <label className="text-[10px] uppercase tracking-wide text-ink-soft">
              Correct buying price
              <input
                type="number"
                min={0}
                step={0.01}
                value={costDraft}
                onChange={(e) => setCostDraft(Math.max(0, Number(e.target.value) || 0))}
                className="figure mt-0.5 w-24 border border-rule bg-paper px-2 py-1"
                aria-label="Corrected buying price"
              />
            </label>
            <label className="text-[10px] uppercase tracking-wide text-ink-soft">
              Reason (required)
              <input
                value={costReason}
                onChange={(e) => setCostReason(e.target.value)}
                placeholder="e.g. mistyped cost on receiving"
                className="mt-0.5 w-full border border-rule bg-paper px-2 py-1 text-xs"
              />
            </label>
            <div className="flex gap-1">
              <button
                disabled={savingCost || !costReason.trim() || costDraft === batch.cost_price}
                onClick={async () => {
                  setSavingCost(true)
                  await onCostCorrect(batch.id, costDraft, costReason.trim())
                  setSavingCost(false)
                  setCorrectingCost(false)
                }}
                className="border border-ink bg-ink px-2 py-1 text-xs text-paper disabled:opacity-40"
              >
                Save correction
              </button>
              <button
                onClick={() => setCorrectingCost(false)}
                className="border border-rule px-2 py-1 text-xs"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
        {!sellsNext && (
          <p className="mt-1 max-w-xs text-xs text-ink-soft">Sells after current batch (FEFO).</p>
        )}
        {sellsNext && (
          <p className="mt-1 text-xs text-stamp-green">Sells next.</p>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-1">
        <input
          type="number"
          min={0}
          step={0.01}
          value={sellingPrice}
          disabled={!canReprice}
          onChange={(e) => setSellingPrice(Math.max(0, Number(e.target.value) || 0))}
          className="figure w-20 border border-rule bg-paper px-2 py-1 disabled:opacity-40"
          aria-label="Batch selling price"
        />
        <input
          type="number"
          min={0}
          step={0.01}
          value={markupPercent || ''}
          disabled={!canReprice}
          onChange={(e) => {
            const nextMarkup = Math.max(0, Number(e.target.value) || 0)
            setMarkupPercent(nextMarkup)
            setSellingPrice(Math.round(batch.cost_price * (1 + nextMarkup / 100) * 100) / 100)
          }}
          className="figure w-20 border border-rule bg-paper px-2 py-1 disabled:opacity-40"
          aria-label="Batch markup percentage"
        />
        <button
          disabled={!canReprice || savingPrice || sellingPrice === (batch.selling_price ?? 0)}
          onClick={async () => {
            setSavingPrice(true)
            await onPriceChange(batch.id, sellingPrice)
            setSavingPrice(false)
          }}
          className="border border-rule px-2 py-1 text-xs disabled:opacity-40"
        >
          Save price
        </button>
      </div>
    </div>
  )
}

function BatchAdjustRow({
  batch,
  onSubmit,
  onPriceChange,
  onCostCorrect,
  sellsNext,
  canReprice,
  canCorrectCost,
}: {
  batch: BatchOut
  sellsNext: boolean
  onSubmit: (
    batchId: number,
    delta: number,
    reason: AdjustmentReason,
    notes: string,
  ) => Promise<void>
  onPriceChange: (batchId: number, sellingPrice: number) => Promise<void>
  onCostCorrect: (batchId: number, costPrice: number, reason: string) => Promise<void>
  canReprice: boolean
  canCorrectCost: boolean
}) {
  const [delta, setDelta] = useState(0)
  const [reason, setReason] = useState<AdjustmentReason>('MISCOUNT')
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)

  return (
    <div className="pb-2">
      <BatchPriceRow
        batch={batch}
        sellsNext={sellsNext}
        onPriceChange={onPriceChange}
        onCostCorrect={onCostCorrect}
        canReprice={canReprice}
        canCorrectCost={canCorrectCost}
      />
      <div className="ruled-row flex flex-wrap items-center justify-end gap-1 pb-2 pt-1 text-sm">
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

  if (error) return (
    <div className="mb-6 ledger-panel p-4">
      <h2 className="mb-2 text-xs uppercase tracking-wide text-ink-soft">Ledger reconciliation</h2>
      <p className="text-sm text-stamp-red">Reconciliation failed: {error}</p>
    </div>
  )

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
              <span>
                {issue.product_name}{' '}
                <span className="text-ink-soft">· batch {issue.batch_number}</span>
              </span>
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
  const formatCurrency = useCurrencyFormatter()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ProductOut[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [showImport, setShowImport] = useState(false)
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
        <div className="flex gap-2">
          <button
            onClick={() => void productsApi.downloadImportTemplate()}
            className="border border-rule px-3 py-1 text-xs text-ink-soft hover:border-brass"
          >
            Download template
          </button>
          <button
            onClick={() => setShowImport(true)}
            className="border border-rule px-3 py-1 text-xs text-ink-soft hover:border-brass"
          >
            Import from Excel
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="border border-ink bg-ink px-3 py-1 text-xs text-paper"
          >
            New product
          </button>
        </div>
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
            <div className="flex items-center gap-4">
              <span className="figure text-xs text-ink-soft" title="Units on hand">
                {p.total_qty_available} in stock
              </span>
              <span className="figure text-xs text-ink-soft" title="Selling price">
                Sell {formatCurrency(p.current_selling_price ?? p.default_selling_price)}
              </span>
              <span className="figure text-xs text-ink-soft" title="Buying price (cost)">
                Buy {p.current_cost !== null ? formatCurrency(p.current_cost) : '—'}
              </span>
              <span
                className={`figure text-xs ${
                  p.margin_percent !== null && p.margin_percent < 0
                    ? 'text-stamp-red'
                    : 'text-stamp-green'
                }`}
                title="Margin (profit as % of selling price)"
              >
                {p.margin_percent !== null ? `${p.margin_percent.toFixed(0)}% margin` : '—'}
              </span>
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
          key={editing.id}
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
      {showImport && (
        <ImportModal
          onClose={() => setShowImport(false)}
          onImported={() => {
            setShowImport(false)
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
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canRepriceBatches = hasPermission('batches.reprice')
  const canCorrectCost = hasPermission('batches.correct_cost')
  const timezone = useConfigStore((s) => s.config?.timezone) ?? fallbackTimezone()
  const formatCurrency = useCurrencyFormatter()
  const [name, setName] = useState(product?.name ?? '')
  const [barcode, setBarcode] = useState(product?.barcode ?? '')
  const [unit, setUnit] = useState(product?.unit ?? 'unit')
  const [reorderPoint, setReorderPoint] = useState(product?.reorder_point ?? 10)
  const [price, setPrice] = useState(product?.default_selling_price ?? 0)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [batches, setBatches] = useState<BatchOut[] | null>(null)
  const [batchesError, setBatchesError] = useState<string | null>(null)

  useEffect(() => {
    if (!isEdit) return
    productsApi
      .batches(product.id)
      .then(setBatches)
      .catch((err) => setBatchesError(err instanceof ApiError ? err.message : 'Could not load batches.'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEdit])

  async function refreshBatches() {
    if (!isEdit) return
    setBatches(await productsApi.batches(product.id))
  }

  async function handleBatchPriceChange(batchId: number, sellingPrice: number) {
    if (!isEdit) return
    try {
      await productsApi.updateBatch(product.id, batchId, sellingPrice)
      await refreshBatches()
    } catch (err) {
      setBatchesError(err instanceof ApiError ? err.message : 'Could not update batch price.')
    }
  }

  async function handleBatchCostCorrect(batchId: number, costPrice: number, reason: string) {
    if (!isEdit) return
    try {
      await productsApi.correctBatchCost(product.id, batchId, costPrice, reason)
      await refreshBatches()
    } catch (err) {
      setBatchesError(err instanceof ApiError ? err.message : 'Could not correct batch cost.')
    }
  }

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
              onChange={(e) => setReorderPoint(Math.max(0, Number(e.target.value) || 0))}
              className="figure mt-1 w-full border border-rule bg-paper px-3 py-2"
            />
          </label>
        </div>
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">
            Default selling price
          </span>
          <input
            type="number"
            min={0}
            step={0.01}
            value={price}
              onChange={(e) => setPrice(Math.max(0, Number(e.target.value) || 0))}
            className="figure mt-1 w-full border border-rule bg-paper px-3 py-2"
          />
          <p className="mt-1 text-xs text-ink-soft">
            {isEdit && product.current_selling_price !== null ? (
              <>
                POS charging <strong>{formatCurrency(product.current_selling_price)}</strong> (current batch)
              </>
            ) : (
              'Fallback price for new batches'
            )}
          </p>
        </label>

        {isEdit && product.current_cost !== null && (
          <label className="block">
            <span className="block text-xs uppercase tracking-wide text-ink-soft">
              Or set by markup % (cost is {product.current_cost.toFixed(2)})
            </span>
            <div className="mt-1 flex items-center gap-2">
              <input
                type="number"
                min={0}
                step={1}
                placeholder="e.g. 40"
                onChange={(e) => {
                  const markupPercent = Number(e.target.value)
                  const cost = product.current_cost ?? 0
                  setPrice(Math.round(cost * (1 + markupPercent / 100) * 100) / 100)
                }}
                className="figure w-24 border border-rule bg-paper px-3 py-2"
              />
              <span className="text-sm text-ink-soft">% → price becomes {price.toFixed(2)}</span>
            </div>
          </label>
        )}

        {isEdit && (
          <div className="border-t border-rule pt-3">
            <h3 className="mb-2 text-xs uppercase tracking-wide text-ink-soft">
              Batches -- prices save instantly, independent of "Save changes" below
            </h3>
            {batchesError && <p className="mb-2 text-sm text-stamp-red">{batchesError}</p>}
            {batches === null && !batchesError && (
              <p className="text-sm text-ink-soft">Loading batches…</p>
            )}
            {batches?.length === 0 && (
              <p className="text-sm text-ink-soft">No batches for this product yet.</p>
            )}
            {batches && batches.length > 0 && (
              <div className="space-y-2">
                {(() => {
                  // Same FEFO display-hint logic as the Adjustment
                  // panel's batch list -- see that panel's own comment
                  // for what this can't fully see (an active stock-take
                  // lock).
                  const today = businessToday(timezone)
                  const fefoNextId = batches.find(
                    (b) => b.qty_remaining > 0 && b.expiry_date >= today,
                  )?.id
                  return batches.map((batch) => (
                    <BatchPriceRow
                      key={`${batch.id}-${batch.selling_price ?? 'null'}-${batch.cost_price}`}
                      batch={batch}
                      sellsNext={batch.id === fefoNextId}
                      onPriceChange={handleBatchPriceChange}
                      onCostCorrect={handleBatchCostCorrect}
                      canReprice={canRepriceBatches}
                      canCorrectCost={canCorrectCost}
                    />
                  ))
                })()}
              </div>
            )}
          </div>
        )}

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
        <span className="font-medium text-ink">{product.name}</span> — history stays intact.
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

function ImportModal({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [rowErrors, setRowErrors] = useState<ImportRowError[] | null>(null)
  const [genericError, setGenericError] = useState<string | null>(null)
  const [successCount, setSuccessCount] = useState<number | null>(null)

  async function handleImport() {
    if (!file) return
    setSubmitting(true)
    setRowErrors(null)
    setGenericError(null)
    try {
      const result = await productsApi.importFromExcel(file)
      setSuccessCount(result.created)
    } catch (err) {
      if (err instanceof ApiError && err.body?.detail && typeof err.body.detail === 'object' && !Array.isArray(err.body.detail)) {
        setRowErrors(err.body.detail.errors ?? null)
        setGenericError(err.body.detail.message)
      } else {
        setGenericError(err instanceof ApiError ? err.message : 'Import failed.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  if (successCount !== null) {
    return (
      <Modal title="Import complete" onClose={onImported}>
        <p className="text-sm text-ink-soft">
          {successCount} product{successCount === 1 ? '' : 's'} imported successfully.
        </p>
        <div className="mt-4 flex justify-end">
          <button
            onClick={onImported}
            className="border border-ink bg-ink px-4 py-2 text-sm text-paper"
          >
            Done
          </button>
        </div>
      </Modal>
    )
  }

  return (
    <Modal title="Import products from Excel" onClose={onClose}>
      <p className="text-sm text-ink-soft">All-or-nothing import.</p>

      <label className="mt-3 block">
        <span className="block text-xs uppercase tracking-wide text-ink-soft">
          Choose file
        </span>
        <input
          type="file"
          accept=".xlsx"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null)
            setRowErrors(null)
            setGenericError(null)
          }}
          className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
        />
      </label>

      {genericError && (
        <p role="alert" className="mt-3 text-sm text-stamp-red">
          {genericError}
        </p>
      )}

      {rowErrors && rowErrors.length > 0 && (
        <div className="mt-3 max-h-64 overflow-y-auto border border-rule">
          <table className="w-full text-left text-sm">
            <thead className="bg-panel">
              <tr>
                <th className="px-2 py-1 font-medium">Row</th>
                <th className="px-2 py-1 font-medium">Field</th>
                <th className="px-2 py-1 font-medium">Problem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-rule">
              {rowErrors.map((e, idx) => (
                <tr key={idx}>
                  <td className="figure px-2 py-1">{e.row || '—'}</td>
                  <td className="px-2 py-1">{e.field}</td>
                  <td className="px-2 py-1">{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className="border border-rule px-4 py-2 text-sm">
          Cancel
        </button>
        <button
          onClick={() => void handleImport()}
          disabled={!file || submitting}
          className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50"
        >
          {submitting ? 'Importing…' : 'Import'}
        </button>
      </div>
    </Modal>
  )
}
