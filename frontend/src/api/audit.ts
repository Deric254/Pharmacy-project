import { api } from './client'
import type { AuditLogPage } from '../types/api'

export interface AuditLogFilters {
  entity_type?: string
  action?: string
  start_date?: string
  end_date?: string
  limit?: number
  offset?: number
}

export const auditLogsApi = {
  list: (filters: AuditLogFilters = {}) =>
    api.get<AuditLogPage>('/audit-logs', filters as Record<string, string | number | undefined>),
}
