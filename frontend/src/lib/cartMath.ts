import type { ProductOut } from '../types/api'

export interface CartLine {
  product: ProductOut
  quantity: number
}

export function calculateSubtotal(cart: CartLine[]): number {
  return cart.reduce(
    (sum, line) => sum + (line.product.current_selling_price ?? 0) * line.quantity,
    0,
  )
}

export function calculateTotal(cart: CartLine[], discount: number): number {
  const subtotal = calculateSubtotal(cart)
  return Math.max(0, subtotal - discount)
}

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
