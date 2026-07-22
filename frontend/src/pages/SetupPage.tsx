import { useState, type FormEvent } from 'react'
import { setupApi } from '../api/setup'
import { useConfigStore } from '../config/store'
import { ApiError } from '../api/client'
import { Logo } from '../components/Logo'

export function SetupPage({ onComplete }: { onComplete: () => void }) {
  const config = useConfigStore((s) => s.config)
  const [fullName, setFullName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    if (password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    if (password !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }

    setSubmitting(true)
    try {
      await setupApi.createFirstUser({ full_name: fullName, username, password })
      onComplete()
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Setup already completed -- most likely this page was left
        // open in a second tab/window after someone else finished it.
        onComplete()
      } else {
        setError(
          err instanceof ApiError
            ? err.message
            : 'Could not reach the server. Check your connection and try again.',
        )
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-paper px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <Logo className="mx-auto mb-3 h-12 w-12" />
          <h1 className="font-display text-2xl text-ink">
            {config?.business_name ?? 'Pharmacy System'}
          </h1>
          <p className="mt-1 text-sm text-ink-soft">
            Welcome. Let's set up the owner account -- this only happens once.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="ledger-panel space-y-4 p-6">
          <div>
            <label
              htmlFor="full_name"
              className="block text-xs uppercase tracking-wide text-ink-soft"
            >
              Full name
            </label>
            <input
              id="full_name"
              autoComplete="name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              required
              placeholder="e.g. Jane Doe"
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-ink outline-none focus-visible:border-brass"
            />
          </div>
          <div>
            <label
              htmlFor="username"
              className="block text-xs uppercase tracking-wide text-ink-soft"
            >
              Username
            </label>
            <input
              id="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              minLength={3}
              placeholder="e.g. jane"
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-ink outline-none focus-visible:border-brass"
            />
          </div>
          <div>
            <label
              htmlFor="password"
              className="block text-xs uppercase tracking-wide text-ink-soft"
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-ink outline-none focus-visible:border-brass"
            />
          </div>
          <div>
            <label
              htmlFor="confirm_password"
              className="block text-xs uppercase tracking-wide text-ink-soft"
            >
              Confirm password
            </label>
            <input
              id="confirm_password"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              minLength={8}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-ink outline-none focus-visible:border-brass"
            />
          </div>

          {error && (
            <p
              role="alert"
              className="border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full border border-ink bg-ink py-2 font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? 'Creating account…' : 'Create owner account'}
          </button>
        </form>
      </div>
    </div>
  )
}
