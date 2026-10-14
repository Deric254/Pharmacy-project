import { useState, type FormEvent } from 'react'
import { licenceApi, type LicenceState } from '../api/licence'
import { ApiError } from '../api/client'

function errorText(err: unknown): string {
  return err instanceof ApiError
    ? err.message
    : 'Could not reach the server. Check that the app is running and try again.'
}

/** Paste-the-licence box shared by the activation screen and the renewal dialog. */
export function LicenceForm({
  installId,
  onActivated,
}: {
  installId: string | undefined
  onActivated: (state: LicenceState) => void
}) {
  const [licence, setLicence] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [copied, setCopied] = useState(false)

  async function copyCode() {
    if (!installId) return
    try {
      await navigator.clipboard.writeText(installId)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      onActivated(await licenceApi.activate(licence))
    } catch (err) {
      setError(errorText(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <p className="text-xs uppercase tracking-wide text-ink-soft">Activation code</p>
        <div className="mt-1 flex items-center gap-2">
          <code className="flex-1 border border-rule bg-panel px-3 py-2 text-center text-lg tracking-widest text-ink">
            {installId ?? '—'}
          </code>
          <button
            type="button"
            onClick={() => void copyCode()}
            className="border border-rule px-3 py-2 text-sm text-ink hover:border-brass"
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        <p className="mt-1 text-xs text-ink-soft">
          Send this code to the owner. They will send back a licence for this computer.
        </p>
      </div>
      <div>
        <label htmlFor="licence" className="block text-xs uppercase tracking-wide text-ink-soft">
          Licence
        </label>
        <textarea
          id="licence"
          value={licence}
          onChange={(e) => setLicence(e.target.value)}
          rows={4}
          className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm text-ink outline-none focus-visible:border-brass"
          placeholder="Paste the whole licence text here"
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
        disabled={submitting || licence.trim() === ''}
        className="w-full border border-ink bg-ink py-2 font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {submitting ? 'Checking…' : 'Activate'}
      </button>
    </form>
  )
}
