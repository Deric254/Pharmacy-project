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
  refreshUser: () => Promise<void>
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

  bootstrap: async () => {
    try {
      const user = await authApi.me()
      set({ user, status: 'authenticated' })
    } catch {
      set({ user: null, status: 'anonymous' })
    }
  },

  refreshUser: async () => {
    const user = await authApi.me()
    set({ user })
  },
}))
