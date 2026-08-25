import { useEffect, useRef, useState, type FormEvent } from 'react'
import { productsApi, purchaseOrdersApi, suppliersApi } from '../api/domain'
import { useAuthStore } from '../auth/store'
import { useCurrencyFormatter } from '../lib/currency'
import { ApiError, downloadExport } from '../api/client'
import { Modal } from '../components/Modal'
import type {
  ImportRowError,
  ProductOut,
  PurchaseOrderOut,
  SupplierOut,
} from '../types/api'

export function PurchasingPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canApprove = hasPermission('purchasing.approve_po')

  const [orders, setOrders] = useState<PurchaseOrderOut[]>([])
  const [suppliers, setSuppliers] = useState<SupplierOut[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showQuickPurchase, setShowQuickPurchase] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [showSuppliers, setShowSuppliers] = useState(false)
  const [selectedPO, setSelectedPO] = useState<PurchaseOrderOut | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    Promise.all([purchaseOrdersApi.list(), suppliersApi.list()])
      .then(([orderList, supplierList]) => {
        if (cancelled) return
        setOrders(orderList)
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

  const allPurchases = [...orders].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

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
            onClick={() => void purchaseOrdersApi.downloadImportTemplate()}
            className="border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass"
          >
            Download template
          </button>
          <button
            onClick={() => setShowImport(true)}
            disabled={suppliers.length === 0}
            className="border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass disabled:opacity-40"
          >
            Import from Excel
          </button>
          <button
            onClick={() => setShowQuickPurchase(true)}
            disabled={suppliers.length === 0}
            className="border border-ink bg-ink px-3 py-1.5 text-sm text-paper disabled:opacity-40"
          >
            Receive stock
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

      <div className="ledger-panel">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-rule">
            <tr>
              <th className="px-3 py-2 font-medium">Purchase</th>
              <th className="px-3 py-2 font-medium">Supplier</th>
              <th className="px-3 py-2 font-medium">Items</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 font-medium">When</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-rule">
            {allPurchases.map((po) => (
              <tr
                key={po.id}
                onClick={() => setSelectedPO(po)}
                className="cursor-pointer hover:bg-panel"
              >
                <td className="figure px-3 py-2">#{po.id}</td>
                <td className="px-3 py-2">{supplierName(suppliers, po.supplier_id)}</td>
                <td className="figure px-3 py-2">{po.items.length}</td>
                <td className="px-3 py-2">
                  <span
                    className={`px-2 py-0.5 text-xs uppercase tracking-wide ${
                      po.status === 'RECONCILED'
                        ? 'bg-stamp-green-soft/40 text-stamp-green'
                        : po.status === 'RECEIVED'
                          ? 'bg-brass-soft/30 text-ink'
                          : 'text-ink-soft'
                    }`}
                  >
                    {po.status}
                  </span>
                </td>
                <td className="px-3 py-2 text-ink-soft">
                  {new Date(po.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
            {allPurchases.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-sm text-ink-soft">
                  No purchases yet. Use "Receive stock" above to log your first delivery.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {showQuickPurchase && (
        <QuickPurchaseModal
          suppliers={suppliers}
          onClose={() => setShowQuickPurchase(false)}
          onReceived={() => {
            setShowQuickPurchase(false)
            refresh()
          }}
        />
      )}

      {showImport && (
        <ImportPOModal
          suppliers={suppliers}
          onClose={() => setShowImport(false)}
          onImported={() => {
            setShowImport(false)
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
          onClose={() => setSelectedPO(null)}
        />
      )}
    </div>
  )
}

function supplierName(suppliers: SupplierOut[], id: number): string {
  return suppliers.find((s) => s.id === id)?.name ?? `Supplier #${id}`
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
      <div className="mb-4 flex justify-end">
        <button
          onClick={() => void downloadExport('/suppliers', {}, 'excel')}
          className="border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass"
        >
          Export to Excel
        </button>
      </div>
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
  onClose,
}: {
  po: PurchaseOrderOut
  supplierName: string
  onClose: () => void
}) {
  const formatCurrency = useCurrencyFormatter()

  return (
    <Modal title={`Purchase order #${po.id}`} onClose={onClose}>
      <p className="mb-1 text-sm">
        Supplier: <span className="font-medium">{supplierName}</span>
      </p>
      <p className="mb-3 text-sm text-ink-soft">
        Received {new Date(po.received_at ?? po.created_at).toLocaleString()}
      </p>

      <ul className="mb-4 divide-y divide-rule border border-rule">
        {po.items.map((item) => (
          <li key={item.id} className="flex justify-between px-3 py-2 text-sm">
            <span>{item.product_name}</span>
            <span className="figure">
              {item.quantity_received ?? item.quantity_ordered} @{' '}
              {formatCurrency(item.unit_cost_actual ?? item.unit_cost_expected)}
            </span>
          </li>
        ))}
      </ul>

      <div className="flex justify-end">
        <button onClick={onClose} className="border border-rule px-4 py-2 text-sm">
          Close
        </button>
      </div>
    </Modal>
  )
}

function ImportPOModal({
  suppliers,
  onClose,
  onImported,
}: {
  suppliers: SupplierOut[]
  onClose: () => void
  onImported: () => void
}) {
  const [supplierId, setSupplierId] = useState<number | ''>(suppliers[0]?.id ?? '')
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [rowErrors, setRowErrors] = useState<ImportRowError[] | null>(null)
  const [genericError, setGenericError] = useState<string | null>(null)
  const [imported, setImported] = useState<PurchaseOrderOut | null>(null)
  const submittingRef = useRef(false)

  async function handleImport() {
    if (!file || !supplierId) return
    if (submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setRowErrors(null)
    setGenericError(null)
    try {
      const result = await purchaseOrdersApi.importFromExcel(file, supplierId)
      setImported(result)
    } catch (err) {
      if (
        err instanceof ApiError &&
        err.body?.detail &&
        typeof err.body.detail === 'object' &&
        !Array.isArray(err.body.detail)
      ) {
        setRowErrors(err.body.detail.errors ?? null)
        setGenericError(err.body.detail.message)
      } else {
        setGenericError(err instanceof ApiError ? err.message : 'Import failed.')
      }
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  if (imported) {
    return (
      <Modal title="Stock received" onClose={onImported}>
        <p className="text-sm text-ink-soft">
          Received {imported.items.length} line item
          {imported.items.length === 1 ? '' : 's'} — already in your inventory.
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
    <Modal title="Receive stock from Excel" onClose={onClose}>
      <p className="text-sm text-ink-soft">
        Product names must match your catalog exactly (not case-sensitive). Everything in the
        file lands in your inventory immediately — if anything doesn't match or is invalid,
        nothing is received at all, never a partial delivery.
      </p>

      <label className="mt-3 block">
        <span className="block text-xs uppercase tracking-wide text-ink-soft">Supplier</span>
        <select
          value={supplierId}
          onChange={(e) => setSupplierId(Number(e.target.value))}
          className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
        >
          {suppliers.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>

      <label className="mt-3 block">
        <span className="block text-xs uppercase tracking-wide text-ink-soft">Choose file</span>
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
          disabled={!file || !supplierId || submitting}
          className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50"
        >
          {submitting ? 'Importing…' : 'Import'}
        </button>
      </div>
    </Modal>
  )
}

interface QuickPurchaseLineDraft {
  productId: number | ''
  productName: string
  quantity: number
  batchNumber: string
  expiryDate: string
  unitCost: number
  sellingPrice: number
}

function generateSessionBatchNumber(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const date = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
  const time = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  return `BATCH-${date}-${time}`
}

function QuickPurchaseModal({
  suppliers,
  onClose,
  onReceived,
}: {
  suppliers: SupplierOut[]
  onClose: () => void
  onReceived: () => void
}) {
  const [supplierId, setSupplierId] = useState<number | ''>(suppliers[0]?.id ?? '')
  // Computed once per modal open (lazy initializer), not on every
  // render -- every line in this same delivery shares one batch
  // identifier by default, matching "received together, same batch",
  // while staying fully editable per line for anyone who wants a
  // different one for a specific product.
  const [sessionBatchNumber] = useState(generateSessionBatchNumber)
  const [lines, setLines] = useState<QuickPurchaseLineDraft[]>([
    {
      productId: '',
      productName: '',
      quantity: 1,
      batchNumber: sessionBatchNumber,
      expiryDate: '',
      unitCost: 0,
      sellingPrice: 0,
    },
  ])
  const [productResults, setProductResults] = useState<ProductOut[]>([])
  const [activeSearchIndex, setActiveSearchIndex] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)

  useEffect(() => {
    if (activeSearchIndex === null || !query.trim()) {
      setProductResults([])
      return
    }
    const timer = setTimeout(() => {
      void productsApi.list(query.trim()).then(setProductResults)
    }, 250)
    return () => clearTimeout(timer)
  }, [query, activeSearchIndex])

  function updateLine(index: number, patch: Partial<QuickPurchaseLineDraft>) {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)))
  }

  function addLine() {
    setLines((prev) => [
      ...prev,
      {
        productId: '',
        productName: '',
        quantity: 1,
        batchNumber: sessionBatchNumber,
        expiryDate: '',
        unitCost: 0,
        sellingPrice: 0,
      },
    ])
  }

  function removeLine(index: number) {
    setLines((prev) => prev.filter((_, i) => i !== index))
  }

  async function handleSubmit() {
    if (submittingRef.current) return
    if (!supplierId) {
      setError('Choose a supplier.')
      return
    }
    const validLines = lines.filter((l) => l.productId !== '')
    if (validLines.length === 0) {
      setError('Add at least one product.')
      return
    }
    if (validLines.some((l) => !l.batchNumber.trim() || !l.expiryDate)) {
      setError('Every line needs a batch number and expiry date.')
      return
    }
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    try {
      await purchaseOrdersApi.quickPurchase({
        supplier_id: supplierId,
        lines: validLines.map((l) => ({
          product_id: l.productId as number,
          quantity: l.quantity,
          batch_number: l.batchNumber.trim(),
          expiry_date: l.expiryDate,
          unit_cost: l.unitCost,
          selling_price: l.sellingPrice,
        })),
      })
      onReceived()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not record this delivery.')
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  return (
    <Modal title="Receive stock" onClose={onClose}>
      <p className="mb-3 text-sm text-ink-soft">
        For stock that's already here — no advance order, no ceremony. Enter what arrived and
        it's in your inventory immediately.
      </p>

      <label className="mb-3 block">
        <span className="block text-xs uppercase tracking-wide text-ink-soft">Supplier</span>
        <select
          value={supplierId}
          onChange={(e) => setSupplierId(Number(e.target.value))}
          className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
        >
          {suppliers.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>

      <div className="space-y-3">
        {lines.map((line, index) => (
          <div key={index} className="relative border border-rule p-3">
            <label className="block">
              <span className="block text-xs uppercase tracking-wide text-ink-soft">
                Product
              </span>
              <input
                value={line.productName}
                onChange={(e) => {
                  updateLine(index, { productName: e.target.value, productId: '' })
                  setQuery(e.target.value)
                  setActiveSearchIndex(index)
                }}
                onFocus={() => setActiveSearchIndex(index)}
                placeholder="Search by name"
                className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
              />
              {activeSearchIndex === index && productResults.length > 0 && (
                <ul className="mt-1 max-h-40 overflow-y-auto border border-rule bg-paper">
                  {productResults.map((p) => (
                    <li key={p.id}>
                      <button
                        type="button"
                        onClick={() => {
                          updateLine(index, { productId: p.id, productName: p.name })
                          setActiveSearchIndex(null)
                          setProductResults([])
                        }}
                        className="block w-full px-3 py-1.5 text-left text-sm hover:bg-panel"
                      >
                        {p.name}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </label>

            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <label className="block">
                <span className="block text-xs uppercase tracking-wide text-ink-soft">Qty</span>
                <input
                  type="number"
                  min={1}
                  value={line.quantity}
                  onChange={(e) => updateLine(index, { quantity: Number(e.target.value) })}
                  className="figure mt-1 w-full border border-rule bg-paper px-2 py-1.5 text-sm"
                />
              </label>
              <label className="block">
                <span className="block text-xs uppercase tracking-wide text-ink-soft">
                  Batch #
                </span>
                <input
                  value={line.batchNumber}
                  onChange={(e) => updateLine(index, { batchNumber: e.target.value })}
                  className="mt-1 w-full border border-rule bg-paper px-2 py-1.5 text-sm"
                />
              </label>
              <label className="block">
                <span className="block text-xs uppercase tracking-wide text-ink-soft">
                  Expiry
                </span>
                <input
                  type="date"
                  value={line.expiryDate}
                  onChange={(e) => updateLine(index, { expiryDate: e.target.value })}
                  className="mt-1 w-full border border-rule bg-paper px-2 py-1.5 text-sm"
                />
              </label>
              <label className="block">
                <span className="block text-xs uppercase tracking-wide text-ink-soft">
                  Unit cost
                </span>
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={line.unitCost}
                  onChange={(e) => updateLine(index, { unitCost: Number(e.target.value) })}
                  className="figure mt-1 w-full border border-rule bg-paper px-2 py-1.5 text-sm"
                />
              </label>
              <label className="block">
                <span className="block text-xs uppercase tracking-wide text-ink-soft">
                  Selling price
                </span>
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={line.sellingPrice}
                  onChange={(e) => updateLine(index, { sellingPrice: Number(e.target.value) })}
                  className="figure mt-1 w-full border border-rule bg-paper px-2 py-1.5 text-sm"
                />
              </label>
            </div>

            {lines.length > 1 && (
              <button
                type="button"
                onClick={() => removeLine(index)}
                className="mt-2 text-xs text-stamp-red underline decoration-dotted"
              >
                Remove line
              </button>
            )}
          </div>
        ))}
      </div>

      <button
        type="button"
        onClick={addLine}
        className="mt-3 border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass"
      >
        + Add another product
      </button>

      {error && <p className="mt-3 text-sm text-stamp-red">{error}</p>}

      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className="border border-rule px-4 py-2 text-sm">
          Cancel
        </button>
        <button
          onClick={() => void handleSubmit()}
          disabled={submitting}
          className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50"
        >
          {submitting ? 'Receiving…' : 'Receive stock'}
        </button>
      </div>
    </Modal>
  )
}
