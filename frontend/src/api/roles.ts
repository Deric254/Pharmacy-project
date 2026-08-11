import { api } from './client'
import type { PermissionOut, RoleCreate, RoleDetailOut, RoleUpdate } from '../types/api'

export const rolesApi = {
  listPermissions: () => api.get<PermissionOut[]>('/permissions'),
  listRoles: () => api.get<RoleDetailOut[]>('/roles'),
  get: (id: number) => api.get<RoleDetailOut>(`/roles/${id}`),
  create: (payload: RoleCreate) => api.post<RoleDetailOut>('/roles', payload),
  update: (id: number, payload: RoleUpdate) => api.patch<RoleDetailOut>(`/roles/${id}`, payload),
  delete: (id: number) => api.delete<void>(`/roles/${id}`),
}
