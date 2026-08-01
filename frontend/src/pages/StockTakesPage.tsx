import { useEffect, useState, type FormEvent } from 'react'
import { stockTakesApi } from '../api/domain'
import { useAuthStore } from '../auth/store'
import { ApiError } from '../api/client'
import type { AdjustmentReason, StockTakeItemOut, StockTakeOut } from '../types/api'

const REASONS: AdjustmentReason[] = [
  'MISCOUNT',
  'DAMAGED',
  'EXPIRED',
  'THEFT_OR_LOSS',
  'DATA_ENTRY_ERROR',
  'OTHER',
]

export function StockTakesPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canApproveVariance = hasPermission('stocktake.approve_variance')

  const [stockTakes, setStockTakes] = useState<StockTakeOut[]>([])
  const [selected, setSelected] = useState<StockTakeOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    stockTakesApi
      .list()
      .then((list) => {
        if (cancelled) return
        setStockTakes(list)
        // Keep the open detail view in sync with the freshly reloaded list.
        setSelected((prev) => (prev ? (list.find((s) => s.id === prev.id) ?? null) : null))
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load stock takes.')
      })
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  const refresh = () => setReloadKey((k) => k + 1)

  return (
    <div className="p-6">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="font-display text-2xl text-ink">Stock Takes</h1>
      </header>

      {error && (
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      {selected ? (
        <StockTakeDetail
          stockTake={selected}
          canApproveVariance={canApproveVariance}
          onBack={() => setSelected(null)}
          onChanged={refresh}
        />
      ) : (
        <StockTakeList
          stockTakes={stockTakes}
          onSelect={setSelected}
          onCreated={refresh}
        />
      )}
    </div>
  )
}

function StockTakeList({
  stockTakes,
  onSelect,
  onCreated,
}: {
  stockTakes: StockTakeOut[]
  onSelect: (st: StockTakeOut) => void
  onCreated: () => void
}) {
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function startFullCount() {
    setStarting(true)
    setError(null)
    try {
      await stockTakesApi.initiate({})
      onCreated()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start a stock take.')
    } finally {
      setStarting(false)
    }
  }

  const open = stockTakes.filter((s) => s.status === 'OPEN')
  const closed = stockTakes.filter((s) => s.status === 'CLOSED')

  return (
    <div>
      <button
        onClick={() => void startFullCount()}
        disabled={starting || open.length > 0}
        className="mb-4 border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-40"
      >
        {starting ? 'Starting…' : 'Start a full stock take'}
      </button>
      {open.length > 0 && (
        <p className="mb-4 text-sm text-ink-soft">
          Close the open stock take below before starting a new one.
        </p>
      )}
      {error && <p className="mb-4 text-sm text-stamp-red">{error}</p>}

      {open.length > 0 && (
        <section className="mb-6">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-ink-soft">In progress</h2>
          <div className="space-y-2">
            {open.map((st) => (
              <StockTakeRow key={st.id} stockTake={st} onSelect={onSelect} />
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="mb-2 text-xs uppercase tracking-wide text-ink-soft">History</h2>
        <div className="space-y-2">
          {closed.map((st) => (
            <StockTakeRow key={st.id} stockTake={st} onSelect={onSelect} />
          ))}
          {closed.length === 0 && <p className="text-sm text-ink-soft">No closed stock takes yet.</p>}
        </div>
      </section>
    </div>
  )
}

function StockTakeRow({
  stockTake,
  onSelect,
}: {
  stockTake: StockTakeOut
  onSelect: (st: StockTakeOut) => void
}) {
  const counted = stockTake.items.filter((i) => i.physical_qty !== null).length
  return (
    <button
      onClick={() => onSelect(stockTake)}
      className="ledger-panel flex w-full items-center justify-between p-3 text-left text-sm hover:border-brass"
    >
      <span>
        Stock take #{stockTake.id}{' '}
        <span className="text-ink-soft">
          · started {new Date(stockTake.started_at).toLocaleDateString()}
        </span>
      </span>
      <span className="figure text-ink-soft">
        {counted}/{stockTake.items.length} counted
      </span>
    </button>
  )
}

function StockTakeDetail({
  stockTake,
  canApproveVariance,
  onBack,
  onChanged,
}: {
  stockTake: StockTakeOut
  canApproveVariance: boolean
  onBack: () => void
  onChanged: () => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [closing, setClosing] = useState(false)
  const [uploadingCounts, setUploadingCounts] = useState(false)
  const [uploadResult, setUploadResult] = useState<string | null>(null)

  const unapproved = stockTake.items.filter(
    (i) => i.variance !== null && i.variance !== 0 && i.approved_at === null,
  )
  const allCounted = stockTake.items.every((i) => i.physical_qty !== null)

  async function handleClose() {
    setClosing(true)
    setError(null)
    try {
      await stockTakesApi.close(stockTake.id)
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not close stock take.')
    } finally {
      setClosing(false)
    }
  }

  async function handleUploadCounts(file: File) {
    setUploadingCounts(true)
    setError(null)
    setUploadResult(null)
    try {
      const result = await stockTakesApi.importCounts(stockTake.id, file)
      setUploadResult(
        result.status === 'CLOSED'
          ? 'Every count applied and the stock take is now closed.'
          : 'Counts applied. Some items still need a manager to approve a large variance.',
      )
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not import that file.')
    } finally {
      setUploadingCounts(false)
    }
  }

  return (
    <div>
      <button onClick={onBack} className="mb-4 text-sm text-ink-soft underline">
        ← Back to list
      </button>

      <div className="mb-4 flex items-center justify-between">
        <h2 className="font-display text-xl text-ink">Stock take #{stockTake.id}</h2>
        <div className="flex items-center gap-2">
          {stockTake.status === 'OPEN' && (
            <>
              <button
                onClick={() => void stockTakesApi.downloadCountTemplate(stockTake.id)}
                className="border border-rule px-3 py-2 text-sm text-ink-soft hover:border-brass"
              >
                Download count sheet
              </button>
              <label className="cursor-pointer border border-rule px-3 py-2 text-sm text-ink-soft hover:border-brass">
                {uploadingCounts ? 'Uploading…' : 'Upload counted sheet'}
                <input
                  type="file"
                  accept=".xlsx"
                  disabled={uploadingCounts}
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) void handleUploadCounts(file)
                    e.target.value = ''
                  }}
                  className="hidden"
                />
              </label>
            </>
          )}
          {stockTake.status === 'OPEN' && (
            <button
              onClick={() => void handleClose()}
              disabled={closing || unapproved.length > 0}
              className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-40"
            >
              {closing ? 'Closing…' : 'Close stock take'}
            </button>
          )}
          {stockTake.status === 'CLOSED' && (
            <span className="text-sm text-stamp-green">Closed</span>
          )}
        </div>
      </div>

      {uploadResult && (
        <p className="mb-4 border border-stamp-green-soft bg-stamp-green-soft/40 px-3 py-2 text-sm text-stamp-green">
          {uploadResult}
        </p>
      )}

      {unapproved.length > 0 && (
        <p className="mb-4 text-sm text-ink-soft">
          {unapproved.length} item(s) have a variance still needing manager approval before this
          can close.
        </p>
      )}
      {!allCounted && (
        <p className="mb-4 text-sm text-ink-soft">Not every item has been counted yet.</p>
      )}
      {error && <p className="mb-4 text-sm text-stamp-red">{error}</p>}

      <div className="divide-y divide-rule border border-rule">
        {stockTake.items.map((item) => (
          <StockTakeItemRow
            key={item.id}
            stockTakeId={stockTake.id}
            item={item}
            readOnly={stockTake.status === 'CLOSED'}
            canApproveVariance={canApproveVariance}
            onChanged={onChanged}
          />
        ))}
      </div>
    </div>
  )
}

function StockTakeItemRow({
  stockTakeId,
  item,
  readOnly,
  canApproveVariance,
  onChanged,
}: {
  stockTakeId: number
  item: StockTakeItemOut
  readOnly: boolean
  canApproveVariance: boolean
  onChanged: () => void
}) {
  const [physicalQty, setPhysicalQty] = useState('')
  const [reason, setReason] = useState<AdjustmentReason>('MISCOUNT')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const needsApproval = item.variance !== null && item.variance !== 0 && item.approved_at === null

  async function handleSubmitCount(e: FormEvent) {
    e.preventDefault()
    if (physicalQty === '') return
    setBusy(true)
    setError(null)
    try {
      await stockTakesApi.submitCount(stockTakeId, item.id, {
        physical_qty: Number(physicalQty),
        reason,
      })
      setPhysicalQty('')
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not submit count.')
    } finally {
      setBusy(false)
    }
  }

  async function handleApprove() {
    setBusy(true)
    setError(null)
    try {
      await stockTakesApi.approveVariance(stockTakeId, item.id)
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not approve variance.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="p-3 text-sm">
      <div className="flex items-center justify-between">
        <span>
          {item.product_name}{' '}
          <span className="text-ink-soft">· batch {item.batch_number}</span>
        </span>
        <span className="figure text-ink-soft">Expected: {item.expected_qty}</span>
      </div>

      {item.physical_qty !== null ? (
        <div className="mt-1 flex items-center justify-between">
          <span className="figure">
            Counted: {item.physical_qty}{' '}
            {item.variance !== 0 && (
              <span className={item.variance !== null && item.variance < 0 ? 'text-stamp-red' : 'text-stamp-green'}>
                ({item.variance !== null && item.variance > 0 ? '+' : ''}
                {item.variance})
              </span>
            )}
          </span>
          {needsApproval && canApproveVariance && (
            <button
              onClick={() => void handleApprove()}
              disabled={busy}
              className="border border-stamp-green px-2 py-1 text-xs text-stamp-green disabled:opacity-50"
            >
              Approve variance
            </button>
          )}
          {needsApproval && !canApproveVariance && (
            <span className="text-xs text-ink-soft">Awaiting manager approval</span>
          )}
          {!needsApproval && item.variance !== 0 && (
            <span className="text-xs text-stamp-green">Applied to stock</span>
          )}
        </div>
      ) : !readOnly ? (
        <form onSubmit={handleSubmitCount} className="mt-2 flex items-center gap-2">
          <input
            type="number"
            min={0}
            value={physicalQty}
            onChange={(e) => setPhysicalQty(e.target.value)}
            placeholder="Physical count"
            required
            className="figure w-28 border border-rule px-2 py-1"
          />
          <select
            value={reason}
            onChange={(e) => setReason(e.target.value as AdjustmentReason)}
            className="border border-rule px-1 py-1 text-xs"
          >
            {REASONS.map((r) => (
              <option key={r} value={r}>
                {r.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={busy}
            className="border border-ink bg-ink px-3 py-1 text-xs text-paper disabled:opacity-50"
          >
            Submit
          </button>
        </form>
      ) : (
        <p className="mt-1 text-xs text-ink-soft">Not counted before close.</p>
      )}
      {error && <p className="mt-1 text-xs text-stamp-red">{error}</p>}
    </div>
  )
}
