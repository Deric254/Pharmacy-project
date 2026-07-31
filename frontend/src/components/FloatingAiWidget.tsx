import { useState, type FormEvent } from 'react'
import { aiApi } from '../api/ai'
import { ApiError } from '../api/client'
import { MarkdownLite } from './MarkdownLite'

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
  const [error, setError] = useState<string | null>(null)

  async function handleAsk(e: FormEvent) {
    e.preventDefault()
    if (!prompt.trim() || asking) return
    setAsking(true)
    setError(null)
    const askedPrompt = prompt
    setPrompt('')
    try {
      const response = await aiApi.ask(askedPrompt)
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
        <button onClick={() => setOpen(false)} aria-label="Close" className="text-paper">
          ×
        </button>
      </div>

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
