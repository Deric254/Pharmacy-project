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
  // conversationId omitted (or null) starts a fresh thread server-side;
  // the returned conversation_id should be remembered and passed on
  // subsequent asks to keep appending to that same thread.
  // 90s, not the default -- a real answer legitimately touches real
  // business data, then a real LLM provider, and if more than one
  // provider is configured, a slow or down first choice is retried
  // against the next rather than failing fast (see
  // ai_assistant_service.py's own per-provider timeout). Cutting this
  // off at the default would turn a slow-but-working answer into a
  // false failure.
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
