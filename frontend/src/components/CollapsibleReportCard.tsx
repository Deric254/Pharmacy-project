import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'

export function CollapsibleReportCard({
  title,
  reportTab,
  children,
}: {
  title: string
  reportTab: string
  children: ReactNode
}) {
  return (
    <details className="mb-6 ledger-panel p-4">
      <summary className="flex cursor-pointer list-none items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-ink-soft">{title}</span>
        <Link
          to={`/reports?tab=${reportTab}`}
          onClick={(e) => e.stopPropagation()}
          className="text-xs text-brass underline decoration-dotted hover:text-ink"
        >
          Full report →
        </Link>
      </summary>
      <div className="mt-3">{children}</div>
    </details>
  )
}
