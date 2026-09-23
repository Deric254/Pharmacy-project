import { api } from './client'
import type {
  AIAskResponse,
  AIConversationDetailOut,
  AIConversationOut,
  AIProviderKeyCreate,
  AIProviderKeyOut,
} from '../types/api'

export const aiApi = {
  listKeys: () => api.get<AIProviderKeyOut[]>('/ai/keys'),
  addKey: (payload: AIProviderKeyCreate) => api.post<AIProviderKeyOut>('/ai/keys', payload),
  deleteKey: (id: number) => api.delete<void>(`/ai/keys/${id}`),
  ask: (
    prompt: string,
    conversationId?: number | null,
    context?: Record<string, string | number | boolean | null>,
  ) =>
    api.post<AIAskResponse>(
      '/ai/ask',
      { prompt, context, conversation_id: conversationId ?? null },
      90_000,
    ),
  listConversations: () => api.get<AIConversationOut[]>('/ai/conversations'),
  getConversation: (id: number) => api.get<AIConversationDetailOut>(`/ai/conversations/${id}`),
  deleteConversation: (id: number) => api.delete<void>(`/ai/conversations/${id}`),
}
