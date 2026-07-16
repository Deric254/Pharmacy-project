import { api } from './client'
import type {
  AdjustmentOut,
  AdjustmentRequest,
  BatchOut,
  ExpiringBatchOut,
  LowStockProductOut,
  ProductOut,
  ReconciliationIssueOut,
  RefundOut,
  RefundRequest,
  SaleCreate,
  SaleOut,
  StockValuationOut,
} from '../types/api'

export const productsApi = {
  list: (search?: string) => api.get<ProductOut[]>('/products', { search }),
  getByBarcode: (barcode: string) =>
    api.get<ProductOut>(`/products/barcode/${encodeURIComponent(barcode)}`),
  batches: (productId: number) => api.get<BatchOut[]>(`/products/${productId}/batches`),
}

export const salesApi = {
  create: (payload: SaleCreate) => api.post<SaleOut>('/sales', payload),
  get: (id: number) => api.get<SaleOut>(`/sales/${id}`),
  refund: (saleId: number, payload: RefundRequest) =>
    api.post<RefundOut>(`/sales/${saleId}/refunds`, payload),
  listRefunds: (saleId: number) => api.get<RefundOut[]>(`/sales/${saleId}/refunds`),
}

export const inventoryApi = {
  lowStock: () => api.get<LowStockProductOut[]>('/inventory/low-stock'),
  expiring: (withinDays?: number) =>
    api.get<ExpiringBatchOut[]>('/inventory/expiring', { within_days: withinDays }),
  valuation: () => api.get<StockValuationOut>('/inventory/valuation'),
  adjust: (payload: AdjustmentRequest) => api.post<AdjustmentOut>('/inventory/adjustments', payload),
  reconcile: () => api.get<ReconciliationIssueOut[]>('/inventory/reconcile'),
}
