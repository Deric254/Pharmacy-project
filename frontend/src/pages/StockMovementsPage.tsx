import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { inventoryApi } from '../api/domain'
import { ApiError } from '../api/client'
import type { StockMovementOut } from '../types/api'

const PAGE_SIZE = 25

const MOVEMENT_TYPES = ['PURCHASE', 'SALE', 'ADJUSTMENT', 'RETURN'] as const

export function StockMovementsPage() {
  // product_id / batch_id arrive via query string so other pages (e.g.
  // a future "History" link on a batch row) can deep-link straight into
  // a filtered view without this page needing to know who's linking in.
  const [searchParams, setSearchParams] = useSearchParams()
  const productIdParam = searchParams.get('product_id') ?? ''
  const batchIdParam = searchParams.get('batch_id') ?? ''

  const [entries, setEntries] = useState<StockMovementOut[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [movementType, setMovementType] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    inventoryApi
      .movements({
        product_id: productIdParam ? Number(productIdParam) : undefined,
        batch_id: batchIdParam ? Number(batchIdParam) : undefined,
        movement_type: movementType || undefined,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        limit: PAGE_SIZE,
        offset,
      })
      .then((page) => {
        if (cancelled) return
        setEntries(page.entries)
        setTotal(page.total)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load stock movement history.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [productIdParam, batchIdParam, movementType, startDate, endDate, offset])

  function applyFilter(setter: (v: string) => void, value: string) {
    setter(value)
    setOffset(0)
  }

  function clearScope() {
    setSearchParams({})
    setOffset(0)
  }

  const hasNextPage = offset + PAGE_SIZE < total
  const hasPrevPage = offset > 0
  const scoped = Boolean(productIdParam || batchIdParam)

  return (
    <div className="p-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="font-display text-2xl text-ink">Stock Movement History</h1>
      </div>
      <p className="mb-4 text-sm text-ink-soft">
        The complete, append-only ledger of every stock change -- purchases, sales, adjustments,
        and returns -- with who made it and when.
      </p>

      {scoped && (
        <div className="mb-4 flex items-center gap-2 border border-rule bg-paper px-3 py-2 text-sm">
          <span>
            Filtered to {productIdParam && `product #${productIdParam}`}
            {productIdParam && batchIdParam && ' · '}
            {batchIdParam && `batch #${batchIdParam}`}
          </span>
          <button onClick={clearScope} className="text-xs text-ink-soft underline">
            Clear
          </button>
        </div>
      )}

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">Type</span>
          <select
            value={movementType}
            onChange={(e) => applyFilter(setMovementType, e.target.value)}
            className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
          >
            <option value="">All</option>
            {MOVEMENT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">From</span>
          <input
            type="date"
            value={startDate}
            onChange={(e) => applyFilter(setStartDate, e.target.value)}
            className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
          />
        </label>
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">To</span>
          <input
            type="date"
            value={endDate}
            onChange={(e) => applyFilter(setEndDate, e.target.value)}
            className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
          />
        </label>
      </div>

      {error && (
        <p
          role="alert"
          className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red"
        >
          {error}
        </p>
      )}

      <div className="ledger-panel divide-y divide-rule">
        {entries.map((entry) => (
          <div key={entry.id} className="px-3 py-3 text-sm">
            <div className="flex items-center justify-between">
              <p className="font-medium">
                {entry.movement_type}{' '}
                <span className="text-xs font-normal text-ink-soft">
                  {entry.product_name} · batch {entry.batch_number}
                </span>
              </p>
              <span className="figure text-xs text-ink-soft">
                {new Date(entry.created_at).toLocaleString()}
              </span>
            </div>
            <p className="text-xs text-ink-soft">
              <span className={`figure ${entry.quantity_delta < 0 ? 'text-stamp-red' : 'text-stamp-green'}`}>
                {entry.quantity_delta > 0 ? '+' : ''}
                {entry.quantity_delta}
              </span>
              {' · '}
              {entry.created_by_name ?? 'System'}
              {entry.reference && ` · ref ${entry.reference}`}
            </p>
            {entry.reason && <p className="mt-1 text-xs text-ink-soft">{entry.reason}</p>}
          </div>
        ))}
        {entries.length === 0 && !loading && (
          <p className="px-3 py-4 text-sm text-ink-soft">No matching stock movements.</p>
        )}
      </div>

      <div className="mt-4 flex items-center justify-between text-sm text-ink-soft">
        <span className="figure">
          {total === 0 ? '0' : `${offset + 1}-${Math.min(offset + PAGE_SIZE, total)}`} of {total}
        </span>
        <div className="flex gap-2">
          <button
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            disabled={!hasPrevPage}
            className="border border-rule px-3 py-1 disabled:opacity-40"
          >
            Previous
          </button>
          <button
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            disabled={!hasNextPage}
            className="border border-rule px-3 py-1 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  )
}
