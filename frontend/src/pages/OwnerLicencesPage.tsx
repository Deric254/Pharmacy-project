import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { licenceApi } from '../api/licence'
import {
  ownerLicencesApi,
  type IssueResult,
  type LedgerCustomer,
  type LedgerOut,
} from '../api/ownerLicences'
import { ApiError } from '../api/client'

function errText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function normCode(code: string): string {
  return code.toUpperCase().replace(/[^A-Z0-9]/g, '')
}

const STATUS_LABEL: Record<LedgerCustomer['status'], string> = {
  active: 'Active',
  expiring: 'Expiring soon',
  expired: 'Expired',
}

const STATUS_CLASS: Record<LedgerCustomer['status'], string> = {
  active: 'text-ink',
  expiring: 'text-brass',
  expired: 'text-stamp-red',
}

function monthLabel(month: string): string {
  const [y, m] = month.split('-').map(Number)
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
}

function Tile({ label, value, warn }: { label: string; value: string | number; warn?: boolean }) {
  return (
    <div className="ledger-panel px-4 py-3">
      <p className="text-xs uppercase tracking-wide text-ink-soft">{label}</p>
      <p className={`mt-1 font-display text-2xl ${warn ? 'text-stamp-red' : 'text-ink'}`}>
        {value}
      </p>
    </div>
  )
}

/**
 * Owner-only licence desk. The server refuses every call unless this is the owner's own
 * computer, the signed-in user holds the top role, and (for issuing) the owner key is sent.
 */
