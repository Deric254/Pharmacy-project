import type { PurchaseOrderOut } from '../types/api'

interface CategorySpend {
  category: string
  total: number
  itemCount: number
  percentOfTotal: number
}

const UNCATEGORISED = 'Uncategorised'

export function categorySpendBreakdown(orders: PurchaseOrderOut[]): CategorySpend[] {
  const totals = new Map<string, Omit<CategorySpend, 'percentOfTotal'>>()
  let grandTotal = 0
  for (const po of orders) {
    if (po.status === 'REVERSED') continue
    for (const item of po.items) {
      const category = item.category_name ?? UNCATEGORISED
      const quantity = item.quantity_received ?? item.quantity_ordered
      const unitCost = item.unit_cost_actual ?? item.unit_cost_expected
      const lineTotal = quantity * unitCost
      const existing = totals.get(category) ?? { category, total: 0, itemCount: 0 }
      existing.total += lineTotal
      existing.itemCount += 1
      totals.set(category, existing)
      grandTotal += lineTotal
    }
  }
  return [...totals.values()]
    .map((entry) => ({
      ...entry,
      percentOfTotal: grandTotal > 0 ? Math.round((entry.total / grandTotal) * 1000) / 10 : 0,
    }))
    .sort((a, b) => b.total - a.total)
}
