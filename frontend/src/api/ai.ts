import { api } from './client'
import type { AIAskResponse, AIProviderKeyCreate, AIProviderKeyOut } from '../types/api'

export const aiApi = {
  listKeys: () => api.get<AIProviderKeyOut[]>('/ai/keys'),
  addKey: (payload: AIProviderKeyCreate) => api.post<AIProviderKeyOut>('/ai/keys', payload),
  deleteKey: (id: number) => api.delete<void>(`/ai/keys/${id}`),
  ask: (prompt: string) => api.post<AIAskResponse>('/ai/ask', { prompt }),
}
