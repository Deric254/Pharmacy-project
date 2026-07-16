import { create } from 'zustand'
import type { UserOut } from '../types/api'
import { authApi } from '../api/auth'
import { setAccessToken } from '../api/client'

interface AuthState {
  user: UserOut | null
  status: 'loading' | 'authenticated' | 'anonymous'
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  bootstrap: () => Promise<void>
  hasPermission: (code: string) => boolean
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  status: 'loading',

  hasPermission: (code: string) => {
    const { user } = get()
    return user !== null && user.permissions.includes(code)
  },

  login: async (username: string, password: string) => {
    const tokens = await authApi.login(username, password)
    setAccessToken(tokens.access_token)
    const user = await authApi.me()
    set({ user, status: 'authenticated' })
  },

  logout: async () => {
    try {
      await authApi.logout()
    } finally {
      setAccessToken(null)
      set({ user: null, status: 'anonymous' })
    }
  },

  // Called once on app load. There is no access token in memory yet
  // (a hard refresh wipes it by design) -- but if a valid refresh
  // cookie exists, /auth/me will 401 once, apiRequest will silently
  // redeem it, and this resolves into a restored session with no
  // visible login flash for a returning user.
  bootstrap: async () => {
    try {
      const user = await authApi.me()
      set({ user, status: 'authenticated' })
    } catch {
      set({ user: null, status: 'anonymous' })
    }
  },
}))
