import { describe, expect, it } from 'vitest'
import { categorySpendBreakdown } from './purchasingAnalytics'
import type { PurchaseOrderOut } from '../types/api'

function item(overrides: Partial<PurchaseOrderOut['items'][number]> = {}) {
  return {
    id: 1,
    product_id: 1,
    product_name: 'Test Product',
    category_name: null,
    quantity_ordered: 1,
    unit_cost_expected: 1,
    quantity_received: null,
    unit_cost_actual: null,
    batch_id: null,
    ...overrides,
  }
}

function po(items: ReturnType<typeof item>[]): PurchaseOrderOut {
  return {
    id: 1,
    supplier_id: 1,
    status: 'RECEIVED',
    created_by_user_id: 1,
    notes: null,
    created_at: '2026-01-01T00:00:00Z',
    sent_at: null,
    in_transit_at: null,
    received_at: null,
    reconciled_at: null,
    items,
  }
}

describe('categorySpendBreakdown', () => {
  it('returns an empty list for no orders', () => {
    expect(categorySpendBreakdown([])).toEqual([])
  })

  it('groups spend by category using actual quantity and cost when received', () => {
    const orders = [
      po([
        item({ category_name: 'Antibiotics', quantity_received: 10, unit_cost_actual: 5 }),
        item({ category_name: 'Antibiotics', quantity_received: 2, unit_cost_actual: 5 }),
      ]),
    ]

    const result = categorySpendBreakdown(orders)

    expect(result).toEqual([{ category: 'Antibiotics', total: 60, itemCount: 2 }])
  })

  it('falls back to ordered quantity and expected cost when not yet received', () => {
    const orders = [
      po([item({ category_name: 'Painkillers', quantity_ordered: 4, unit_cost_expected: 3 })]),
    ]

    expect(categorySpendBreakdown(orders)).toEqual([
      { category: 'Painkillers', total: 12, itemCount: 1 },
    ])
  })

  it('groups items with no category under "Uncategorised"', () => {
    const orders = [
      po([item({ category_name: null, quantity_received: 1, unit_cost_actual: 9 })]),
    ]

    expect(categorySpendBreakdown(orders)).toEqual([
      { category: 'Uncategorised', total: 9, itemCount: 1 },
    ])
  })

  it('sums across multiple purchase orders, not just within one', () => {
    const orders = [
      po([item({ category_name: 'Antibiotics', quantity_received: 1, unit_cost_actual: 10 })]),
      po([item({ category_name: 'Antibiotics', quantity_received: 1, unit_cost_actual: 15 })]),
    ]

    expect(categorySpendBreakdown(orders)).toEqual([
      { category: 'Antibiotics', total: 25, itemCount: 2 },
    ])
  })

  it('sorts categories by total spend, highest first', () => {
    const orders = [
      po([
        item({ category_name: 'Painkillers', quantity_received: 1, unit_cost_actual: 5 }),
        item({ category_name: 'Antibiotics', quantity_received: 1, unit_cost_actual: 50 }),
      ]),
    ]

    expect(categorySpendBreakdown(orders).map((r) => r.category)).toEqual([
      'Antibiotics',
      'Painkillers',
    ])
  })
})
