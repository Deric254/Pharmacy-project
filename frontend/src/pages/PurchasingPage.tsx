import { useEffect, useState, type FormEvent } from 'react'
import { productsApi, purchaseOrdersApi, suppliersApi } from '../api/domain'
import { useAuthStore } from '../auth/store'
import { useCurrencyFormatter } from '../lib/currency'
import { ApiError } from '../api/client'
import { Modal } from '../components/Modal'
import type {
  KanbanBoard,
  ProductOut,
  PurchaseOrderOut,
  PurchaseOrderStatus,
  ReceivingLine,
  ReceivingVarianceOut,
  SupplierOut,
} from '../types/api'

const COLUMNS: { status: PurchaseOrderStatus; label: string }[] = [
  { status: 'DRAFT', label: 'Draft' },
  { status: 'SENT', label: 'Sent' },
  { status: 'IN_TRANSIT', label: 'In Transit' },
  { status: 'RECEIVED', label: 'Received' },
  { status: 'RECONCILED', label: 'Reconciled' },
]

export function PurchasingPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canApprove = hasPermission('purchasing.approve_po')
  const canReceive = hasPermission('purchasing.receive_stock')

  const [board, setBoard] = useState<KanbanBoard | null>(null)
  const [suppliers, setSuppliers] = useState<SupplierOut[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [showSuppliers, setShowSuppliers] = useState(false)
  const [selectedPO, setSelectedPO] = useState<PurchaseOrderOut | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    Promise.all([purchaseOrdersApi.kanban(), suppliersApi.list()])
      .then(([kanban, supplierList]) => {
        if (cancelled) return
        setBoard(kanban)
        setSuppliers(supplierList)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load purchasing data.')
      })
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  function refresh() {
    setSelectedPO(null)
    setReloadKey((k) => k + 1)
  }

  return (
    <div className="p-6">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="font-display text-2xl text-ink">Purchasing</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setShowSuppliers(true)}
            className="border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass"
          >
            Suppliers
          </button>
          <button
            onClick={() => setShowCreate(true)}
            disabled={suppliers.length === 0}
            className="border border-ink bg-ink px-3 py-1.5 text-sm text-paper disabled:opacity-40"
          >
            New purchase order
          </button>
        </div>
      </header>

      {error && (
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}
      {suppliers.length === 0 && !error && (
        <p className="mb-4 text-sm text-ink-soft">
          Add a supplier first (Suppliers button above) before creating a purchase order.
        </p>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {COLUMNS.map((col) => (
          <div key={col.status} className="ledger-panel min-h-[200px] p-2">
            <h2 className="mb-2 px-1 text-xs uppercase tracking-wide text-ink-soft">
              {col.label} ({board?.[col.status]?.length ?? 0})
            </h2>
            <div className="space-y-2">
              {board?.[col.status]?.map((po) => (
                <button
                  key={po.id}
                  onClick={() => setSelectedPO(po)}
                  className="w-full border border-rule bg-paper p-2 text-left text-sm hover:border-brass"
                >
                  <p className="font-medium">PO #{po.id}</p>
                  <p className="text-xs text-ink-soft">
                    {supplierName(suppliers, po.supplier_id)} · {po.items.length} line(s)
                  </p>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {showCreate && (
        <CreatePOModal
          suppliers={suppliers}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false)
            refresh()
          }}
        />
      )}

      {showSuppliers && (
        <SuppliersModal
          suppliers={suppliers}
          canRecordPayment={canApprove}
          onClose={() => setShowSuppliers(false)}
          onChanged={refresh}
        />
      )}

      {selectedPO && (
        <PODetailModal
          po={selectedPO}
          supplierName={supplierName(suppliers, selectedPO.supplier_id)}
          canApprove={canApprove}
          canReceive={canReceive}
          onClose={() => setSelectedPO(null)}
          onChanged={refresh}
        />
      )}
    </div>
  )
}

function supplierName(suppliers: SupplierOut[], id: number): string {
  return suppliers.find((s) => s.id === id)?.name ?? `Supplier #${id}`
}

function CreatePOModal({
  suppliers,
  onClose,
  onCreated,
}: {
  suppliers: SupplierOut[]
  onClose: () => void
  onCreated: () => void
}) {
  const [supplierId, setSupplierId] = useState(suppliers[0]?.id ?? 0)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ProductOut[]>([])
  const [lines, setLines] = useState<{ product: ProductOut; qty: number; cost: number }[]>([])
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSearch(e: FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    try {
      setResults(await productsApi.list(query.trim()))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Search failed.')
    }
  }

  function addLine(product: ProductOut) {
    if (lines.some((l) => l.product.id === product.id)) return
    setLines((prev) => [...prev, { product, qty: 1, cost: product.default_selling_price / 2 }])
    setResults([])
    setQuery('')
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (lines.length === 0) {
      setError('Add at least one product line.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await purchaseOrdersApi.create({
        supplier_id: supplierId,
        notes: notes || null,
        items: lines.map((l) => ({
          product_id: l.product.id,
          quantity_ordered: l.qty,
          unit_cost_expected: l.cost,
        })),
      })
      onCreated()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create purchase order.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title="New purchase order" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">Supplier</span>
          <select
            value={supplierId}
            onChange={(e) => setSupplierId(Number(e.target.value))}
            className="mt-1 w-full border border-rule bg-paper px-3 py-2"
          >
            {suppliers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>

        <div>
          <span className="block text-xs uppercase tracking-wide text-ink-soft">Products</span>
          <form onSubmit={handleSearch} className="mt-1 flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search product"
              className="flex-1 border border-rule bg-paper px-3 py-2 text-sm"
            />
            <button type="submit" className="border border-rule px-3 py-2 text-sm">
              Search
            </button>
          </form>
          {results.length > 0 && (
            <ul className="mt-1 divide-y divide-rule border border-rule">
              {results.map((p) => (
                <li key={p.id}>
                  <button
                    type="button"
                    onClick={() => addLine(p)}
                    className="w-full px-3 py-2 text-left text-sm hover:bg-paper"
                  >
                    {p.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {lines.length > 0 && (
          <div className="space-y-2">
            {lines.map((line, idx) => (
              <div key={line.product.id} className="ruled-row grid grid-cols-[1fr_80px_100px] gap-2 pb-2 text-sm">
                <span className="truncate">{line.product.name}</span>
                <input
                  type="number"
                  min={1}
                  value={line.qty}
                  onChange={(e) => {
                    const qty = Number(e.target.value)
                    setLines((prev) => prev.map((l, i) => (i === idx ? { ...l, qty } : l)))
                  }}
                  className="figure border border-rule px-2 py-1"
                />
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={line.cost}
                  onChange={(e) => {
                    const cost = Number(e.target.value)
                    setLines((prev) => prev.map((l, i) => (i === idx ? { ...l, cost } : l)))
                  }}
                  className="figure border border-rule px-2 py-1"
                />
              </div>
            ))}
          </div>
        )}

        <input
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Notes (optional)"
          className="w-full border border-rule bg-paper px-3 py-2 text-sm"
        />

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
            {submitting ? 'Creating…' : 'Create draft'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

function SuppliersModal({
  suppliers,
  canRecordPayment,
  onClose,
  onChanged,
}: {
  suppliers: SupplierOut[]
  canRecordPayment: boolean
  onClose: () => void
  onChanged: () => void
}) {
  const formatCurrency = useCurrencyFormatter()
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [paymentAmounts, setPaymentAmounts] = useState<Record<number, string>>({})

  async function handleCreate(e: FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setError(null)
    try {
      await suppliersApi.create({ name: name.trim(), contact_phone: phone || null })
      setName('')
      setPhone('')
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add supplier.')
    }
  }

  async function handlePay(supplierId: number) {
    const amount = Number(paymentAmounts[supplierId])
    if (!amount || amount <= 0) return
    setError(null)
    try {
      await suppliersApi.recordPayment(supplierId, { amount })
      setPaymentAmounts((prev) => ({ ...prev, [supplierId]: '' }))
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not record payment.')
    }
  }

  return (
    <Modal title="Suppliers" onClose={onClose}>
      <form onSubmit={handleCreate} className="mb-4 flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Supplier name"
          className="flex-1 border border-rule bg-paper px-3 py-2 text-sm"
        />
        <input
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="Phone (optional)"
          className="w-36 border border-rule bg-paper px-3 py-2 text-sm"
        />
        <button type="submit" className="border border-ink bg-ink px-3 py-2 text-sm text-paper">
          Add
        </button>
      </form>

      {error && <p className="mb-3 text-sm text-stamp-red">{error}</p>}

      <ul className="divide-y divide-rule">
        {suppliers.map((s) => (
          <li key={s.id} className="flex items-center justify-between gap-2 py-2 text-sm">
            <div>
              <p className="font-medium">{s.name}</p>
              <p className="figure text-xs text-ink-soft">
                Owed: {formatCurrency(s.balance_owed)}
              </p>
            </div>
            {canRecordPayment && s.balance_owed > 0 && (
              <div className="flex items-center gap-1">
                <input
                  type="number"
                  min={0.01}
                  step={0.01}
                  value={paymentAmounts[s.id] ?? ''}
                  onChange={(e) =>
                    setPaymentAmounts((prev) => ({ ...prev, [s.id]: e.target.value }))
                  }
                  placeholder="amount"
                  className="figure w-24 border border-rule px-2 py-1 text-xs"
                />
                <button
                  onClick={() => void handlePay(s.id)}
                  className="border border-stamp-green px-2 py-1 text-xs text-stamp-green"
                >
                  Pay
                </button>
              </div>
            )}
          </li>
        ))}
        {suppliers.length === 0 && (
          <li className="py-3 text-sm text-ink-soft">No suppliers yet.</li>
        )}
      </ul>
    </Modal>
  )
}

function PODetailModal({
  po,
  supplierName,
  canApprove,
  canReceive,
  onClose,
  onChanged,
}: {
  po: PurchaseOrderOut
  supplierName: string
  canApprove: boolean
  canReceive: boolean
  onClose: () => void
  onChanged: () => void
}) {
  const formatCurrency = useCurrencyFormatter()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showReceive, setShowReceive] = useState(false)
  const [variances, setVariances] = useState<ReceivingVarianceOut[] | null>(null)

  async function transition(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Action failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={`Purchase order #${po.id}`} onClose={onClose}>
      <p className="mb-1 text-sm">
        Supplier: <span className="font-medium">{supplierName}</span>
      </p>
      <p className="mb-3 text-sm text-ink-soft">Status: {po.status}</p>

      <ul className="mb-4 divide-y divide-rule border border-rule">
        {po.items.map((item) => (
          <li key={item.id} className="flex justify-between px-3 py-2 text-sm">
            <span>Product #{item.product_id}</span>
            <span className="figure">
              {item.quantity_ordered} @ {formatCurrency(item.unit_cost_expected)}
              {item.quantity_received !== null && (
                <span className="text-ink-soft"> (recv {item.quantity_received})</span>
              )}
            </span>
          </li>
        ))}
      </ul>

      {error && <p className="mb-3 text-sm text-stamp-red">{error}</p>}
      {variances && variances.length > 0 && (
        <div className="mb-3 border border-stamp-red-soft bg-stamp-red-soft/30 p-2 text-sm text-stamp-red">
          <p className="font-medium">Receiving variance detected:</p>
          {variances.map((v) => (
            <p key={v.item_id}>
              Item #{v.item_id}: ordered {v.quantity_ordered}, received {v.quantity_received} (
              {v.variance > 0 ? '+' : ''}
              {v.variance})
            </p>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {po.status === 'DRAFT' && canApprove && (
          <ActionButton
            busy={busy}
            onClick={() => transition(() => purchaseOrdersApi.send(po.id))}
          >
            Send to supplier
          </ActionButton>
        )}
        {po.status === 'SENT' && (
          <ActionButton
            busy={busy}
            onClick={() => transition(() => purchaseOrdersApi.markInTransit(po.id))}
          >
            Mark in transit
          </ActionButton>
        )}
        {po.status === 'IN_TRANSIT' && canReceive && (
          <ActionButton busy={busy} onClick={() => setShowReceive(true)}>
            Receive shipment
          </ActionButton>
        )}
        {po.status === 'RECEIVED' && canApprove && (
          <ActionButton
            busy={busy}
            onClick={() =>
              transition(async () => {
                await purchaseOrdersApi.reconcile(po.id, {})
              })
            }
          >
            Reconcile
          </ActionButton>
        )}
      </div>

      {showReceive && (
        <ReceiveForm
          po={po}
          onClose={() => setShowReceive(false)}
          onReceived={(result) => {
            setVariances(result.variances)
            setShowReceive(false)
            onChanged()
          }}
        />
      )}
    </Modal>
  )
}

function ReceiveForm({
  po,
  onClose,
  onReceived,
}: {
  po: PurchaseOrderOut
  onClose: () => void
  onReceived: (result: { variances: ReceivingVarianceOut[] }) => void
}) {
  const [lines, setLines] = useState<Record<number, ReceivingLine>>(() =>
    Object.fromEntries(
      po.items.map((item) => [
        item.id,
        {
          item_id: item.id,
          batch_number: '',
          expiry_date: '',
          quantity_received: item.quantity_ordered,
          unit_cost_actual: item.unit_cost_expected,
        },
      ]),
    ),
  )
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const lineList = Object.values(lines)
    if (lineList.some((l) => !l.batch_number || !l.expiry_date)) {
      setError('Every line needs a batch number and expiry date.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const result = await purchaseOrdersApi.receive(po.id, { lines: lineList })
      onReceived(result)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not receive shipment.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mt-4 border-t border-rule pt-4">
      <p className="mb-2 text-xs uppercase tracking-wide text-ink-soft">
        Receiving -- enter what actually arrived
      </p>
      <form onSubmit={handleSubmit} className="space-y-3">
        {po.items.map((item) => (
          <div key={item.id} className="grid grid-cols-2 gap-2 border border-rule p-2 text-sm">
            <p className="col-span-2">
              Product #{item.product_id} (ordered {item.quantity_ordered})
            </p>
            <input
              placeholder="Batch number"
              value={lines[item.id].batch_number}
              onChange={(e) =>
                setLines((prev) => ({
                  ...prev,
                  [item.id]: { ...prev[item.id], batch_number: e.target.value },
                }))
              }
              className="border border-rule px-2 py-1"
            />
            <input
              type="date"
              value={lines[item.id].expiry_date}
              onChange={(e) =>
                setLines((prev) => ({
                  ...prev,
                  [item.id]: { ...prev[item.id], expiry_date: e.target.value },
                }))
              }
              className="border border-rule px-2 py-1"
            />
            <input
              type="number"
              min={0}
              value={lines[item.id].quantity_received}
              onChange={(e) =>
                setLines((prev) => ({
                  ...prev,
                  [item.id]: {
                    ...prev[item.id],
                    quantity_received: Number(e.target.value),
                  },
                }))
              }
              placeholder="Qty received"
              className="figure border border-rule px-2 py-1"
            />
            <input
              type="number"
              min={0}
              step={0.01}
              value={lines[item.id].unit_cost_actual}
              onChange={(e) =>
                setLines((prev) => ({
                  ...prev,
                  [item.id]: {
                    ...prev[item.id],
                    unit_cost_actual: Number(e.target.value),
                  },
                }))
              }
              placeholder="Actual unit cost"
              className="figure border border-rule px-2 py-1"
            />
          </div>
        ))}

        {error && <p className="text-sm text-stamp-red">{error}</p>}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="border border-rule px-4 py-2 text-sm">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="border border-stamp-green bg-stamp-green px-4 py-2 text-sm text-paper disabled:opacity-50"
          >
            {submitting ? 'Receiving…' : 'Confirm receipt'}
          </button>
        </div>
      </form>
    </div>
  )
}

function ActionButton({
  busy,
  onClick,
  children,
}: {
  busy: boolean
  onClick: () => void
  children: string
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="border border-ink bg-ink px-3 py-1.5 text-sm text-paper disabled:opacity-50"
    >
      {children}
    </button>
  )
}
