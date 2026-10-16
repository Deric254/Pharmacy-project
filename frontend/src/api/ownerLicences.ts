import { api, downloadFile } from './client'

export type LedgerStatus = 'active' | 'expiring' | 'expired'

export interface LedgerCustomer {
  id: string
  name: string
  code: string
  phone: string
  notes: string
  monthly_fee: number | null
  created: string
  last_issued: string | null
  expires: string | null
  days_left: number | null
  status: LedgerStatus
  licence_count: number
  latest_licence: string | null
  months: { month: string; paid: boolean }[]
  paid_count: number
  unpaid_months: string[]
  amount_outstanding: number | null
}

export interface LedgerOut {
  summary: {
    total_customers: number
    active: number
    expiring: number
    expired: number
    unpaid_months: number
    amount_outstanding: number
  }
  customers: LedgerCustomer[]
  current_month: string
}

export interface IssueRequest {
  owner_key: string
  code: string
  user: string
  days: number
  paid_this_month: boolean
  phone?: string
  notes?: string
  monthly_fee?: number
}

export interface IssueResult {
  licence: string
  user: string
  code: string
  expires: string
  renewal: boolean
  customer: LedgerCustomer
}

export const ownerLicencesApi = {
  list: () => api.get<LedgerOut>('/owner/licences'),
  issue: (body: IssueRequest) => api.post<IssueResult>('/owner/licences/issue', body),
  setPayment: (id: string, month: string, paid: boolean) =>
    api.put<LedgerCustomer>(`/owner/licences/${id}/payments`, { month, paid }),
  remove: (id: string) => api.delete<void>(`/owner/licences/${id}`),
  exportCsv: () => downloadFile('/owner/licences/export.csv', 'licence-ledger.csv'),
}
