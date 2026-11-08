import { useState, type FormEvent } from 'react'
import { licenceApi, type LicenceState } from '../api/licence'
import { ApiError } from '../api/client'
import { PayWindow } from './PayWindow'
import { PAY, openExternal, type PlanCode } from '../lib/payment'

function errorText(err: unknown): string {
  return err instanceof ApiError
    ? err.message
    : 'Could not reach the server. Check that the app is running and try again.'
}

/** Pay buttons and the paste-the-licence box, shared by the activation screen and the renewal dialog. */
export function LicenceForm({
  installId,
  renewUrl,
  onActivated,
}: {
  installId: string | undefined
  renewUrl?: string
  onActivated: (state: LicenceState) => void
}) {
  const [plan, setPlan] = useState<PlanCode | null>(null)
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
    <>
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <p className="text-xs uppercase tracking-wide text-ink-soft">Buy or renew</p>
        <div className="mt-1 flex gap-2">
          {(Object.keys(PAY.plans) as PlanCode[]).map((code) => (
            <button
              key={code}
              type="button"
              disabled={!installId}
              onClick={() => setPlan(code)}
              className="flex-1 border border-rule bg-panel px-2 py-2 text-sm text-ink hover:border-brass disabled:opacity-50"
            >
              <b>{code === 'M' ? `$${PAY.plans.M.amount} / month` : `$${PAY.plans.L.amount} for life`}</b>
              <span className="block text-xs text-ink-soft">Pay with PayPal</span>
            </button>
          ))}
        </div>
        <p className="mt-1 text-xs text-ink-soft">
          Pay, email us the proof, and we send your licence back. Paste it below.
          {renewUrl && (
            <>
              {' '}
              <button
                type="button"
                onClick={() => openExternal(renewUrl)}
                className="underline decoration-dotted"
              >
                Or pay on the website
              </button>
            </>
          )}
        </p>
      </div>
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
    {plan && installId && <PayWindow installId={installId} plan={plan} onClose={() => setPlan(null)} />}
    </>
  )
}
