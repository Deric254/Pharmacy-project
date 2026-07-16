// Mirrors backend/app/schemas/*.py exactly. Financial fields are
// display-only here -- the frontend never recomputes totals; it shows
// whatever the server returns, full stop.

export interface UserOut {
  id: number
  full_name: string
  username: string
  role_name: string
  permissions: string[]
  is_active: boolean
}

export interface TokenResponse {
  access_token: string
  token_type: string
}

export interface ProductOut {
  id: number
  name: string
  barcode: string | null
  unit: string
  category_id: number | null
  reorder_point: number
  default_selling_price: number
  is_active: boolean
  created_at: string
  total_qty_available: number
}

export interface BatchOut {
  id: number
  product_id: number
  batch_number: string
  expiry_date: string
  qty_received: number
  qty_remaining: number
  cost_price: number
  created_at: string
}

export type AdjustmentReason =
  | 'DAMAGED'
  | 'THEFT_OR_LOSS'
  | 'MISCOUNT'
  | 'EXPIRED'
  | 'DATA_ENTRY_ERROR'
  | 'OTHER'

export interface AdjustmentRequest {
  batch_id: number
  quantity_delta: number
  reason: AdjustmentReason
  notes?: string | null
}

export interface AdjustmentOut {
  batch_id: number
  quantity_delta: number
  qty_remaining_after: number
  reason: AdjustmentReason
}

export interface ReconciliationIssueOut {
  batch_id: number
  product_id: number
  qty_remaining: number
  ledger_sum: number
  discrepancy: number
}

export type PaymentMethod = 'CASH' | 'MPESA' | 'CARD'

export interface SaleItemRequest {
  product_id: number
  quantity: number
}

export interface PaymentRequest {
  method: PaymentMethod
  amount: number
  reference?: string | null
}

export interface SaleCreate {
  items: SaleItemRequest[]
  payments: PaymentRequest[]
  discount_amount: number
  customer_id: number | null
}

export interface SaleItemOut {
  id: number
  product_id: number
  batch_id: number
  quantity: number
  unit_price: number
  line_total: number
}

export interface PaymentOut {
  method: PaymentMethod
  amount: number
  reference: string | null
}

export interface SaleOut {
  id: number
  cashier_user_id: number
  customer_id: number | null
  subtotal: number
  discount_amount: number
  total_amount: number
  created_at: string
  items: SaleItemOut[]
  payments: PaymentOut[]
}

export interface LowStockProductOut {
  product_id: number
  name: string
  barcode: string | null
  total_qty_available: number
  reorder_point: number
}

export interface ExpiringBatchOut {
  batch_id: number
  product_id: number
  product_name: string
  batch_number: string
  expiry_date: string
  days_remaining: number
  qty_remaining: number
}

export interface StockValuationOut {
  total_value: number
  by_product: { product_id: number; name: string; qty_on_hand: number; value: number }[]
}

export type RefundReason = 'CUSTOMER_RETURN' | 'DAMAGED' | 'WRONG_ITEM_SOLD' | 'EXPIRED' | 'OTHER'

export interface RefundItemRequest {
  sale_item_id: number
  quantity: number
  restock: boolean
}

export interface RefundRequest {
  reason: RefundReason
  method: PaymentMethod
  notes?: string | null
  items: RefundItemRequest[]
}

export interface RefundItemOut {
  sale_item_id: number
  product_id: number
  batch_id: number
  quantity: number
  unit_price: number
  line_total: number
  restocked: boolean
}

export interface RefundOut {
  id: number
  sale_id: number
  processed_by_user_id: number
  reason: RefundReason
  method: PaymentMethod
  notes: string | null
  total_amount: number
  created_at: string
  items: RefundItemOut[]
}

export interface ApiErrorBody {
  detail?: string | { msg: string; loc: (string | number)[] }[]
}
