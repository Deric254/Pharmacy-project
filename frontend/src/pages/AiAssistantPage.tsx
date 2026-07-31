import { useEffect, useState, type FormEvent } from 'react'
import { aiApi } from '../api/ai'
import { ApiError } from '../api/client'
import { Modal } from '../components/Modal'
import type { AIProviderKeyOut, AIProviderName } from '../types/api'

const PROVIDERS: AIProviderName[] = ['CLAUDE', 'OPENAI', 'GEMINI', 'DEEPSEEK', 'NVIDIA']

export function AiAssistantPage() {
  const [keys, setKeys] = useState<AIProviderKeyOut[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showAddKey, setShowAddKey] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    aiApi
      .listKeys()
      .then((list) => {
        if (cancelled) return
        setKeys(list)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load provider keys.')
      })
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  async function handleDeleteKey(id: number) {
    try {
      await aiApi.deleteKey(id)
      setReloadKey((k) => k + 1)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove that key.')
    }
  }

  return (
    <div className="p-6">
      <h1 className="mb-1 font-display text-2xl text-ink">AI Settings</h1>
      <p className="mb-6 text-sm text-ink-soft">
        Bring your own API key from any supported provider. Keys are encrypted at rest and never
        shown again after you save them — only a masked preview. If your first-priority provider
        is unavailable, the next one by priority is tried automatically. Once a key is added, the
        assistant is available from the icon in the corner of every page.
      </p>

      {error && (
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      <section className="ledger-panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-xs uppercase tracking-wide text-ink-soft">Provider keys</h2>
          <button
            onClick={() => setShowAddKey(true)}
            className="border border-rule px-2 py-1 text-xs hover:border-brass"
          >
            Add key
          </button>
        </div>
        <ul className="divide-y divide-rule">
          {keys.map((k) => (
            <li key={k.id} className="flex items-center justify-between py-2 text-sm">
              <span>
                {k.provider} <span className="figure text-ink-soft">{k.masked_key}</span>{' '}
                <span className="text-xs text-ink-soft">priority {k.priority}</span>
                {!k.is_active && (
                  <span className="ml-2 text-xs uppercase text-stamp-red">inactive</span>
                )}
              </span>
              <button
                onClick={() => void handleDeleteKey(k.id)}
                className="text-xs text-stamp-red underline decoration-dotted"
              >
                Remove
              </button>
            </li>
          ))}
          {keys.length === 0 && (
            <li className="py-3 text-sm text-ink-soft">
              No provider keys configured yet. Add one to start using the assistant.
            </li>
          )}
        </ul>
      </section>

      {showAddKey && (
        <AddKeyModal
          onClose={() => setShowAddKey(false)}
          onAdded={() => {
            setShowAddKey(false)
            setReloadKey((k) => k + 1)
          }}
        />
      )}
    </div>
  )
}

function AddKeyModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [provider, setProvider] = useState<AIProviderName>('CLAUDE')
  const [apiKey, setApiKey] = useState('')
  const [priority, setPriority] = useState(1)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await aiApi.addKey({ provider, api_key: apiKey, priority })
      onAdded()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save this key.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title="Add a provider key" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">Provider</span>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value as AIProviderName)}
            className="mt-1 w-full border border-rule bg-paper px-3 py-2"
          >
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">API key</span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            required
            className="mt-1 w-full border border-rule bg-paper px-3 py-2"
          />
        </label>
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">
            Priority (lower tries first)
          </span>
          <input
            type="number"
            min={1}
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value))}
            className="figure mt-1 w-full border border-rule bg-paper px-3 py-2"
          />
        </label>

        {error && <p className="text-sm text-stamp-red">{error}</p>}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="border border-rule px-4 py-2 text-sm">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50"
          >
            {submitting ? 'Saving…' : 'Save key'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
