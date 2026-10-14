import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { productsApi, purchaseOrdersApi, suppliersApi } from '../api/domain'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { useCurrencyFormatter } from '../lib/currency'
import { businessToday, fallbackTimezone, isUnsellableExpiry } from '../lib/businessDate'
import { presetRange, type DateRangePreset } from '../lib/dateRangePresets'
import { DateRangePicker } from '../components/DateRangePicker'
import { categorySpendBreakdown } from '../lib/purchasingAnalytics'
import { ApiError, downloadExport } from '../api/client'
import { Modal } from '../components/Modal'
import type {
  ImportRowError,
  ProductOut,
  PurchaseOrderOut,
  SupplierKpiOut,
  SupplierOut,
  SupplierPaymentOut,
} from '../types/api'

export function PurchasingPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canApprove = hasPermission('purchasing.approve_po')
  const canReversePayment = hasPermission('purchasing.reverse_payment')
  const canReversePurchase = hasPermission('purchasing.reverse_purchase')

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

      <SupplierKpiPanel />

      <CategoryBreakdownPanel orders={allPurchases} />

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
                      po.status === 'REVERSED'
                        ? 'bg-stamp-red-soft/40 text-stamp-red'
                        : 'bg-brass-soft/30 text-ink'
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
          canReversePayment={canReversePayment}
          onClose={() => setShowSuppliers(false)}
          onChanged={refresh}
        />
      )}

      {selectedPO && (
        <PODetailModal
          po={selectedPO}
          supplierName={supplierName(suppliers, selectedPO.supplier_id)}
          canReverse={canReversePurchase}
          onReversed={() => {
            setSelectedPO(null)
            refresh()
          }}
          onClose={() => setSelectedPO(null)}
        />
      )}
    </div>
  )
}

function supplierName(suppliers: SupplierOut[], id: number): string {
  return suppliers.find((s) => s.id === id)?.name ?? `Supplier #${id}`
}

function SupplierKpiPanel() {
  const formatCurrency = useCurrencyFormatter()
  const timezone = useConfigStore((s) => s.config?.timezone) ?? fallbackTimezone()
  const [preset, setPreset] = useState<DateRangePreset>('month')
  const [range, setRange] = useState(() => presetRange('month', timezone))
  const [kpis, setKpis] = useState<SupplierKpiOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  function applyPreset(next: DateRangePreset) {
    setPreset(next)
    if (next !== 'custom') setRange(presetRange(next, timezone))
  }

  useEffect(() => {
    let cancelled = false
    setError(null)
    suppliersApi
      .kpis(range.start, range.end)
      .then((data) => {
        if (!cancelled) setKpis(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load supplier KPIs.')
        }
      })
    return () => {
      cancelled = true
    }
  }, [range])

  return (
    <div className="mb-4 ledger-panel p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xs uppercase tracking-wide text-ink-soft">Suppliers</h2>
        <DateRangePicker
          preset={preset}
          range={range}
          onPresetChange={applyPreset}
          onRangeChange={setRange}
        />
      </div>
      {error && (
        <p role="alert" className="text-sm text-stamp-red">
          {error}
        </p>
      )}
      {!error && kpis && (
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <p className="text-xs uppercase tracking-wide text-ink-soft">Purchased</p>
            <p className="figure text-xl text-ink">{formatCurrency(kpis.total_purchased)}</p>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-ink-soft">Paid</p>
            <p className="figure text-xl text-ink">{formatCurrency(kpis.total_paid)}</p>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-ink-soft">Net due</p>
            <p className="figure text-xl text-ink">{formatCurrency(kpis.net_due)}</p>
          </div>
        </div>
      )}
    </div>
  )
}

