import { api } from './client'
import type { BackupLogOut, RestoreResult } from '../types/api'

export const backupsApi = {
  list: () => api.get<BackupLogOut[]>('/backups'),
  run: () => api.post<BackupLogOut>('/backups/run'),
  restore: (backupId: number, confirm: boolean) =>
    api.post<RestoreResult>(`/backups/${backupId}/restore`, { confirm }),
  connectGoogleDrive: (refreshToken: string) =>
    api.post<void>('/backups/connect-google-drive', { refresh_token: refreshToken }),
}
