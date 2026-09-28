import type { PurchaseOrderOut } from '../types/api'

interface CategorySpend {
  category: string
  total: number
  itemCount: number
  // This category's share of total spend across every order passed in --
  // "if I invest in category X, what percentage of my purchasing does it
  // actually take up", answered directly rather than eyeballed off a chart.
  percentOfTotal: number
}

const UNCATEGORISED = 'Uncategorised'

// Same "as they are bought" line-total math PurchasingPage's
// PODetailModal already uses per item (quantity actually received,
// times the actual unit cost once known), just summed by category
// across every order already loaded on the Purchasing page -- no
// extra network round trip needed to produce this breakdown.
export function categorySpendBreakdown(orders: PurchaseOrderOut[]): CategorySpend[] {
  const totals = new Map<string, Omit<CategorySpend, 'percentOfTotal'>>()
  let grandTotal = 0
  for (const po of orders) {
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
