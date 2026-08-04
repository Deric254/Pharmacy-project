import { useState, type FormEvent } from 'react'
import { aiApi } from '../api/ai'
import { ApiError } from '../api/client'
import { MarkdownLite } from './MarkdownLite'
import { useViewedRangeStore } from '../lib/viewedRangeStore'
import type { AIConversationOut } from '../types/api'

interface Turn {
  prompt: string
  answer: string
  providerUsed: string | null
}

export function FloatingAiWidget() {
  const [open, setOpen] = useState(false)
  const [prompt, setPrompt] = useState('')
  const [asking, setAsking] = useState(false)
  const [conversation, setConversation] = useState<Turn[]>([])
  const [conversationId, setConversationId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<AIConversationOut[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)

  const viewedRange = useViewedRangeStore((s) => s.range)

  async function handleAsk(e: FormEvent) {
    e.preventDefault()
    if (!prompt.trim() || asking) return
    setAsking(true)
    setError(null)
    const askedPrompt = prompt
    setPrompt('')
    try {
      const context = viewedRange
        ? { viewing_start_date: viewedRange.start, viewing_end_date: viewedRange.end }
        : undefined
      const response = await aiApi.ask(askedPrompt, conversationId, context)
      setConversationId(response.conversation_id)
      setConversation((prev) => [
        ...prev,
        { prompt: askedPrompt, answer: response.answer, providerUsed: response.provider_used },
      ])
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reach the assistant.')
    } finally {
      setAsking(false)
    }
  }

  function startNewChat() {
    setConversationId(null)
    setConversation([])
    setError(null)
    setHistoryOpen(false)
  }

  async function toggleHistory() {
    const next = !historyOpen
    setHistoryOpen(next)
    if (!next) return
    setHistoryLoading(true)
    setHistoryError(null)
    try {
      setHistory(await aiApi.listConversations())
    } catch (err) {
      setHistoryError(err instanceof ApiError ? err.message : 'Could not load past chats.')
    } finally {
      setHistoryLoading(false)
    }
  }

  async function openConversation(id: number) {
    setHistoryError(null)
    try {
      const detail = await aiApi.getConversation(id)
      setConversation(
        detail.messages.map((m) => ({
          prompt: m.prompt,
          answer: m.answer,
          providerUsed: m.provider_used,
        })),
      )
      setConversationId(detail.id)
      setError(null)
      setHistoryOpen(false)
    } catch (err) {
      setHistoryError(err instanceof ApiError ? err.message : 'Could not load that chat.')
    }
  }

  async function handleDelete(id: number, title: string) {
    if (!window.confirm(`Delete "${title}"? This cannot be undone.`)) return
    try {
      await aiApi.deleteConversation(id)
      setHistory((prev) => prev.filter((c) => c.id !== id))
      if (id === conversationId) startNewChat()
    } catch (err) {
      setHistoryError(err instanceof ApiError ? err.message : 'Could not delete that chat.')
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        aria-label="Ask the assistant"
        className="fixed bottom-5 right-5 flex h-12 w-12 items-center justify-center rounded-full border border-ink bg-ink text-paper shadow-lg hover:bg-brass"
      >
        <span className="font-display text-lg">AI</span>
      </button>
    )
  }

  return (
    <div className="fixed bottom-5 right-5 z-50 flex max-h-[70vh] w-96 flex-col border border-rule bg-paper shadow-xl">
      <div className="flex items-center justify-between border-b border-rule bg-ink px-3 py-2">
        <span className="text-sm font-medium text-paper">Assistant</span>
        <div className="flex items-center gap-3">
          <button
            onClick={() => void toggleHistory()}
            className="text-xs text-paper underline decoration-dotted underline-offset-2 hover:text-brass"
          >
            History
          </button>
          <button
            onClick={startNewChat}
            className="text-xs text-paper underline decoration-dotted underline-offset-2 hover:text-brass"
          >
            New chat
          </button>
          <button onClick={() => setOpen(false)} aria-label="Close" className="text-paper">
            ×
          </button>
        </div>
      </div>

      {historyOpen && (
        <div className="max-h-48 overflow-y-auto border-b border-rule bg-paper">
          {historyLoading && <p className="p-3 text-sm text-ink-soft">Loading…</p>}
          {historyError && <p className="p-3 text-sm text-stamp-red">{historyError}</p>}
          {!historyLoading && !historyError && history.length === 0 && (
            <p className="p-3 text-sm text-ink-soft">No past chats yet.</p>
          )}
          {history.map((c) => (
            <div
              key={c.id}
              className={`flex items-center justify-between gap-2 border-b border-rule px-3 py-2 hover:bg-rule/20 ${
                c.id === conversationId ? 'bg-rule/30' : ''
              }`}
            >
              <button
                onClick={() => void openConversation(c.id)}
                className="min-w-0 flex-1 truncate text-left text-sm text-ink"
                title={c.title}
              >
                {c.title}
              </button>
              <button
                onClick={() => void handleDelete(c.id, c.title)}
                aria-label={`Delete ${c.title}`}
                className="text-xs text-stamp-red hover:underline"
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {conversation.length === 0 && (
          <p className="text-sm text-ink-soft">
            Ask anything about your business — today's sales, what's low on stock, how a
            product's margin looks. Answers are grounded in your real data.
          </p>
        )}
        {conversation.map((turn, i) => (
          <div key={i} className="space-y-1">
            <p className="text-sm font-medium text-ink">{turn.prompt}</p>
            <div className="border-l-2 border-brass pl-2 text-sm text-ink-soft">
              <MarkdownLite text={turn.answer} />
            </div>
          </div>
        ))}
        {error && <p className="text-sm text-stamp-red">{error}</p>}
      </div>

      <form onSubmit={(e) => void handleAsk(e)} className="flex gap-2 border-t border-rule p-2">
        <input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ask something…"
          className="flex-1 border border-rule bg-paper px-2 py-1.5 text-sm"
        />
        <button
          type="submit"
          disabled={asking}
          className="border border-ink bg-ink px-3 py-1.5 text-sm text-paper disabled:opacity-50"
        >
          {asking ? '…' : 'Ask'}
        </button>
      </form>
    </div>
  )
}
