import { useEffect, useState, type FormEvent } from 'react'
import { customersApi } from '../api/domain'
import { useCurrencyFormatter } from '../lib/currency'
import { ApiError } from '../api/client'
import type { CustomerOut, PurchaseHistoryEntryOut } from '../types/api'

export function CustomersPage() {
  const [query, setQuery] = useState('')
  const [customers, setCustomers] = useState<CustomerOut[]>([])
  const [selected, setSelected] = useState<CustomerOut | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  function load(search?: string) {
    setLoading(true)
    customersApi
      .list(search)
      .then((list) => {
        setCustomers(list)
        setError(null)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load customers.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => load(), [])

  function handleSearch(e: FormEvent) {
    e.preventDefault()
    load(query.trim() || undefined)
  }

  return (
    <div className="p-6">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="font-display text-2xl text-ink">Customers</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="border border-ink bg-ink px-3 py-1.5 text-sm text-paper"
        >
          New customer
        </button>
      </header>

      <form onSubmit={handleSearch} className="mb-4 flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name or phone"
          className="flex-1 border border-rule bg-panel px-3 py-2 outline-none focus-visible:border-brass"
        />
        <button type="submit" className="border border-ink bg-ink px-4 py-2 text-paper">
          Search
        </button>
      </form>

      {error && (
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      <div className="ledger-panel divide-y divide-rule">
        {customers.map((c) => (
          <button
            key={c.id}
            onClick={() => setSelected(c)}
            className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-panel"
          >
            <div>
              <p className="font-medium">{c.name}</p>
              <p className="text-xs text-ink-soft">{c.phone ?? 'No phone on file'}</p>
            </div>
            <span className="figure text-brass">{c.loyalty_points} pts</span>
          </button>
        ))}
        {customers.length === 0 && !loading && (
          <p className="px-3 py-4 text-sm text-ink-soft">No customers found.</p>
        )}
      </div>

      {showCreate && (
        <CreateCustomerModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false)
            load()
          }}
        />
      )}

      {selected && (
        <CustomerDetailModal customer={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}

function CreateCustomerModal({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: () => void
}) {
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await customersApi.create({ name, phone: phone || null, email: email || null })
      onCreated()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create customer.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-3 border border-rule bg-paper p-5"
      >
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-display text-lg text-ink">New customer</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-ink-soft">
            ✕
          </button>
        </div>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name"
          required
          className="w-full border border-rule bg-paper px-3 py-2"
        />
        <input
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="Phone (optional)"
          className="w-full border border-rule bg-paper px-3 py-2"
        />
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email (optional)"
          type="email"
          className="w-full border border-rule bg-paper px-3 py-2"
        />
        {error && <p className="text-sm text-stamp-red">{error}</p>}
        <button
          type="submit"
          disabled={submitting}
          className="w-full border border-ink bg-ink py-2 text-paper disabled:opacity-50"
        >
          {submitting ? 'Saving…' : 'Save customer'}
        </button>
      </form>
    </div>
  )
}

function CustomerDetailModal({
  customer,
  onClose,
}: {
  customer: CustomerOut
  onClose: () => void
}) {
  const formatCurrency = useCurrencyFormatter()
  const [history, setHistory] = useState<PurchaseHistoryEntryOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    customersApi
      .purchaseHistory(customer.id)
      .then(setHistory)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load history.'))
  }, [customer.id])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4">
      <div className="max-h-[85vh] w-full max-w-md overflow-y-auto border border-rule bg-paper p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-lg text-ink">{customer.name}</h2>
          <button onClick={onClose} aria-label="Close" className="text-ink-soft">
            ✕
          </button>
        </div>
        <p className="text-sm text-ink-soft">{customer.phone ?? 'No phone on file'}</p>
        <p className="figure mt-1 text-sm text-brass">{customer.loyalty_points} loyalty points</p>

        <h3 className="mb-2 mt-4 text-xs uppercase tracking-wide text-ink-soft">
          Purchase history
        </h3>
        {error && <p className="text-sm text-stamp-red">{error}</p>}
        <ul className="divide-y divide-rule border border-rule">
          {history?.map((h) => (
            <li key={h.sale_id} className="flex justify-between px-3 py-2 text-sm">
              <span>
                Sale #{h.sale_id} · {new Date(h.created_at).toLocaleDateString()}
              </span>
              <span className="figure">{formatCurrency(h.total_amount)}</span>
            </li>
          ))}
          {history?.length === 0 && (
            <li className="px-3 py-3 text-sm text-ink-soft">No purchases yet.</li>
          )}
        </ul>
      </div>
    </div>
  )
}
