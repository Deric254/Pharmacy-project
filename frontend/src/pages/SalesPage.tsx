import { useEffect, useRef, useState } from 'react'
import { salesApi } from '../api/domain'
import { useCurrencyFormatter } from '../lib/currency'
import { ApiError, downloadExport } from '../api/client'
import { Modal } from '../components/Modal'
import type {
  PaymentMethod,
  RefundOut,
  RefundReason,
  SaleListItemOut,
  SaleOut,
} from '../types/api'

const REFUND_REASONS: { value: RefundReason; label: string }[] = [
  { value: 'CUSTOMER_RETURN', label: 'Customer return' },
  { value: 'DAMAGED', label: 'Damaged' },
  { value: 'WRONG_ITEM_SOLD', label: 'Wrong item sold' },
  { value: 'EXPIRED', label: 'Expired' },
  { value: 'OTHER', label: 'Other' },
]

export function SalesPage() {
  const formatCurrency = useCurrencyFormatter()
  const [sales, setSales] = useState<SaleListItemOut[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedSaleId, setSelectedSaleId] = useState<number | null>(null)
  const limit = 25

  function load() {
    setLoading(true)
    setError(null)
    salesApi
      .list({ limit, offset })
      .then((page) => {
        setSales(page.entries)
        setTotal(page.total)
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : 'Could not load sales.')
      })
      .finally(() => setLoading(false))
  }

  useEffect(load, [offset])

  const hasNextPage = offset + limit < total
  const hasPrevPage = offset > 0

  return (
    <div className="p-6">
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="mb-1 font-display text-2xl text-ink">Sales</h1>
          <p className="text-sm text-ink-soft">
            Every sale that's gone through the register, newest first. Open one to see exactly
            what was sold, or to process a refund.
          </p>
        </div>
        <button
          onClick={() => void downloadExport('/sales', {}, 'excel')}
          className="border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass"
        >
          Export to Excel
        </button>
      </div>

      {error && (
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      <div className="ledger-panel">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-rule">
            <tr>
              <th className="px-3 py-2 font-medium">Sale</th>
              <th className="px-3 py-2 font-medium">Cashier</th>
              <th className="px-3 py-2 font-medium">Customer</th>
              <th className="px-3 py-2 font-medium">Items</th>
              <th className="px-3 py-2 text-right font-medium">Total</th>
              <th className="px-3 py-2 font-medium">When</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-rule">
            {sales.map((s) => (
              <tr
                key={s.id}
                onClick={() => setSelectedSaleId(s.id)}
                className="cursor-pointer hover:bg-panel"
              >
                <td className="figure px-3 py-2">#{s.id}</td>
                <td className="px-3 py-2">{s.cashier_name}</td>
                <td className="px-3 py-2 text-ink-soft">{s.customer_name ?? '—'}</td>
                <td className="figure px-3 py-2">{s.item_count}</td>
                <td className="figure px-3 py-2 text-right">{formatCurrency(s.total_amount)}</td>
                <td className="px-3 py-2 text-ink-soft">
                  {new Date(s.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
            {!loading && sales.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-sm text-ink-soft">
                  No sales yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex items-center justify-between text-sm text-ink-soft">
        <span>{total} total</span>
        <div className="flex gap-2">
          <button
            onClick={() => setOffset((o) => Math.max(0, o - limit))}
            disabled={!hasPrevPage}
            className="border border-rule px-3 py-1 disabled:opacity-40"
          >
            Previous
          </button>
          <button
            onClick={() => setOffset((o) => o + limit)}
            disabled={!hasNextPage}
            className="border border-rule px-3 py-1 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>

      {selectedSaleId !== null && (
        <SaleDetailModal
          saleId={selectedSaleId}
          onClose={() => setSelectedSaleId(null)}
          onRefunded={() => {
            setSelectedSaleId(null)
            load()
          }}
        />
      )}
    </div>
  )
}

function SaleDetailModal({
  saleId,
  onClose,
  onRefunded,
}: {
  saleId: number
  onClose: () => void
  onRefunded: () => void
}) {
  const formatCurrency = useCurrencyFormatter()
  const [sale, setSale] = useState<SaleOut | null>(null)
  const [refunds, setRefunds] = useState<RefundOut[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showRefund, setShowRefund] = useState(false)

  useEffect(() => {
    salesApi
      .get(saleId)
      .then(setSale)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : 'Could not load this sale.')
      })
    salesApi.listRefunds(saleId).then(setRefunds).catch(() => setRefunds([]))
  }, [saleId])

  async function handleViewReceipt(id: number) {
    try {
      const blob = await salesApi.receiptBlob(id)
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank')
      setTimeout(() => URL.revokeObjectURL(url), 30000)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the receipt.')
    }
  }

  if (showRefund && sale) {
    return (
      <RefundModal
        sale={sale}
        onClose={() => setShowRefund(false)}
        onRefunded={onRefunded}
      />
    )
  }

  return (
    <Modal title={`Sale #${saleId}`} onClose={onClose}>
      {error && <p className="text-sm text-stamp-red">{error}</p>}
      {!sale && !error && <p className="text-sm text-ink-soft">Loading…</p>}
      {sale && (
        <>
          <ul className="divide-y divide-rule border border-rule">
            {sale.items.map((item) => (
              <li key={item.id} className="flex justify-between px-3 py-2 text-sm">
                <span>
                  {item.product_name} × {item.quantity}
                </span>
                <span className="figure">{formatCurrency(item.line_total)}</span>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex justify-between text-sm font-medium">
            <span>Total</span>
            <span className="figure">{formatCurrency(sale.total_amount)}</span>
          </div>

          {refunds.length > 0 && (
            <div className="mt-4">
              <p className="mb-1 text-xs uppercase tracking-wide text-ink-soft">
                Already refunded
              </p>
              <ul className="divide-y divide-rule border border-rule">
                {refunds.map((r) => (
                  <li key={r.id} className="px-3 py-2 text-sm">
                    <div className="flex justify-between">
                      <span>{new Date(r.created_at).toLocaleString()}</span>
                      <span className="figure text-stamp-red">
                        -{formatCurrency(r.total_amount)}
                      </span>
                    </div>
                    {r.items.map((ri) => (
                      <p key={ri.sale_item_id} className="text-xs text-ink-soft">
                        {ri.quantity} unit{ri.quantity === 1 ? '' : 's'}
                        {ri.restocked ? ' (returned to stock)' : ' (not restocked)'}
                      </p>
                    ))}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-4 flex justify-end gap-2">
            <button onClick={onClose} className="border border-rule px-4 py-2 text-sm">
              Close
            </button>
            <button
              onClick={() => void handleViewReceipt(sale.id)}
              className="border border-rule px-4 py-2 text-sm text-ink-soft hover:border-brass"
            >
              Receipt
            </button>
            <button
              onClick={() => setShowRefund(true)}
              className="border border-ink bg-ink px-4 py-2 text-sm text-paper"
            >
              Refund…
            </button>
          </div>
        </>
      )}
    </Modal>
  )
}

interface RefundLine {
  saleItemId: number
  productName: string
  maxQuantity: number
  quantity: number
  restock: boolean
}

function RefundModal({
  sale,
  onClose,
  onRefunded,
}: {
  sale: SaleOut
  onClose: () => void
  onRefunded: () => void
}) {
  const [lines, setLines] = useState<RefundLine[]>(
    sale.items.map((item) => ({
      saleItemId: item.id,
      productName: item.product_name,
      maxQuantity: item.quantity,
      quantity: item.quantity,
      restock: true,
    })),
  )
  const [reason, setReason] = useState<RefundReason>('CUSTOMER_RETURN')
  const [method, setMethod] = useState<PaymentMethod>('CASH')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)

  function updateLine(saleItemId: number, patch: Partial<RefundLine>) {
    setLines((prev) => prev.map((l) => (l.saleItemId === saleItemId ? { ...l, ...patch } : l)))
  }

  async function handleSubmit() {
    if (submittingRef.current) return // synchronous guard against a fast double-click
    const toRefund = lines.filter((l) => l.quantity > 0)
    if (toRefund.length === 0) {
      setError('Choose a quantity to refund for at least one item.')
      return
    }
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    try {
      await salesApi.refund(sale.id, {
        reason,
        method,
        items: toRefund.map((l) => ({
          sale_item_id: l.saleItemId,
          quantity: l.quantity,
          restock: l.restock,
        })),
      })
      onRefunded()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Refund failed. Nothing was refunded.')
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  return (
    <Modal title={`Refund sale #${sale.id}`} onClose={onClose}>
      <div className="space-y-3">
        {lines.map((line) => (
          <div key={line.saleItemId} className="border border-rule p-3">
            <p className="mb-2 text-sm font-medium">
              {line.productName}{' '}
              <span className="font-normal text-ink-soft">(sold {line.maxQuantity})</span>
            </p>
            <div className="flex items-center gap-4">
              <label className="flex items-center gap-2 text-sm">
                <span className="text-xs uppercase tracking-wide text-ink-soft">
                  Qty to refund
                </span>
                <input
                  type="number"
                  min={0}
                  max={line.maxQuantity}
                  value={line.quantity}
                  onChange={(e) =>
                    updateLine(line.saleItemId, {
                      quantity: Math.min(
                        line.maxQuantity,
                        Math.max(0, Number(e.target.value)),
                      ),
                    })
                  }
                  className="figure w-20 border border-rule px-2 py-1"
                />
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={line.restock}
                  onChange={(e) => updateLine(line.saleItemId, { restock: e.target.checked })}
                />
                Return to stock
              </label>
            </div>
          </div>
        ))}

        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">Reason</span>
          <select
            value={reason}
            onChange={(e) => setReason(e.target.value as RefundReason)}
            className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
          >
            {REFUND_REASONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">
            Refund method
          </span>
          <select
            value={method}
            onChange={(e) => setMethod(e.target.value as PaymentMethod)}
            className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
          >
            <option value="CASH">Cash</option>
            <option value="CARD">Card</option>
            <option value="MPESA">Mobile money</option>
          </select>
        </label>

        {error && <p className="text-sm text-stamp-red">{error}</p>}

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="border border-rule px-4 py-2 text-sm">
            Cancel
          </button>
          <button
            onClick={() => void handleSubmit()}
            disabled={submitting}
            className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50"
          >
            {submitting ? 'Processing…' : 'Process refund'}
          </button>
        </div>
      </div>
    </Modal>
  )
}
