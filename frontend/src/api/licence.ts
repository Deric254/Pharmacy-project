import { api } from './client'

export interface LicenceState {
  ok: boolean
  kind: 'owner' | 'licensed' | 'expired' | 'unlicensed' | 'unconfigured'
  message: string
  install_id?: string
  renew_url?: string
  expires?: string
  days_left?: number
  /** Just ended: everything still works while the person is asked to renew. */
  grace?: boolean
  grace_days_left?: number
  /** Past the grace period: reading, exporting and signing in work, changes are refused. */
  read_only?: boolean
  lifetime?: boolean
  user?: string
  features?: string[]
}

export const licenceApi = {
  state: (timeoutMs?: number) => api.get<LicenceState>('/licence/state', undefined, timeoutMs),
  activate: (licence: string) => api.post<LicenceState>('/licence/activate', { licence }),
  claimOwner: (ownerKey: string) =>
    api.post<LicenceState>('/licence/claim-owner', { owner_key: ownerKey }),
}