function CategoryBreakdownPanel({ orders }: { orders: PurchaseOrderOut[] }) {
  const formatCurrency = useCurrencyFormatter()
  const breakdown = useMemo(() => categorySpendBreakdown(orders), [orders])

  if (breakdown.length === 0) return null

  return (
    <div className="ledger-panel mb-4">
      <h2 className="border-b border-rule px-3 py-2 text-xs uppercase tracking-wide text-ink-soft">
        Spend by category
      </h2>
      <table className="w-full text-left text-sm">
        <tbody className="divide-y divide-rule">
          {breakdown.map((row) => (
            <tr key={row.category}>
              <td className="px-3 py-2">{row.category}</td>
              <td className="figure px-3 py-2 text-ink-soft">
                {row.itemCount} {row.itemCount === 1 ? 'item' : 'items'}
              </td>
              <td className="figure px-3 py-2 text-right text-ink-soft">
                {row.percentOfTotal}%
              </td>
              <td className="figure px-3 py-2 text-right font-medium">
                {formatCurrency(row.total)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SuppliersModal({
  suppliers,
  canRecordPayment,
  canReversePayment,
  onClose,
  onChanged,
}: {
  suppliers: SupplierOut[]
  canRecordPayment: boolean
  canReversePayment: boolean
  onClose: () => void
  onChanged: () => void
}) {
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [error, setError] = useState<string | null>(null)

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
          <SupplierRow
            key={s.id}
            supplier={s}
            canRecordPayment={canRecordPayment}
            canReversePayment={canReversePayment}
            onChanged={onChanged}
            onError={setError}
          />
        ))}
        {suppliers.length === 0 && (
          <li className="py-3 text-sm text-ink-soft">No suppliers yet.</li>
        )}
      </ul>
    </Modal>
  )
}

function newAttemptKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random()
    .toString(36)
    .slice(2)}`
}

function SupplierRow({
  supplier,
  canRecordPayment,
  canReversePayment,
  onChanged,
  onError,
}: {
  supplier: SupplierOut
  canRecordPayment: boolean
  canReversePayment: boolean
  onChanged: () => void
  onError: (message: string | null) => void
}) {
  const formatCurrency = useCurrencyFormatter()
  const [amount, setAmount] = useState('')
  const [paying, setPaying] = useState(false)
  const attempt = useRef<{ key: string; cents: number } | null>(null)
  const busy = useRef(false)

  const [showHistory, setShowHistory] = useState(false)
  const [payments, setPayments] = useState<SupplierPaymentOut[] | null>(null)
  const [reversingId, setReversingId] = useState<number | null>(null)
  const [reason, setReason] = useState('')
  const [reversing, setReversing] = useState(false)

  const ownedCents = Math.round(supplier.balance_owed * 100)

  async function loadPayments() {
    try {
      setPayments(await suppliersApi.payments(supplier.id))
    } catch (err) {
      onError(err instanceof ApiError ? err.message : 'Could not load payments.')
    }
  }

  async function handlePay() {
    if (busy.current) return
    const typed = amount.trim()
    if (!/^\d+(\.\d{1,2})?$/.test(typed) || Number(typed) <= 0) {
      onError('Enter an amount greater than 0 with at most 2 decimal places.')
      return
    }
    const cents = Math.round(Number(typed) * 100)
    if (cents > ownedCents) {
      onError(`Payment cannot exceed the amount owed (${formatCurrency(supplier.balance_owed)}).`)
      return
    }
    if (attempt.current === null || attempt.current.cents !== cents) {
      attempt.current = { key: newAttemptKey(), cents }
    }
    busy.current = true
    setPaying(true)
    onError(null)
    try {
      await suppliersApi.recordPayment(supplier.id, {
        amount: cents / 100,
        idempotency_key: attempt.current.key,
      })
      attempt.current = null
      setAmount('')
      if (showHistory) await loadPayments()
      onChanged()
    } catch (err) {
      if (err instanceof ApiError) {
        attempt.current = null
        onError(err.message)
      } else {
        onError('Could not reach the server. Check the payment history before retrying.')
      }
    } finally {
      busy.current = false
      setPaying(false)
    }
  }

  async function handleReverse(paymentId: number) {
    if (busy.current) return
    if (reason.trim().length < 3) {
      onError('Give a reason (at least 3 characters) for reversing a payment.')
      return
    }
    busy.current = true
    setReversing(true)
    onError(null)
    try {
      await suppliersApi.reversePayment(supplier.id, paymentId, {
        reason: reason.trim(),
      })
      setReversingId(null)
      setReason('')
      await loadPayments()
      onChanged()
    } catch (err) {
      onError(err instanceof ApiError ? err.message : 'Could not reverse the payment.')
    } finally {
      busy.current = false
      setReversing(false)
    }
  }

  return (
    <li className="py-2 text-sm">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="font-medium">{supplier.name}</p>
          <p className="figure text-xs text-ink-soft">
            {supplier.balance_owed < 0
              ? `Overpaid (credit): ${formatCurrency(-supplier.balance_owed)}`
              : `Owed: ${formatCurrency(supplier.balance_owed)}`}
          </p>
        </div>
        <div className="flex items-center gap-1">
          {canRecordPayment && supplier.balance_owed > 0 && (
            <>
              <input
                type="number"
                min={0.01}
                max={supplier.balance_owed}
                step="0.01"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                disabled={paying}
                placeholder="amount"
                aria-label={`Payment amount for ${supplier.name}`}
                className="figure w-24 border border-rule px-2 py-1 text-xs"
              />
              <button
                onClick={() => void handlePay()}
                disabled={paying}
                className="border border-stamp-green px-2 py-1 text-xs text-stamp-green disabled:opacity-50"
              >
                {paying ? 'Paying…' : 'Pay'}
              </button>
            </>
          )}
          {canRecordPayment && (
            <button
              onClick={() => {
                const next = !showHistory
                setShowHistory(next)
                if (next) void loadPayments()
              }}
              className="border border-rule px-2 py-1 text-xs text-ink-soft hover:border-brass"
            >
              {showHistory ? 'Hide payments' : 'Payments'}
            </button>
          )}
        </div>
      </div>

      {showHistory && (
        <div className="mt-2 border border-rule bg-paper px-3 py-2">
          {payments === null && <p className="text-xs text-ink-soft">Loading…</p>}
          {payments?.length === 0 && <p className="text-xs text-ink-soft">No payments yet.</p>}
          {payments?.map((p) => (
            <div key={p.id} className="border-b border-rule py-1.5 text-xs last:border-b-0">
              <div className="flex items-center justify-between gap-2">
                <span className={p.reversed ? 'text-ink-soft line-through' : ''}>
                  <span className="figure">{formatCurrency(p.amount)}</span>{' '}
                  <span className="text-ink-soft">{new Date(p.created_at).toLocaleString()}</span>
                  {p.notes ? <span className="text-ink-soft"> · {p.notes}</span> : null}
                </span>
                {p.reversed ? (
                  <span className="text-stamp-red">Reversed: {p.reversal_reason}</span>
                ) : (
                  canReversePayment &&
                  reversingId !== p.id && (
                    <button
                      onClick={() => {
                        setReversingId(p.id)
                        setReason('')
                      }}
                      className="border border-stamp-red px-2 py-0.5 text-stamp-red"
                    >
                      Reverse
                    </button>
                  )
                )}
              </div>
              {reversingId === p.id && (
                <div className="mt-1 flex items-center gap-1">
                  <input
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    maxLength={255}
                    placeholder="Reason (required)"
                    aria-label="Reason for reversing this payment"
                    className="flex-1 border border-rule px-2 py-1"
                  />
                  <button
                    onClick={() => void handleReverse(p.id)}
                    disabled={reversing}
                    className="border border-stamp-red bg-stamp-red px-2 py-1 text-paper disabled:opacity-50"
                  >
                    {reversing ? 'Reversing…' : 'Confirm reversal'}
                  </button>
                  <button
                    onClick={() => setReversingId(null)}
                    disabled={reversing}
                    className="border border-rule px-2 py-1 text-ink-soft"
                  >
                    Cancel
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </li>
  )
}

function PODetailModal({
  po,
  supplierName,
  canReverse,
  onReversed,
  onClose,
}: {
  po: PurchaseOrderOut
  supplierName: string
  canReverse: boolean
  onReversed: () => void
  onClose: () => void
}) {
  const formatCurrency = useCurrencyFormatter()
  const [confirming, setConfirming] = useState(false)
  const [reason, setReason] = useState('')
  const [reversing, setReversing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const busy = useRef(false)

  const total = po.items.reduce((sum, item) => {
    const quantity = item.quantity_received ?? item.quantity_ordered
    return sum + quantity * (item.unit_cost_actual ?? item.unit_cost_expected)
  }, 0)

  async function handleReverse() {
    if (busy.current) return
    if (reason.trim().length < 3) {
      setError('Give a reason (at least 3 characters) for reversing this purchase.')
      return
    }
    busy.current = true
    setReversing(true)
    setError(null)
    try {
      await purchaseOrdersApi.reverse(po.id, { reason: reason.trim() })
      onReversed()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Could not reach the server. Check the purchase list before retrying.',
      )
    } finally {
      busy.current = false
      setReversing(false)
    }
  }

  return (
    <Modal title={`Purchase order #${po.id}`} onClose={onClose}>
      <p className="mb-1 text-sm">
        Supplier: <span className="font-medium">{supplierName}</span>
      </p>
      <p className="mb-3 text-sm text-ink-soft">
        Received {new Date(po.received_at ?? po.created_at).toLocaleString()}
      </p>

      {po.status === 'REVERSED' && (
        <p className="mb-3 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          Reversed{po.reversed_at ? ` ${new Date(po.reversed_at).toLocaleString()}` : ''}:{' '}
          {po.reversal_reason}
        </p>
      )}

      <ul className="mb-4 divide-y divide-rule border border-rule">
        {po.items.map((item) => (
          <li key={item.id} className="flex justify-between px-3 py-2 text-sm">
            <span>
              {item.product_name}
              <span className="ml-2 text-xs text-ink-soft">
                {item.category_name ?? 'Uncategorised'}
              </span>
            </span>
            <span className="figure">
              {item.quantity_received ?? item.quantity_ordered} @{' '}
              {formatCurrency(item.unit_cost_actual ?? item.unit_cost_expected)}
            </span>
          </li>
        ))}
      </ul>

      {confirming && (
        <div className="mb-4 border border-stamp-red-soft px-3 py-2 text-sm">
          <p className="mb-2">
            This removes this stock from inventory and cancels {formatCurrency(total)} owed to{' '}
            {supplierName}. It only works while the stock is exactly as this purchase left it (nothing sold, adjusted or delivered into since).
          </p>
          {error && (
            <p role="alert" className="mb-2 text-stamp-red">
              {error}
            </p>
          )}
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={255}
            disabled={reversing}
            placeholder="Reason (required)"
            aria-label="Reason for reversing this purchase"
            className="mb-2 w-full border border-rule px-2 py-1"
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={() => {
                setConfirming(false)
                setError(null)
              }}
              disabled={reversing}
              className="border border-rule px-3 py-1.5 text-ink-soft"
            >
              Cancel
            </button>
            <button
              onClick={() => void handleReverse()}
              disabled={reversing}
              className="border border-stamp-red bg-stamp-red px-3 py-1.5 text-paper disabled:opacity-50"
            >
              {reversing ? 'Reversing…' : 'Confirm reversal'}
            </button>
          </div>
        </div>
      )}

      <div className="flex justify-end gap-2">
        {canReverse && po.status === 'RECEIVED' && !confirming && (
          <button
            onClick={() => setConfirming(true)}
            className="border border-stamp-red px-4 py-2 text-sm text-stamp-red"
          >
            Reverse purchase
          </button>
        )}
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
  const formatCurrency = useCurrencyFormatter()

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
    const breakdown = categorySpendBreakdown([imported])
    return (
      <Modal title="Stock received" onClose={onImported}>
        <p className="text-sm text-ink-soft">
          Received {imported.items.length} line item
          {imported.items.length === 1 ? '' : 's'} — already in your inventory.
        </p>
        {breakdown.length > 0 && (
          <ul className="mt-3 divide-y divide-rule border border-rule">
            {breakdown.map((row) => (
              <li key={row.category} className="flex justify-between px-3 py-2 text-sm">
                <span>{row.category}</span>
                <span className="figure text-ink-soft">
                  {row.percentOfTotal}% · {formatCurrency(row.total)}
                </span>
              </li>
            ))}
          </ul>
        )}
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
  sellingPriceTouched: boolean
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
  const timezone = useConfigStore((s) => s.config?.timezone) ?? fallbackTimezone()
  const [supplierId, setSupplierId] = useState<number | ''>(suppliers[0]?.id ?? '')
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
      sellingPriceTouched: false,
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
        sellingPriceTouched: false,
      },
    ])
  }

  function markupFor(line: QuickPurchaseLineDraft): number {
    return line.unitCost > 0 ? ((line.sellingPrice - line.unitCost) / line.unitCost) * 100 : 0
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
    const today = businessToday(timezone)
    const unsellable = validLines.filter((l) => isUnsellableExpiry(l.expiryDate, today))
    if (
      unsellable.length > 0 &&
      !window.confirm(
        `${unsellable.length} line(s) have an expiry date that is today or already past ` +
          `(${unsellable.map((l) => l.expiryDate).join(', ')}). That stock cannot be sold. ` +
          'Check the year for a typo.\n\nReceive it anyway?',
      )
    ) {
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
          selling_price: l.sellingPriceTouched ? l.sellingPrice : undefined,
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
                          updateLine(index, {
                            productId: p.id,
                            productName: p.name,
                            sellingPrice: p.current_selling_price ?? 0,
                          })
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
                  step="any"
                  value={line.unitCost}
                  onChange={(e) =>
                    updateLine(index, {
                      unitCost: Math.round((Math.max(0, Number(e.target.value) || 0)) * 100) / 100,
                    })
                  }
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
                  step="any"
                  value={line.sellingPrice}
                  onChange={(e) =>
                    updateLine(index, {
                      sellingPrice: Math.round((Math.max(0, Number(e.target.value) || 0)) * 100) / 100,
                      sellingPriceTouched: true,
                    })
                  }
                  className="figure mt-1 w-full border border-rule bg-paper px-2 py-1.5 text-sm"
                />
              </label>
              <label className="block">
                <span className="block text-xs uppercase tracking-wide text-ink-soft">
                  Markup %
                </span>
                <input
                  type="number"
                  step="any"
                  value={Math.round(markupFor(line) * 100) / 100 || ''}
                  onChange={(e) => {
                    const markupPercent = Number(e.target.value) || 0
                    const sellingPrice =
                      Math.round(line.unitCost * (1 + markupPercent / 100) * 100) / 100
                    updateLine(index, { sellingPrice, sellingPriceTouched: true })
                  }}
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
