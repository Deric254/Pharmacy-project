import type { PurchaseOrderOut } from '../types/api'

export interface CategorySpend {
  category: string
  total: number
  itemCount: number
}

const UNCATEGORISED = 'Uncategorised'

// Same "as they are bought" line-total math PurchasingPage's
// PODetailModal already uses per item (quantity actually received,
// times the actual unit cost once known), just summed by category
// across every order already loaded on the Purchasing page -- no
// extra network round trip needed to produce this breakdown.
export function categorySpendBreakdown(orders: PurchaseOrderOut[]): CategorySpend[] {
  const totals = new Map<string, CategorySpend>()
  for (const po of orders) {
    for (const item of po.items) {
      const category = item.category_name ?? UNCATEGORISED
      const quantity = item.quantity_received ?? item.quantity_ordered
      const unitCost = item.unit_cost_actual ?? item.unit_cost_expected
      const existing = totals.get(category) ?? { category, total: 0, itemCount: 0 }
      existing.total += quantity * unitCost
      existing.itemCount += 1
      totals.set(category, existing)
    }
  }
  return [...totals.values()].sort((a, b) => b.total - a.total)
}
