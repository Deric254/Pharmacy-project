import { api } from './client'
import type { BusinessConfigOut, BusinessConfigUpdate } from '../types/config'

export const configApi = {
  get: () => api.get<BusinessConfigOut>('/config'),
  update: (payload: BusinessConfigUpdate) => api.patch<BusinessConfigOut>('/config', payload),
}
