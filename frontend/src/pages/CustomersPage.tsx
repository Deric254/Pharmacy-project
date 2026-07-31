import { useEffect, useState, type FormEvent } from 'react'
import { customersApi } from '../api/domain'
import { useCurrencyFormatter } from '../lib/currency'
import { ApiError, downloadExport } from '../api/client'
import { Modal } from '../components/Modal'
import type { CustomerOut, ImportRowError, PurchaseHistoryEntryOut } from '../types/api'

export function CustomersPage() {
  const [query, setQuery] = useState('')
  const [customers, setCustomers] = useState<CustomerOut[]>([])
  const [selected, setSelected] = useState<CustomerOut | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [showImport, setShowImport] = useState(false)
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
        <div className="flex gap-2">
          <button
            onClick={() => void downloadExport('/customers', {}, 'excel')}
            className="border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass"
          >
            Export to Excel
          </button>
          <button
            onClick={() => void customersApi.downloadImportTemplate()}
            className="border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass"
          >
            Download template
          </button>
          <button
            onClick={() => setShowImport(true)}
            className="border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass"
          >
            Import from Excel
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="border border-ink bg-ink px-3 py-1.5 text-sm text-paper"
          >
            New customer
          </button>
        </div>
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

      {showImport && (
        <CustomerImportModal
          onClose={() => setShowImport(false)}
          onImported={() => {
            setShowImport(false)
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
    <Modal title="New customer" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
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
    </Modal>
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
    <Modal title={customer.name} onClose={onClose}>
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
    </Modal>
  )
}

function CustomerImportModal({
  onClose,
  onImported,
}: {
  onClose: () => void
  onImported: () => void
}) {
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
      const result = await customersApi.importFromExcel(file)
      setSuccessCount(result.created)
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
      setSubmitting(false)
    }
  }

  if (successCount !== null) {
    return (
      <Modal title="Import complete" onClose={onImported}>
        <p className="text-sm text-ink-soft">
          {successCount} customer{successCount === 1 ? '' : 's'} imported successfully.
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
    <Modal title="Import customers from Excel" onClose={onClose}>
      <p className="text-sm text-ink-soft">
        Use the template's columns for name, phone, and email. If anything is wrong when you
        upload, nothing is imported until it's fixed — never a partial import.
      </p>

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
          disabled={!file || submitting}
          className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50"
        >
          {submitting ? 'Importing…' : 'Import'}
        </button>
      </div>
    </Modal>
  )
}
