import { api, downloadFile, uploadFile } from './client'
import type {
  AdjustmentOut,
  AdjustmentRequest,
  BatchOut,
  CountSubmit,
  CustomerCreate,
  CustomerOut,
  ExpiringBatchOut,
  KanbanBoard,
  LowStockProductOut,
  PaymentRecordRequest,
  ProductCreate,
  ProductOut,
  ProductUpdate,
  PurchaseHistoryEntryOut,
  PurchaseOrderCreate,
  PurchaseOrderOut,
  ReceiveRequest,
  ReceiveResponse,
  ReconcileRequest,
  ReconciliationIssueOut,
  RefundOut,
  RefundRequest,
  SaleCreate,
  SaleOut,
  SalePage,
  StockTakeCreate,
  StockTakeItemOut,
  StockTakeOut,
  StockValuationOut,
  SupplierCreate,
  SupplierOut,
} from '../types/api'

export const productsApi = {
  list: (search?: string) => api.get<ProductOut[]>('/products', { search }),
  getByBarcode: (barcode: string) =>
    api.get<ProductOut>(`/products/barcode/${encodeURIComponent(barcode)}`),
  batches: (productId: number) => api.get<BatchOut[]>(`/products/${productId}/batches`),
  create: (payload: ProductCreate) => api.post<ProductOut>('/products', payload),
  update: (productId: number, payload: ProductUpdate) =>
    api.patch<ProductOut>(`/products/${productId}`, payload),
  deactivate: (productId: number) => api.delete<void>(`/products/${productId}`),
  downloadImportTemplate: () =>
    downloadFile('/products/import-template', 'product-import-template.xlsx'),
  importFromExcel: (file: File) =>
    uploadFile<{ created: number }>('/products/import', file),
}

export const salesApi = {
  create: (payload: SaleCreate) => api.post<SaleOut>('/sales', payload),
  get: (id: number) => api.get<SaleOut>(`/sales/${id}`),
  list: (params: { start_date?: string; end_date?: string; limit?: number; offset?: number }) =>
    api.get<SalePage>('/sales', params),
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

export const suppliersApi = {
  list: () => api.get<SupplierOut[]>('/suppliers'),
  get: (id: number) => api.get<SupplierOut>(`/suppliers/${id}`),
  create: (payload: SupplierCreate) => api.post<SupplierOut>('/suppliers', payload),
  recordPayment: (supplierId: number, payload: PaymentRecordRequest) =>
    api.post<SupplierOut>(`/suppliers/${supplierId}/payments`, payload),
}

export const purchaseOrdersApi = {
  kanban: () => api.get<KanbanBoard>('/purchase-orders/kanban'),
  get: (id: number) => api.get<PurchaseOrderOut>(`/purchase-orders/${id}`),
  create: (payload: PurchaseOrderCreate) =>
    api.post<PurchaseOrderOut>('/purchase-orders', payload),
  send: (id: number) => api.post<PurchaseOrderOut>(`/purchase-orders/${id}/send`),
  markInTransit: (id: number) =>
    api.post<PurchaseOrderOut>(`/purchase-orders/${id}/mark-in-transit`),
  receive: (id: number, payload: ReceiveRequest) =>
    api.post<ReceiveResponse>(`/purchase-orders/${id}/receive`, payload),
  reconcile: (id: number, payload: ReconcileRequest) =>
    api.post<PurchaseOrderOut>(`/purchase-orders/${id}/reconcile`, payload),
}

export const customersApi = {
  list: (search?: string) => api.get<CustomerOut[]>('/customers', { search }),
  get: (id: number) => api.get<CustomerOut>(`/customers/${id}`),
  getByPhone: (phone: string) =>
    api.get<CustomerOut>(`/customers/phone/${encodeURIComponent(phone)}`),
  create: (payload: CustomerCreate) => api.post<CustomerOut>('/customers', payload),
  purchaseHistory: (id: number) =>
    api.get<PurchaseHistoryEntryOut[]>(`/customers/${id}/purchase-history`),
}

export const stockTakesApi = {
  list: () => api.get<StockTakeOut[]>('/stock-takes'),
  get: (id: number) => api.get<StockTakeOut>(`/stock-takes/${id}`),
  initiate: (payload: StockTakeCreate) => api.post<StockTakeOut>('/stock-takes', payload),
  submitCount: (stockTakeId: number, itemId: number, payload: CountSubmit) =>
    api.post<StockTakeItemOut>(
      `/stock-takes/${stockTakeId}/items/${itemId}/count`,
      payload,
    ),
  approveVariance: (stockTakeId: number, itemId: number) =>
    api.post<StockTakeItemOut>(`/stock-takes/${stockTakeId}/items/${itemId}/approve`),
  close: (stockTakeId: number) => api.post<StockTakeOut>(`/stock-takes/${stockTakeId}/close`),
}
