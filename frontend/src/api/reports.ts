import { api, getAccessToken, ApiError } from './client'
import type {
  ExpiredStockReportOut,
  FastSlowMoversOut,
  ProfitReportOut,
  ReceivingDiscrepancyReportOut,
  SalesSummaryOut,
  StockTakeHistoryOut,
} from '../types/api'

export const reportsApi = {
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

/**
 * Excel/PDF exports return a binary file with a real filename in
 * Content-Disposition, not JSON -- the generic `api` client (which
 * always parses JSON) doesn't fit here. This does its own fetch,
 * reuses the same in-memory access token, and triggers a normal
 * browser download via a throwaway object URL.
 */
export async function downloadReportExport(
  path: string,
  query: Record<string, string | number>,
  format: 'excel' | 'pdf',
): Promise<void> {
  const url = new URL(`/api/v1${path}`, window.location.origin)
  for (const [key, value] of Object.entries(query)) {
    url.searchParams.set(key, String(value))
  }
  url.searchParams.set('export', format)

  const headers: Record<string, string> = {}
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(url.pathname + url.search, { headers, credentials: 'include' })
  if (!res.ok) {
    let message = res.statusText
    try {
      const body = (await res.json()) as { detail?: string }
      if (body.detail) message = body.detail
    } catch {
      // response wasn't JSON -- keep the statusText fallback
    }
    throw new ApiError(res.status, message, null)
  }

  const disposition = res.headers.get('Content-Disposition') ?? ''
  const filenameMatch = /filename="?([^"]+)"?/.exec(disposition)
  const filename = filenameMatch?.[1] ?? `report.${format === 'excel' ? 'xlsx' : 'pdf'}`

  const blob = await res.blob()
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(objectUrl)
}
