import { useEffect, useState } from 'react'
import { reportsApi } from '../api/reports'
import type {
  IntelligenceArea,
  IntelligenceInsight,
  IntelligenceKpi,
  IntelligenceReportOut,
} from '../types/api'
import { useCurrencyFormatter } from '../lib/currency'

const PERIODS = [7, 30, 90]

const DOT: Record<string, string> = {
  problem: 'bg-stamp-red',
  watch: 'bg-brass',
  good: 'bg-stamp-green',
  info: 'bg-ink-soft',
}

function rank(severity: string): number {
  return severity === 'problem' ? 0 : severity === 'watch' ? 1 : severity === 'info' ? 2 : 3
}

type Fmt = (value: number) => string

function fmtValue(unit: string, v: number | null | undefined, money: Fmt): string {
  if (v === null || v === undefined) return '—'
  if (unit === 'money') return money(v)
  if (unit === 'percent') return `${v.toFixed(1)}%`
  return Math.round(v).toLocaleString()
}

function fmtChange(
  unit: string,
  current: number | null | undefined,
  previous: number | null | undefined,
  changePct: number | null | undefined,
): string | null {
  if (current === null || current === undefined || previous === null || previous === undefined) {
    return null
  }
  if (unit === 'percent') {
    const d = current - previous
    return `${d >= 0 ? '+' : ''}${d.toFixed(1)} pts`
  }
  if (changePct === null || changePct === undefined) return null
  return `${changePct >= 0 ? '+' : ''}${changePct.toFixed(1)}%`
}

function KpiTile({ k, money }: { k: IntelligenceKpi; money: Fmt }) {
  const change = fmtChange(k.unit, k.value, k.previous, k.change_pct)
  return (
    <div className="min-w-0">
      <div className="text-xs text-ink-soft">{k.label}</div>
      <div className="figure font-medium">{fmtValue(k.unit, k.value, money)}</div>
      {k.previous !== null && (
        <div className="text-xs text-ink-soft">
          was {fmtValue(k.unit, k.previous, money)}
          {change ? ` (${change})` : ''}
        </div>
      )}
    </div>
  )
}

