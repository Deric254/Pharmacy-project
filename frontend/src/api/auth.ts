import { api } from './client'
import type { TokenResponse, UserOut } from '../types/api'

export const authApi = {
  login: (username: string, password: string) =>
    api.post<TokenResponse>('/auth/login', { username, password }),
  refresh: () => api.post<TokenResponse>('/auth/refresh'),
  logout: () => api.post<void>('/auth/logout'),
  me: () => api.get<UserOut>('/auth/me'),
}
