import { create } from 'zustand'
import type { BusinessConfigOut } from '../types/config'
import { configApi } from '../api/config'
import { applyTheme } from '../theme/themes'

interface ConfigState {
  config: BusinessConfigOut | null
  status: 'loading' | 'ready' | 'error'
  load: (timeoutMs?: number) => Promise<void>
  refresh: () => Promise<void>
}

function applyBranding(config: BusinessConfigOut): void {
  applyTheme(config.theme_name)
  document.title = config.business_name

  const favicon = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (favicon && config.logo_url) {
    favicon.href = config.logo_url
  }
}

export const useConfigStore = create<ConfigState>((set) => ({
  config: null,
  status: 'loading',

  load: async (timeoutMs?: number) => {
    try {
      const config = await configApi.get(timeoutMs)
      applyBranding(config)
      set({ config, status: 'ready' })
    } catch {
      // Branding failing to load must never block the app -- fall back
      // to whatever theme is already compiled into index.css (ledger)
      // and a generic title, rather than showing a blank screen.
      set({ config: null, status: 'error' })
    }
  },

  refresh: async () => {
    const config = await configApi.get()
    applyBranding(config)
    set({ config, status: 'ready' })
  },
}))
