import { api, uploadFile } from './client'
import type { SetupStatusOut, FirstUserCreate, RestoreResult } from '../types/api'

export const setupApi = {
  status: (timeoutMs?: number) => api.get<SetupStatusOut>('/setup/status', undefined, timeoutMs),
  createFirstUser: (payload: FirstUserCreate) => api.post<void>('/setup/first-user', payload),
  restoreFromFile: (file: File, passphrase: string) =>
    uploadFile<RestoreResult>('/setup/restore-from-file', file, { passphrase }),
}
