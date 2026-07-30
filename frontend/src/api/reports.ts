import { api, downloadExport } from './client'
import type {
  ExpiredStockReportOut,
  FastSlowMoversOut,
  KpiDashboardOut,
  ProfitReportOut,
  ReceivingDiscrepancyReportOut,
  SalesSummaryOut,
  StockTakeHistoryOut,
} from '../types/api'

export const reportsApi = {
  kpiDashboard: (startDate: string, endDate: string) =>
    api.get<KpiDashboardOut>('/reports/kpi-dashboard', {
      start_date: startDate,
      end_date: endDate,
    }),
  salesSummary: (startDate: string, endDate: string, groupBy: 'day' | 'month') =>
    api.get<SalesSummaryOut>('/reports/sales', {
      start_date: startDate,
      end_date: endDate,
      group_by: groupBy,
    }),
  profit: (startDate: string, endDate: string) =>
    api.get<ProfitReportOut>('/reports/profit', { start_date: startDate, end_date: endDate }),
  expiredStock: () => api.get<ExpiredStockReportOut>('/reports/expired-stock'),
  fastSlowMovers: (days: number, limit: number) =>
    api.get<FastSlowMoversOut>('/reports/fast-slow-movers', { days, limit }),
  receivingDiscrepancies: () =>
    api.get<ReceivingDiscrepancyReportOut>('/reports/receiving-discrepancies'),
  stockTakeHistory: () => api.get<StockTakeHistoryOut>('/reports/stock-take-history'),
}

// Kept under its original name here so ReportsPage.tsx's existing
// import doesn't need to change -- the real implementation now lives
// in client.ts as the shared, generic downloadExport.
export const downloadReportExport = downloadExport
