import type { ReactNode } from 'react'

/**
 * Recharts' ResponsiveContainer only ever fills the width it's given --
 * it has no concept of "too many data points for this width," so past a
 * certain density, category labels along the x-axis start overlapping
 * or getting skipped. The fix isn't shrinking the labels further (they're
 * already near the readable floor); it's giving the chart more physical
 * width than the panel and letting the panel scroll horizontally, the
 * same way a spreadsheet does. Below the point-count where everything
 * already fits, this renders identically to a plain ResponsiveContainer
 * -- min-width simply loses to the parent's 100% width.
 */
export function ChartScrollArea({
  itemCount,
  minPxPerItem,
  children,
}: {
  itemCount: number
  minPxPerItem: number
  children: ReactNode
}) {
  const minWidth = itemCount * minPxPerItem

  return (
    <div className="overflow-x-auto">
      <div style={{ width: '100%', minWidth: `${minWidth}px` }}>{children}</div>
    </div>
  )
}
