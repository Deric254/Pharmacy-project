import type { ReactNode } from 'react'

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
