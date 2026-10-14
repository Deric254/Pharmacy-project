import { api } from './client'

export interface LicenceState {
  ok: boolean
  kind: 'owner' | 'licensed' | 'unlicensed' | 'unconfigured'
  message: string
  install_id?: string
  expires?: string
  days_left?: number
  user?: string
  features?: string[]
}

export const licenceApi = {
  state: (timeoutMs?: number) => api.get<LicenceState>('/licence/state', undefined, timeoutMs),
  activate: (licence: string) => api.post<LicenceState>('/licence/activate', { licence }),
  claimOwner: (ownerKey: string) =>
    api.post<LicenceState>('/licence/claim-owner', { owner_key: ownerKey }),
}
