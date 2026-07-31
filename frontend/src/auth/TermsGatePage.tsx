import { useState } from 'react'
import { authApi } from '../api/auth'
import { useAuthStore } from './store'
import { ApiError } from '../api/client'

export function TermsGatePage() {
  const refreshUser = useAuthStore((s) => s.refreshUser)
  const [agreed, setAgreed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleAccept() {
    if (!agreed) return
    setSubmitting(true)
    setError(null)
    try {
      await authApi.acceptTerms()
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
    <div className="grid min-h-screen place-items-center bg-paper px-4 py-8">
      <div className="w-full max-w-2xl">
        <div className="mb-4 text-center">
          <h1 className="font-display text-2xl text-ink">Terms of use</h1>
          <p className="mt-1 text-sm text-ink-soft">
            Please read and accept before continuing.
          </p>
        </div>

        <div className="ledger-panel max-h-96 space-y-3 overflow-y-auto p-6 text-sm text-ink-soft">
          <p className="border border-stamp-red-soft bg-stamp-red-soft/30 px-3 py-2 text-xs text-stamp-red">
            This is a general starting template, not legal advice. It has not been reviewed by a
            lawyer for your specific jurisdiction or pharmacy regulations, and should be before
            relying on it.
          </p>

          <p>
            <strong>1. Acceptance.</strong> By using this system, you agree to these terms. If
            you do not agree, do not use the system.
          </p>
          <p>
            <strong>2. Purpose of the software.</strong> This system is a tool to help record and
            manage inventory, sales, purchasing, and related business data. It does not provide
            medical, legal, tax, or regulatory advice, and does not replace your professional
            judgment or your obligations under pharmacy law and health regulations in your
            jurisdiction.
          </p>
          <p>
            <strong>3. Accuracy of data.</strong> The system is built to record exactly what is
            entered and to keep those records internally consistent. It cannot verify that what
            is entered (prices, quantities, expiry dates, patient-facing information, etc.) is
            itself correct — that responsibility remains with the people using it.
          </p>
          <p>
            <strong>4. No warranty.</strong> The system is provided "as is," without warranty of
            any kind, express or implied, including fitness for a particular purpose.
          </p>
          <p>
            <strong>5. Limitation of liability.</strong> To the fullest extent permitted by law,
            the provider of this system is not liable for indirect, incidental, or consequential
            damages arising from its use, including business losses, lost stock, or lost data,
            except where such limitation is not permitted by law.
          </p>
          <p>
            <strong>6. Your data.</strong> The business retains ownership of all data entered
            into the system. Backups are the business's responsibility to run and store securely.
          </p>
          <p>
            <strong>7. Account responsibility.</strong> Each user is responsible for actions
            taken under their own login. Passwords must not be shared.
          </p>
          <p>
            <strong>8. Changes.</strong> These terms may be updated from time to time. Continued
            use after an update means you accept the revised terms.
          </p>
        </div>

        <label className="mt-4 flex items-start gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="mt-1"
          />
          <span>I have read and agree to these terms of use.</span>
        </label>

        {error && (
          <p
            role="alert"
            className="mt-3 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red"
          >
            {error}
          </p>
        )}

        <button
          onClick={() => void handleAccept()}
          disabled={!agreed || submitting}
          className="mt-4 w-full border border-ink bg-ink py-2 font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {submitting ? 'Saving…' : 'Accept and continue'}
        </button>
      </div>
    </div>
  )
}
