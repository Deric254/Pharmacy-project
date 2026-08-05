import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../auth/store'
import { useConfigStore } from '../config/store'
import { authApi } from '../api/auth'
import { ApiError } from '../api/client'
import { Logo } from '../components/Logo'
import { Modal } from '../components/Modal'

export function LoginPage() {
  const login = useAuthStore((s) => s.login)
  const status = useAuthStore((s) => s.status)
  const config = useConfigStore((s) => s.config)
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [showForgotPassword, setShowForgotPassword] = useState(false)

  if (status === 'authenticated') {
    const from = (location.state as { from?: string } | null)?.from ?? '/'
    return <Navigate to={from} replace />
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(username, password)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('Wrong username or password.')
      } else if (err instanceof ApiError && err.status === 429) {
        setError('Too many attempts. Wait a few minutes and try again.')
      } else if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError('Could not reach the server. Check your connection and try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="grid min-h-screen place-items-center bg-paper px-4"
      style={{
        // Purely decorative -- an inline data-URI, not an external
        // file, so there's nothing to fail to load and no network
        // dependency for a desktop app that may run offline. Built
        // from the same ledger palette as the rest of the app
        // (paper/rule/brass tokens in index.css), not a new color
        // choice, and scoped to this one page only -- it can't affect
        // any other screen, any data, or any request path.
        backgroundImage:
          'radial-gradient(circle at 50% 0%, var(--color-paper) 0%, var(--color-paper-dim) 70%), ' +
          `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cg fill='none' stroke='%23c9c0a4' stroke-width='1' opacity='0.35'%3E%3Cpath d='M0 60h120M60 0v120'/%3E%3C/g%3E%3C/svg%3E")`,
        backgroundBlendMode: 'normal, multiply',
      }}
    >
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <Logo className="mx-auto mb-3 h-12 w-12" />
          <h1 className="font-display text-2xl text-ink">
            {config?.business_name ?? 'Pharmacy System'}
          </h1>
          <p className="mt-1 text-sm text-ink-soft">{config?.slogan || 'Sign in to open the register'}</p>
        </div>

        <form onSubmit={handleSubmit} className="ledger-panel space-y-4 p-6">
          <div>
            <label htmlFor="username" className="block text-xs uppercase tracking-wide text-ink-soft">
              Username
            </label>
            <input
              id="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-ink outline-none focus-visible:border-brass"
            />
          </div>
          <div>
            <label htmlFor="password" className="block text-xs uppercase tracking-wide text-ink-soft">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-ink outline-none focus-visible:border-brass"
            />
            <button
              type="button"
              onClick={() => setShowForgotPassword(true)}
              className="mt-1 text-xs text-ink-soft underline decoration-dotted"
            >
              Forgot password?
            </button>
          </div>

          {error && (
            <p role="alert" className="border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full border border-ink bg-ink py-2 font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>

      {showForgotPassword && (
        <ForgotPasswordModal onClose={() => setShowForgotPassword(false)} />
      )}
    </div>
  )
}

function ForgotPasswordModal({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState<'username' | 'answer' | 'done'>('username')
  const [username, setUsername] = useState('')
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleUsernameSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const result = await authApi.getSecurityQuestion(username)
      setQuestion(result.question)
      setStep('answer')
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

  async function handleAnswerSubmit(e: FormEvent) {
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
      await authApi.forgotPassword({
        username,
        security_answer: answer,
        new_password: newPassword,
      })
      setStep('done')
    } catch (err) {
      // Deliberately the same generic message regardless of whether
      // the username was wrong, the answer was wrong, or the account
      // never had a question set — never confirm which one it was.
      setError(
        err instanceof ApiError
          ? 'That answer didn\'t match. Ask an owner or administrator to reset your password instead.'
          : 'Could not reach the server. Check your connection and try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  if (step === 'done') {
    return (
      <Modal title="Password reset" onClose={onClose}>
        <p className="text-sm text-ink-soft">
          Your password has been changed. You can sign in with it now.
        </p>
        <div className="mt-4 flex justify-end">
          <button onClick={onClose} className="border border-ink bg-ink px-4 py-2 text-sm text-paper">
            Done
          </button>
        </div>
      </Modal>
    )
  }

  if (step === 'answer') {
    return (
      <Modal title="Answer your security question" onClose={onClose}>
        <form onSubmit={(e) => void handleAnswerSubmit(e)} className="space-y-3">
          <p className="text-sm font-medium text-ink">{question}</p>
          <label className="block">
            <span className="block text-xs uppercase tracking-wide text-ink-soft">Answer</span>
            <input
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              required
              autoFocus
              className="mt-1 w-full border border-rule bg-paper px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="block text-xs uppercase tracking-wide text-ink-soft">
              New password
            </span>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              minLength={8}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="block text-xs uppercase tracking-wide text-ink-soft">
              Confirm new password
            </span>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              minLength={8}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2"
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
              {submitting ? 'Resetting…' : 'Reset password'}
            </button>
          </div>
        </form>
      </Modal>
    )
  }

  return (
    <Modal title="Forgot your password?" onClose={onClose}>
      <form onSubmit={(e) => void handleUsernameSubmit(e)} className="space-y-3">
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">Username</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoFocus
            className="mt-1 w-full border border-rule bg-paper px-3 py-2"
          />
        </label>

        {error && <p className="text-sm text-stamp-red">{error}</p>}

        <p className="text-xs text-ink-soft">
          If you don't remember your security answer either, ask an owner or administrator to
          reset your password instead — they can do this without knowing your password.
        </p>

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="border border-rule px-4 py-2 text-sm">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50"
          >
            {submitting ? 'Checking…' : 'Continue'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