export function OwnerLicencesPage() {
  const [isOwner, setIsOwner] = useState<boolean | null>(null)
  const [ledger, setLedger] = useState<LedgerOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [result, setResult] = useState<IssueResult | null>(null)
  const [copied, setCopied] = useState<string | null>(null)

  // The issue form. The owner key lives only in this component's memory.
  const [ownerKey, setOwnerKey] = useState('')
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [days, setDays] = useState('30')
  const [paid, setPaid] = useState(false)
  const [phone, setPhone] = useState('')
  const [fee, setFee] = useState('')
  const [issuing, setIssuing] = useState(false)
  const [exporting, setExporting] = useState(false)

  const reload = useCallback(async () => {
    try {
      setLedger(await ownerLicencesApi.list())
      setError(null)
    } catch (err) {
      setError(errText(err, 'Could not load the licence ledger.'))
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    licenceApi
      .state()
      .then((s) => {
        if (cancelled) return
        setIsOwner(s.kind === 'owner')
        if (s.kind === 'owner') void reload()
      })
      .catch(() => {
        if (!cancelled) setIsOwner(false)
      })
    return () => {
      cancelled = true
    }
  }, [reload])

  const existing = useMemo(
    () => ledger?.customers.find((c) => normCode(c.code) === normCode(code)) ?? null,
    [ledger, code],
  )

  function startRenewal(c: LedgerCustomer) {
    setCode(c.code)
    setName(c.name)
    setResult(null)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  async function handleIssue(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setResult(null)
    const dayCount = Number(days)
    if (!Number.isInteger(dayCount) || dayCount < 1 || dayCount > 365) {
      setError('Days must be a whole number from 1 to 365.')
      return
    }
    setIssuing(true)
    try {
      const out = await ownerLicencesApi.issue({
        owner_key: ownerKey,
        code,
        user: existing ? existing.name : name,
        days: dayCount,
        paid_this_month: paid,
        phone: phone.trim() || undefined,
        monthly_fee: fee.trim() === '' ? undefined : Number(fee),
      })
      setResult(out)
      setCode('')
      setName('')
      setPaid(false)
      setPhone('')
      setFee('')
      await reload()
    } catch (err) {
      setError(errText(err, 'Could not issue the licence. Try again.'))
    } finally {
      setIssuing(false)
    }
  }

  async function copy(text: string, id: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(id)
    } catch {
      setCopied(null)
    }
  }

  async function togglePayment(c: LedgerCustomer, month: string, nowPaid: boolean) {
    try {
      await ownerLicencesApi.setPayment(c.id, month, nowPaid)
      await reload()
    } catch (err) {
      setError(errText(err, 'Could not save that payment.'))
    }
  }

  async function handleDelete(c: LedgerCustomer) {
    const ok = window.confirm(
      `Remove ${c.name} from the ledger?\n\nThis only removes the record here. A licence you already sent keeps working until it expires.`,
    )
    if (!ok) return
    try {
      await ownerLicencesApi.remove(c.id)
      await reload()
    } catch (err) {
      setError(errText(err, 'Could not remove that customer.'))
    }
  }

  async function handleExport() {
    setExporting(true)
    try {
      await ownerLicencesApi.exportCsv()
    } catch (err) {
      setError(errText(err, 'Could not export the ledger.'))
    } finally {
      setExporting(false)
    }
  }

  if (isOwner === null) {
    return <div className="p-6 text-sm text-ink-soft">Loading…</div>
  }
  if (!isOwner) {
    return (
      <div className="p-6">
        <h1 className="font-display text-2xl text-ink">Licences</h1>
        <p className="mt-2 text-sm text-ink-soft">
          This is only available on the owner&apos;s own computer.
        </p>
      </div>
    )
  }

  const s = ledger?.summary

  return (
    <div className="space-y-6 p-6">
      <header className="flex items-center justify-between">
        <h1 className="font-display text-2xl text-ink">Licences (owner only)</h1>
        <button
          onClick={() => void handleExport()}
          disabled={exporting || !ledger || ledger.customers.length === 0}
          className="border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass disabled:opacity-50"
        >
          {exporting ? 'Exporting…' : 'Export to CSV (Excel)'}
        </button>
      </header>

      {error && (
        <p
          role="alert"
          className="border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red"
        >
          {error}
        </p>
      )}

      {s && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Tile label="Total users" value={s.total_customers} />
          <Tile label="Active" value={s.active} />
          <Tile label="Expiring ≤ 14 days" value={s.expiring} warn={s.expiring > 0} />
          <Tile label="Expired" value={s.expired} warn={s.expired > 0} />
          <Tile label="Unpaid months" value={s.unpaid_months} warn={s.unpaid_months > 0} />
        </div>
      )}

      <section className="ledger-panel p-5">
        <h2 className="mb-3 font-display text-lg text-ink">Generate a licence</h2>
        <form onSubmit={handleIssue} className="grid gap-4 md:grid-cols-2">
          <div>
            <label htmlFor="ol-code" className="block text-xs uppercase tracking-wide text-ink-soft">
              Activation code from the customer
            </label>
            <input
              id="ol-code"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="ABCD-EFGH-JKLM"
              autoComplete="off"
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 font-mono text-sm tracking-widest text-ink outline-none focus-visible:border-brass"
            />
            {existing && (
              <p className="mt-1 text-xs text-brass">
                Existing customer: this will be a renewal for {existing.name}.
              </p>
            )}
          </div>
          <div>
            <label htmlFor="ol-name" className="block text-xs uppercase tracking-wide text-ink-soft">
              Customer name
            </label>
            <input
              id="ol-name"
              value={existing ? existing.name : name}
              onChange={(e) => setName(e.target.value)}
              readOnly={existing !== null}
              maxLength={60}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm text-ink outline-none focus-visible:border-brass read-only:opacity-70"
            />
          </div>
          <div>
            <label htmlFor="ol-days" className="block text-xs uppercase tracking-wide text-ink-soft">
              Licence length (days)
            </label>
            <input
              id="ol-days"
              type="number"
              min={1}
              max={365}
              value={days}
              onChange={(e) => setDays(e.target.value)}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm text-ink outline-none focus-visible:border-brass"
            />
          </div>
          <div>
            <label htmlFor="ol-key" className="block text-xs uppercase tracking-wide text-ink-soft">
              Owner key
            </label>
            <input
              id="ol-key"
              type="password"
              autoComplete="off"
              value={ownerKey}
              onChange={(e) => setOwnerKey(e.target.value)}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm text-ink outline-none focus-visible:border-brass"
            />
            <p className="mt-1 text-xs text-ink-soft">
              Never saved. Kept in memory only while this page is open.
            </p>
          </div>
          <div>
            <label htmlFor="ol-phone" className="block text-xs uppercase tracking-wide text-ink-soft">
              Phone (optional)
            </label>
            <input
              id="ol-phone"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              maxLength={40}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm text-ink outline-none focus-visible:border-brass"
            />
          </div>
          <div>
            <label htmlFor="ol-fee" className="block text-xs uppercase tracking-wide text-ink-soft">
              Monthly fee (optional)
            </label>
            <input
              id="ol-fee"
              type="number"
              min={0}
              step="any"
              value={fee}
              onChange={(e) => setFee(e.target.value)}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm text-ink outline-none focus-visible:border-brass"
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-ink md:col-span-2">
            <input type="checkbox" checked={paid} onChange={(e) => setPaid(e.target.checked)} />
            They have paid for this month
          </label>
          <div className="md:col-span-2">
            <button
              type="submit"
              disabled={
                issuing ||
                ownerKey.trim() === '' ||
                normCode(code).length !== 12 ||
                (!existing && name.trim() === '')
              }
              className="border border-ink bg-ink px-4 py-2 text-sm font-medium text-paper disabled:opacity-50"
            >
              {issuing ? 'Generating…' : existing ? 'Generate renewal' : 'Generate licence'}
            </button>
          </div>
        </form>

        {result && (
          <div className="mt-5 border border-brass bg-paper p-4">
            <p className="text-sm text-ink">
              {result.renewal ? 'Renewal' : 'Licence'} for <strong>{result.user}</strong>, valid
              until {result.expires}. Send them everything in the box below:
            </p>
            <textarea
              readOnly
              rows={4}
              value={result.licence}
              onFocus={(e) => e.currentTarget.select()}
              className="mt-2 w-full border border-rule bg-panel px-3 py-2 font-mono text-xs text-ink"
            />
            <button
              type="button"
              onClick={() => void copy(result.licence, 'result')}
              className="mt-2 border border-rule px-3 py-1.5 text-sm text-ink hover:border-brass"
            >
              {copied === 'result' ? 'Copied' : 'Copy licence'}
            </button>
          </div>
        )}
      </section>

      <section className="ledger-panel overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-rule text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-3 py-2">Customer</th>
              <th className="px-3 py-2">Activation code</th>
              <th className="px-3 py-2">Licence</th>
              <th className="px-3 py-2">Paid months</th>
              <th className="px-3 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {ledger?.customers.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-ink-soft">
                  No licences issued yet. Generate the first one above.
                </td>
              </tr>
            )}
            {ledger?.customers.map((c) => (
              <CustomerRows
                key={c.id}
                c={c}
                open={expanded === c.id}
                copied={copied === c.id}
                onToggle={() => setExpanded(expanded === c.id ? null : c.id)}
                onRenew={() => startRenewal(c)}
                onCopy={() => c.latest_licence && void copy(c.latest_licence, c.id)}
                onPay={(month, nowPaid) => void togglePayment(c, month, nowPaid)}
                onDelete={() => void handleDelete(c)}
              />
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}

function CustomerRows({
  c,
  open,
  copied,
  onToggle,
  onRenew,
  onCopy,
  onPay,
  onDelete,
}: {
  c: LedgerCustomer
  open: boolean
  copied: boolean
  onToggle: () => void
  onRenew: () => void
  onCopy: () => void
  onPay: (month: string, paid: boolean) => void
  onDelete: () => void
}) {
  const unpaid = c.unpaid_months.length
  return (
    <>
      <tr className="border-b border-rule align-top">
        <td className="px-3 py-2">
          <p className="font-medium text-ink">{c.name}</p>
          {c.phone && <p className="text-xs text-ink-soft">{c.phone}</p>}
        </td>
        <td className="px-3 py-2 font-mono text-xs tracking-wider text-ink">{c.code}</td>
        <td className="px-3 py-2">
          <p className={STATUS_CLASS[c.status]}>{STATUS_LABEL[c.status]}</p>
          <p className="text-xs text-ink-soft">
            {c.expires ? `until ${c.expires}` : '—'}
            {c.days_left !== null && c.days_left >= 0 ? ` (${c.days_left} days left)` : ''}
          </p>
        </td>
        <td className="px-3 py-2">
          <p className="text-ink">
            {c.paid_count} of {c.months.length}
          </p>
          {unpaid > 0 && (
            <p className="text-xs text-stamp-red">
              {unpaid} unpaid
              {c.amount_outstanding !== null && ` · ${c.amount_outstanding.toLocaleString()}`}
            </p>
          )}
        </td>
        <td className="space-x-3 whitespace-nowrap px-3 py-2 text-right text-xs">
          <button onClick={onToggle} className="text-ink underline decoration-dotted">
            {open ? 'Hide payments' : 'Payments'}
          </button>
          <button onClick={onRenew} className="text-ink underline decoration-dotted">
            Renew
          </button>
          <button onClick={onCopy} className="text-ink underline decoration-dotted">
            {copied ? 'Copied' : 'Copy last licence'}
          </button>
          <button onClick={onDelete} className="text-stamp-red underline decoration-dotted">
            Remove
          </button>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-rule bg-panel">
          <td colSpan={5} className="px-3 py-3">
            <p className="mb-2 text-xs uppercase tracking-wide text-ink-soft">
              Tick each month as they pay
            </p>
            <div className="flex flex-wrap gap-2">
              {c.months.map((m) => (
                <label
                  key={m.month}
                  className="flex items-center gap-2 border border-rule bg-paper px-3 py-1.5 text-sm text-ink"
                >
                  <input
                    type="checkbox"
                    checked={m.paid}
                    onChange={(e) => onPay(m.month, e.target.checked)}
                  />
                  {monthLabel(m.month)}
                </label>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
