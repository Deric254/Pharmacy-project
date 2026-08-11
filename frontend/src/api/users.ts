import { api } from './client'
import type { RoleOut, UserCreate, UserListItemOut } from '../types/api'

export const usersApi = {
  list: () => api.get<UserListItemOut[]>('/users'),
  create: (payload: UserCreate) => api.post<UserListItemOut>('/users', payload),
  deactivate: (id: number) => api.delete<void>(`/users/${id}`),
  // Deliberately NOT rolesApi.listRoles() (/roles, gated on
  // roles.manage) -- Administrator has users.manage but not
  // roles.manage, and creating a staff account only needs to see role
  // *names* to assign one, not the full permission-editing surface.
  // This hits /users/roles, gated on the same permission as this page.
  listRoles: () => api.get<RoleOut[]>('/users/roles'),
}
