import { api, downloadExport } from './client'
import type {
  ExpiredStockReportOut,
  FastSlowMoversOut,
  KpiDashboardOut,
  ProfitReportOut,
  ReceivingDiscrepancyReportOut,
  RevenuePotentialOut,
  RevenueTrendOut,
  SalesSummaryOut,
  StockTakeHistoryOut,
  TopCustomersOut,
} from '../types/api'

export const reportsApi = {
  revenuePotential: () => api.get<RevenuePotentialOut>('/reports/revenue-potential'),
  revenueTrend: (startDate: string, endDate: string) =>
    api.get<RevenueTrendOut>('/reports/revenue-trend', {
      start_date: startDate,
      end_date: endDate,
    }),
  topCustomers: (startDate: string, endDate: string, limit = 20) =>
    api.get<TopCustomersOut>('/reports/top-customers', {
      start_date: startDate,
      end_date: endDate,
      limit,
    }),
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
