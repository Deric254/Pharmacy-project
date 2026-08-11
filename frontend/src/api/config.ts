import { api } from './client'
import type { BusinessConfigOut, BusinessConfigUpdate } from '../types/config'

export const configApi = {
  get: (timeoutMs?: number) => api.get<BusinessConfigOut>('/config', undefined, timeoutMs),
  update: (payload: BusinessConfigUpdate) => api.patch<BusinessConfigOut>('/config', payload),
}
