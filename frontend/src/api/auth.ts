import { api } from './client'
import type {
  AdminResetPasswordResponse,
  ChangePasswordRequest,
  ForgotPasswordRequest,
  TokenResponse,
  UserOut,
} from '../types/api'

export const authApi = {
  login: (username: string, password: string) =>
    api.post<TokenResponse>('/auth/login', { username, password }),
  refresh: () => api.post<TokenResponse>('/auth/refresh'),
  logout: () => api.post<void>('/auth/logout'),
  me: () => api.get<UserOut>('/auth/me'),
  getSecurityQuestion: (username: string) =>
    api.get<{ question: string }>('/auth/security-question', { username }),
  adminResetPassword: (userId: number) =>
    api.post<AdminResetPasswordResponse>('/auth/admin-reset-password', { user_id: userId }),
  changePassword: (payload: ChangePasswordRequest) =>
    api.post<void>('/auth/change-password', payload),
  forgotPassword: (payload: ForgotPasswordRequest) =>
    api.post<void>('/auth/forgot-password', payload),
  acceptTerms: () => api.post<void>('/auth/accept-terms'),
}
