import { describe, expect, it } from 'vitest'
import { calculateSubtotal, calculateTotal, cartSignature, type CartLine } from './cartMath'
import type { ProductOut } from '../types/api'

function product(overrides: Partial<ProductOut> = {}): ProductOut {
  return {
    id: 1,
    name: 'Test Product',
    default_selling_price: 10,
    unit: 'unit',
    is_active: true,
    total_qty_available: 100,
    ...overrides,
  } as ProductOut
}

function line(id: number, price: number, quantity: number): CartLine {
  return { product: product({ id, default_selling_price: price }), quantity }
}

describe('calculateSubtotal', () => {
  it('is 0 for an empty cart', () => {
    expect(calculateSubtotal([])).toBe(0)
  })

  it('is price times quantity for a single line', () => {
    expect(calculateSubtotal([line(1, 10, 3)])).toBe(30)
  })

  it('sums multiple lines', () => {
    expect(calculateSubtotal([line(1, 10, 2), line(2, 5, 4)])).toBe(40)
  })

  it('contributes 0 for a zero-quantity line without erroring', () => {
    expect(calculateSubtotal([line(1, 10, 0)])).toBe(0)
  })

  it('handles fractional (real-world currency) prices correctly', () => {
    expect(calculateSubtotal([line(1, 9.99, 3)])).toBeCloseTo(29.97, 10)
  })

  it('handles the classic 0.1 + 0.2 floating-point case without visible drift', () => {
    // Not exactly 0.3 in IEEE 754 -- the point of this test is that
    // the result is close enough for currency display (toBeCloseTo),
    // not that JS floating point becomes exact.
    expect(calculateSubtotal([line(1, 0.1, 1), line(2, 0.2, 1)])).toBeCloseTo(0.3, 10)
  })

  it('handles a very large quantity without overflow or precision loss', () => {
    expect(calculateSubtotal([line(1, 10, 1_000_000)])).toBe(10_000_000)
  })

  it('handles a cart with many distinct lines (stress case)', () => {
    const bigCart = Array.from({ length: 2000 }, (_, i) => line(i, 1, 1))
    expect(calculateSubtotal(bigCart)).toBe(2000)
  })

  it('never returns a negative number for any non-negative inputs', () => {
    expect(calculateSubtotal([line(1, 5, 5)])).toBeGreaterThanOrEqual(0)
  })
})

describe('calculateTotal', () => {
  it('equals the subtotal when there is no discount', () => {
    expect(calculateTotal([line(1, 10, 2)], 0)).toBe(20)
  })

  it('subtracts a discount smaller than the subtotal', () => {
    expect(calculateTotal([line(1, 10, 2)], 5)).toBe(15)
  })

  it('is exactly 0 when the discount equals the subtotal', () => {
    expect(calculateTotal([line(1, 10, 2)], 20)).toBe(0)
  })

  it('clamps to 0 rather than going negative when discount exceeds subtotal', () => {
    expect(calculateTotal([line(1, 10, 2)], 999)).toBe(0)
  })

  it('is 0 for an empty cart regardless of the discount amount', () => {
    expect(calculateTotal([], 50)).toBe(0)
    expect(calculateTotal([], 0)).toBe(0)
  })

  it('never goes negative even with a negative discount value', () => {
    // The UI clamps discount input to >= 0, but the pure function
    // must not silently produce a nonsensical negative charge (or a
    // total LARGER than the subtotal) if it's ever called with bad
    // input from somewhere else.
    const result = calculateTotal([line(1, 10, 1)], -5)
    expect(result).toBeGreaterThanOrEqual(0)
  })

  it('handles fractional discount amounts (e.g. a percentage-derived value)', () => {
    expect(calculateTotal([line(1, 100, 1)], 12.5)).toBeCloseTo(87.5, 10)
  })
})

describe('cartSignature', () => {
  it('is identical for the same cart regardless of item order', () => {
    const a = cartSignature([line(1, 10, 1), line(2, 5, 2)], 0, 'CASH', 'none')
    const b = cartSignature([line(2, 5, 2), line(1, 10, 1)], 0, 'CASH', 'none')
    expect(a).toBe(b)
  })

  it('changes when a quantity changes', () => {
    const a = cartSignature([line(1, 10, 1)], 0, 'CASH', 'none')
    const b = cartSignature([line(1, 10, 2)], 0, 'CASH', 'none')
    expect(a).not.toBe(b)
  })

  it('changes when the discount changes', () => {
    const a = cartSignature([line(1, 10, 1)], 0, 'CASH', 'none')
    const b = cartSignature([line(1, 10, 1)], 5, 'CASH', 'none')
    expect(a).not.toBe(b)
  })

  it('changes when the payment method changes', () => {
    const a = cartSignature([line(1, 10, 1)], 0, 'CASH', 'none')
    const b = cartSignature([line(1, 10, 1)], 0, 'MPESA', 'none')
    expect(a).not.toBe(b)
  })

  it('changes when the customer changes', () => {
    const a = cartSignature([line(1, 10, 1)], 0, 'CASH', 'customer-1')
    const b = cartSignature([line(1, 10, 1)], 0, 'CASH', 'customer-2')
    expect(a).not.toBe(b)
  })

  it('is stable and deterministic for an empty cart', () => {
    const a = cartSignature([], 0, 'CASH', 'none')
    const b = cartSignature([], 0, 'CASH', 'none')
    expect(a).toBe(b)
  })

  it('distinguishes two genuinely different sales that happen to have the same subtotal', () => {
    // 2 units of a 10-currency item vs 1 unit of a 20-currency item
    // -- same subtotal (20), must NOT produce the same signature,
    // since they are different carts and would need different
    // idempotency keys.
    const a = cartSignature([line(1, 10, 2)], 0, 'CASH', 'none')
    const b = cartSignature([line(2, 20, 1)], 0, 'CASH', 'none')
    expect(a).not.toBe(b)
  })

  it('handles a large cart (stress case) without error and stays order-independent', () => {
    const cart = Array.from({ length: 500 }, (_, i) => line(i, 1, 1))
    const shuffled = [...cart].reverse()
    expect(cartSignature(cart, 0, 'CASH', 'none')).toBe(
      cartSignature(shuffled, 0, 'CASH', 'none'),
    )
  })
})
