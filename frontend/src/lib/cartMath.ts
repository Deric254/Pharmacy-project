import type { ProductOut } from '../types/api'

export interface CartLine {
  product: ProductOut
  quantity: number
}

/**
 * The subtotal shown in the cart before any discount -- sum of each
 * line's own selling price times quantity. An empty cart is 0, not
 * NaN or undefined, so callers never need a special case for "cart
 * hasn't been touched yet".
 */
export function calculateSubtotal(cart: CartLine[]): number {
  return cart.reduce((sum, line) => sum + line.product.default_selling_price * line.quantity, 0)
}

/**
 * The final charge amount: subtotal minus discount, floored at 0.
 * Floored rather than allowed to go negative -- a discount typed in
 * before the cart is fully built, or one that briefly exceeds the
 * subtotal while items are still being removed, must never produce a
 * negative total a payment method would have to somehow charge.
 */
export function calculateTotal(cart: CartLine[], discount: number): number {
  const subtotal = calculateSubtotal(cart)
  return Math.max(0, subtotal - discount)
}

/**
 * The stable identifier for what's actually being charged, used to
 * decide whether a checkout attempt is a genuine retry (reuse the
 * same idempotency key) or a new sale (needs a fresh one). Order of
 * cart lines must not matter -- adding the same two products in a
 * different order is still the same sale -- so lines are sorted by
 * product id before joining. Distinct from a deep-equality check:
 * this is deliberately a compact string, not a full object diff,
 * because it only needs to answer "did what's being charged change",
 * not "what exactly changed".
 */
export function cartSignature(
  cart: CartLine[],
  discount: number,
  paymentMethod: string,
  customerKey: string,
): string {
  const items = cart
    .map((l) => `${l.product.id}:${l.quantity}`)
    .sort()
    .join(',')
  return `${items}|${discount}|${paymentMethod}|${customerKey}`
}
