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

export type PurchaseOrderStatus = 'DRAFT' | 'SENT' | 'IN_TRANSIT' | 'RECEIVED' | 'RECONCILED'

export interface SupplierOut {
  id: number
  name: string
  contact_phone: string | null
  contact_email: string | null
  address: string | null
  notes: string | null
  created_at: string
  balance_owed: number
}

export interface SupplierCreate {
  name: string
  contact_phone?: string | null
  contact_email?: string | null
  address?: string | null
  notes?: string | null
}

export interface PaymentRecordRequest {
  amount: number
  notes?: string | null
}

export interface PurchaseOrderItemCreate {
  product_id: number
  quantity_ordered: number
  unit_cost_expected: number
}

export interface PurchaseOrderCreate {
  supplier_id: number
  items: PurchaseOrderItemCreate[]
  notes?: string | null
}

export interface PurchaseOrderItemOut {
  id: number
  product_id: number
  quantity_ordered: number
  unit_cost_expected: number
  quantity_received: number | null
  unit_cost_actual: number | null
  batch_id: number | null
}

export interface PurchaseOrderOut {
  id: number
  supplier_id: number
  status: PurchaseOrderStatus
  created_by_user_id: number
  notes: string | null
  created_at: string
  sent_at: string | null
  in_transit_at: string | null
  received_at: string | null
  reconciled_at: string | null
  items: PurchaseOrderItemOut[]
}

export interface ReceivingLine {
  item_id: number
  batch_number: string
  expiry_date: string
  quantity_received: number
  unit_cost_actual: number
}

export interface ReceiveRequest {
  lines: ReceivingLine[]
}

export interface ReceivingVarianceOut {
  item_id: number
  product_id: number
  quantity_ordered: number
  quantity_received: number
  variance: number
}

export interface ReceiveResponse {
  purchase_order: PurchaseOrderOut
  variances: ReceivingVarianceOut[]
}

export interface ReconcileRequest {
  payment_amount?: number | null
  notes?: string | null
}

export type KanbanBoard = Record<string, PurchaseOrderOut[]>

export interface CustomerOut {
  id: number
  name: string
  phone: string | null
  email: string | null
  loyalty_points: number
  created_at: string
}

export interface CustomerCreate {
  name: string
  phone?: string | null
  email?: string | null
}

export interface PurchaseHistoryEntryOut {
  sale_id: number
  total_amount: number
  created_at: string
}

export type StockTakeStatus = 'OPEN' | 'CLOSED'

export interface StockTakeCreate {
  product_ids?: number[] | null
  notes?: string | null
}

export interface CountSubmit {
  physical_qty: number
  reason?: AdjustmentReason | null
  notes?: string | null
}

export interface StockTakeItemOut {
  id: number
  batch_id: number
  product_id: number
  expected_qty: number
  physical_qty: number | null
  reason: string | null
  counted_by_user_id: number | null
  counted_at: string | null
  approved_by_user_id: number | null
  approved_at: string | null
  variance: number | null
}

export interface StockTakeOut {
  id: number
  status: StockTakeStatus
  initiated_by_user_id: number
  started_at: string
  closed_at: string | null
  notes: string | null
  items: StockTakeItemOut[]
}

export interface SalesSummaryEntry {
  period: string
  sale_count: number
  total_revenue: number
  total_discount: number
}

export interface SalesSummaryOut {
  entries: SalesSummaryEntry[]
  total_revenue: number
  total_sale_count: number
}

export interface ProfitReportOut {
  start_date: string
  end_date: string
  total_revenue: number
  total_cost: number
  total_profit: number
  profit_margin_percent: number
}

export interface ExpiredStockEntry {
  batch_id: number
  product_id: number
  product_name: string
  batch_number: string
  expiry_date: string
  days_expired: number
  qty_remaining: number
  value_at_cost: number
}

export interface ExpiredStockReportOut {
  entries: ExpiredStockEntry[]
  total_value: number
  recommendation: string
}

export interface ProductMovementEntry {
  product_id: number
  name: string
  quantity_sold: number
}

export interface NeverSoldEntry {
  product_id: number
  name: string
}

export interface FastSlowMoversOut {
  period_days: number
  fast_movers: ProductMovementEntry[]
  slow_movers: ProductMovementEntry[]
  never_sold: NeverSoldEntry[]
}

export interface ReceivingDiscrepancyEntry {
  purchase_order_id: number
  item_id: number
  product_id: number
  product_name: string
  quantity_ordered: number
  quantity_received: number
  variance: number
}

export interface ReceivingDiscrepancyReportOut {
  entries: ReceivingDiscrepancyEntry[]
  recommendation: string
}

export interface StockTakeHistoryEntry {
  stock_take_id: number
  started_at: string
  closed_at: string | null
  shrinkage_value: number
  shrinkage_percent: number
}

export interface StockTakeHistoryOut {
  entries: StockTakeHistoryEntry[]
}

export interface PermissionOut {
  code: string
  description: string
}

export interface RoleDetailOut {
  id: number
  name: string
  description: string
  is_system: boolean
  permissions: string[]
  user_count: number
}

export interface RoleCreate {
  name: string
  description?: string
  permission_codes: string[]
}

export interface RoleUpdate {
  name?: string
  description?: string
  permission_codes?: string[]
}

export interface ApiErrorBody {
  detail?: string | { msg: string; loc: (string | number)[] }[]
}
