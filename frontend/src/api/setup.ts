import { api } from './client'
import type { SetupStatusOut, FirstUserCreate } from '../types/api'

export const setupApi = {
  status: () => api.get<SetupStatusOut>('/setup/status'),
  createFirstUser: (payload: FirstUserCreate) => api.post<void>('/setup/first-user', payload),
}
