import { useState, type FormEvent } from 'react'
import { authApi } from '../api/auth'
import { useAuthStore } from './store'
import { ApiError } from '../api/client'

export function MustChangePasswordPage() {
  const refreshUser = useAuthStore((s) => s.refreshUser)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    if (newPassword.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }

    setSubmitting(true)
    try {
      await authApi.changePassword({ current_password: currentPassword, new_password: newPassword })
      await refreshUser()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Could not reach the server. Check your connection and try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-paper px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <h1 className="font-display text-2xl text-ink">Set a real password</h1>
          <p className="mt-1 text-sm text-ink-soft">
            You're signed in with a temporary password someone gave you. Set one only you know
            before continuing.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="ledger-panel space-y-4 p-6">
          <label className="block">
            <span className="block text-xs uppercase tracking-wide text-ink-soft">
              Temporary password
            </span>
            <input
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
              autoFocus
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-ink outline-none focus-visible:border-brass"
            />
          </label>
          <label className="block">
            <span className="block text-xs uppercase tracking-wide text-ink-soft">
              New password
            </span>
            <input
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              minLength={8}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-ink outline-none focus-visible:border-brass"
            />
          </label>
          <label className="block">
            <span className="block text-xs uppercase tracking-wide text-ink-soft">
              Confirm new password
            </span>
            <input
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              minLength={8}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-ink outline-none focus-visible:border-brass"
            />
          </label>

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
            {submitting ? 'Saving…' : 'Set password and continue'}
          </button>
        </form>
      </div>
    </div>
  )
}
