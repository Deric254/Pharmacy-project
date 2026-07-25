import { useEffect, useState } from 'react'
import { auditLogsApi } from '../api/audit'
import { ApiError, downloadExport } from '../api/client'
import type { AuditLogOut } from '../types/api'

const PAGE_SIZE = 25

export function AuditLogPage() {
  const [entries, setEntries] = useState<AuditLogOut[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [entityType, setEntityType] = useState('')
  const [action, setAction] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    auditLogsApi
      .list({
        entity_type: entityType || undefined,
        action: action || undefined,
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
        setError(err instanceof ApiError ? err.message : 'Could not load the audit trail.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [entityType, action, startDate, endDate, offset])

  function applyFilter(setter: (v: string) => void, value: string) {
    setter(value)
    setOffset(0)
  }

  const hasNextPage = offset + PAGE_SIZE < total
  const hasPrevPage = offset > 0

  return (
    <div className="p-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="font-display text-2xl text-ink">Audit Trail</h1>
        <button
          onClick={() =>
            void downloadExport(
              '/audit-logs',
              {
                ...(entityType && { entity_type: entityType }),
                ...(action && { action }),
                ...(startDate && { start_date: startDate }),
                ...(endDate && { end_date: endDate }),
              },
              'excel',
            )
          }
          className="border border-rule px-3 py-1 text-sm text-ink-soft hover:border-brass"
        >
          Export to Excel
        </button>
      </div>
      <p className="mb-6 text-sm text-ink-soft">
        Every price change, refund, password reset, and role edit, in order — who did it and
        when. Names shown here are recorded at the time of the action, not looked up fresh, so a
        deactivated or renamed account never rewrites what actually happened.
      </p>

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">
            Entity type
          </span>
          <input
            value={entityType}
            onChange={(e) => applyFilter(setEntityType, e.target.value)}
            placeholder="e.g. user, role"
            className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
          />
        </label>
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">Action</span>
          <input
            value={action}
            onChange={(e) => applyFilter(setAction, e.target.value)}
            placeholder="e.g. login.failed"
            className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
          />
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
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      <div className="ledger-panel divide-y divide-rule">
        {entries.map((entry) => (
          <div key={entry.id} className="px-3 py-3 text-sm">
            <div className="flex items-center justify-between">
              <p className="font-medium">
                {entry.action}{' '}
                <span className="text-xs font-normal text-ink-soft">
                  {entry.entity_type} #{entry.entity_id}
                </span>
              </p>
              <span className="figure text-xs text-ink-soft">
                {new Date(entry.created_at).toLocaleString()}
              </span>
            </div>
            <p className="text-xs text-ink-soft">
              {entry.user_name_snapshot ?? 'System'}
              {entry.ip_address && ` · ${entry.ip_address}`}
            </p>
            {(entry.old_value ?? entry.new_value) && (
              <p className="mt-1 text-xs text-ink-soft">
                {entry.old_value && <span className="figure">was: {entry.old_value}</span>}
                {entry.old_value && entry.new_value && ' · '}
                {entry.new_value && <span className="figure">now: {entry.new_value}</span>}
              </p>
            )}
          </div>
        ))}
        {entries.length === 0 && !loading && (
          <p className="px-3 py-4 text-sm text-ink-soft">No matching audit entries.</p>
        )}
      </div>

      <div className="mt-4 flex items-center justify-between text-sm text-ink-soft">
        <span className="figure">
          {total === 0 ? '0' : `${offset + 1}-${Math.min(offset + PAGE_SIZE, total)}`} of{' '}
          {total}
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
