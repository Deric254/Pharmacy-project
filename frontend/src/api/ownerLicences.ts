import { api, downloadFile } from './client'

export type LedgerStatus = 'active' | 'expiring' | 'expired' | 'retired'

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
  retired: boolean
  retired_on: string | null
  /** Active (not retired) computers this customer name has. */
  active_computers: number
  licence_count: number
  latest_licence: string | null
  months: { month: string; paid: boolean }[]
  paid_count: number
  unpaid_months: string[]
  amount_outstanding: number | null
}

export interface LedgerOut {
  summary: {
    /** Computers that still count (retired ones are not users). */
    total_customers: number
    retired: number
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
  /** Required by the server when this new code is for a name that already has a computer. */
  relation?: 'migration' | 'extra'
  /** Which computer a migration replaces (code); optional when there is only one. */
  replaces?: string
}

export interface IssueResult {
  licence: string
  user: string
  code: string
  expires: string
  renewal: boolean
  retired_code: string | null
  customer: LedgerCustomer
}

export const ownerLicencesApi = {
  list: () => api.get<LedgerOut>('/owner/licences'),
  issue: (body: IssueRequest) => api.post<IssueResult>('/owner/licences/issue', body),
  setPayment: (id: string, month: string, paid: boolean) =>
    api.put<LedgerCustomer>(`/owner/licences/${id}/payments`, { month, paid }),
  setRetired: (id: string, retired: boolean) =>
    api.put<LedgerCustomer>(`/owner/licences/${id}/retired`, { retired }),
  remove: (id: string) => api.delete<void>(`/owner/licences/${id}`),
  exportCsv: () => downloadFile('/owner/licences/export.csv', 'licence-ledger.csv'),
}