function InsightBlock({ i, money }: { i: IntelligenceInsight; money: Fmt }) {
  const cmp = i.comparison
  const cmpChange = cmp ? fmtChange(cmp.unit, cmp.current, cmp.previous, cmp.change_pct) : null
  return (
    <div className="border-t border-rule py-3">
      <div className="flex items-start gap-2">
        <span
          aria-hidden
          className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${DOT[i.severity] ?? 'bg-ink-soft'}`}
        />
        <div className="min-w-0 text-sm">
          <div className="font-medium">{i.title}</div>
          <div className="mt-0.5">{i.what_happened}</div>
          <div className="mt-1">
            <span className="text-ink-soft">Suggested action: </span>
            {i.recommended_action}
          </div>
          <details className="mt-2 text-xs">
            <summary className="cursor-pointer text-ink-soft">Evidence and details</summary>
            <div className="mt-2 flex flex-col gap-1.5">
              <div>
                {i.evidence.map((e, n) => (
                  <div key={n} className="flex justify-between gap-4 py-0.5">
                    <span className="text-ink-soft">{e.label}</span>
                    <span className="text-right">{e.value}</span>
                  </div>
                ))}
              </div>
              <div>
                <span className="text-ink-soft">Period: </span>
                {i.period.label} ({i.period.start} to {i.period.end})
              </div>
              {cmp && (
                <div>
                  <span className="text-ink-soft">
                    Compared with {cmp.period.label.toLowerCase()} ({cmp.period.start} to{' '}
                    {cmp.period.end}):{' '}
                  </span>
                  {cmp.label} {fmtValue(cmp.unit, cmp.current, money)} vs{' '}
                  {fmtValue(cmp.unit, cmp.previous, money)}
                  {cmpChange ? ` (${cmpChange})` : ''}
                </div>
              )}
              <div>
                <span className="text-ink-soft">Why it matters: </span>
                {i.why_it_matters}
              </div>
              {i.impact && (
                <div>
                  <span className="text-ink-soft">Potential impact: </span>
                  {money(i.impact.amount)} — {i.impact.basis}
                </div>
              )}
              <div>
                <span className="text-ink-soft">Confidence: </span>
                {i.confidence.level}
                {i.confidence.limitations.length > 0 && (
                  <ul className="mt-1 list-disc pl-4 text-ink-soft">
                    {i.confidence.limitations.map((l, n) => (
                      <li key={n}>{l}</li>
                    ))}
                  </ul>
                )}
              </div>
              {i.follow_up && (
                <div>
                  <span className="text-ink-soft">Follow up: </span>
                  {i.follow_up}
                </div>
              )}
            </div>
          </details>
        </div>
      </div>
    </div>
  )
}

function AreaBlock({ area, money }: { area: IntelligenceArea; money: Fmt }) {
  const [open, setOpen] = useState(false)
  const sorted = [...area.insights].sort((a, b) => rank(a.severity) - rank(b.severity))
  const worst = sorted[0]?.severity ?? null
  const summary =
    area.status === 'unavailable'
      ? 'Not available'
      : area.insights.length === 0
        ? 'No findings'
        : `${area.insights.length} finding${area.insights.length === 1 ? '' : 's'}`
  return (
    <div className="border-t border-rule">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 py-3 text-left"
      >
        <span className="flex items-center gap-2 text-sm font-medium">
          <span
            aria-hidden
            className={`h-2 w-2 rounded-full ${worst ? DOT[worst] : 'border border-rule'}`}
          />
          {area.title}
        </span>
        <span className="text-xs text-ink-soft">
          {summary}
          {area.status === 'partial' ? ' · partial' : ''} {open ? '−' : '+'}
        </span>
      </button>
      {open && (
        <div className="pb-3">
          {area.notes.map((n, idx) => (
            <div key={idx} className="mb-1 text-xs text-ink-soft">
              {n}
            </div>
          ))}
          {area.kpis.length > 0 && (
            <div className="my-2 grid grid-cols-2 gap-3 md:grid-cols-4">
              {area.kpis.map((k) => (
                <KpiTile key={k.label} k={k} money={money} />
              ))}
            </div>
          )}
          {sorted.map((i) => (
            <InsightBlock key={i.kind} i={i} money={money} />
          ))}
          {area.insights.length === 0 && area.status !== 'unavailable' && (
            <div className="py-2 text-xs text-ink-soft">Nothing unusual found for this period.</div>
          )}
          {area.missing.length > 0 && (
            <div className="mt-2 text-xs text-ink-soft">
              <div className="font-medium">
                {area.status === 'unavailable' ? "Why it isn't available" : "What's missing"}
              </div>
              <ul className="mt-1 list-disc pl-4">
                {area.missing.map((m, n) => (
                  <li key={n}>{m}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function IntelligencePanel() {
  const money = useCurrencyFormatter()
  const [days, setDays] = useState(30)
  const [report, setReport] = useState<IntelligenceReportOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // Never leave the previous period's figures on screen while the new one loads.
    setReport(null)
    setError(null)
    reportsApi
      .intelligence(days)
      .then((r) => {
        if (!cancelled) setReport(r)
      })
      .catch(() => {
        if (!cancelled) {
          setError('Business intelligence could not be loaded right now. Please try again.')
        }
      })
    return () => {
      cancelled = true
    }
  }, [days])

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {PERIODS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => setDays(p)}
            className={`border px-3 py-1 text-sm ${
              p === days ? 'border-brass font-medium text-ink' : 'border-rule text-ink-soft'
            }`}
          >
            {p} days
          </button>
        ))}
      </div>
      {error && <p className="text-sm text-stamp-red">{error}</p>}
      {!error && !report && <p className="text-sm text-ink-soft">Loading…</p>}
      {report && (
        <div>
          <div className="mb-2 text-xs text-ink-soft">
            {report.period.start} to {report.period.end}, compared with{' '}
            {report.previous_period.start} to {report.previous_period.end}
          </div>
          {report.areas.map((a) => (
            <AreaBlock key={a.id} area={a} money={money} />
          ))}
        </div>
      )}
    </div>
  )
}
