import { api } from './client'
import type { RoleOut, UserCreate, UserListItemOut } from '../types/api'

export const usersApi = {
  list: () => api.get<UserListItemOut[]>('/users'),
  create: (payload: UserCreate) => api.post<UserListItemOut>('/users', payload),
  deactivate: (id: number) => api.delete<void>(`/users/${id}`),
  listRoles: () => api.get<RoleOut[]>('/users/roles'),
}
