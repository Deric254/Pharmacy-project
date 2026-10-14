import { useState, type FormEvent } from 'react'
import { licenceApi, type LicenceState } from '../api/licence'
import { ApiError } from '../api/client'
import { LicenceForm } from '../components/LicenceForm'
import { Logo } from '../components/Logo'

/** Shown instead of the whole app while this copy has no valid licence. */
export function LicencePage({
  state,
  onActivated,
}: {
  state: LicenceState
  onActivated: (state: LicenceState) => void
}) {
  const [showOwner, setShowOwner] = useState(false)
  const [ownerKey, setOwnerKey] = useState('')
  const [ownerError, setOwnerError] = useState<string | null>(null)
  const [claiming, setClaiming] = useState(false)

  async function handleClaim(e: FormEvent) {
    e.preventDefault()
    setOwnerError(null)
    setClaiming(true)
    try {
      onActivated(await licenceApi.claimOwner(ownerKey))
    } catch (err) {
      setOwnerError(
        err instanceof ApiError ? err.message : 'Could not reach the server. Try again.',
      )
    } finally {
      setClaiming(false)
    }
  }

  const canActivate = state.kind !== 'unconfigured'

  return (
    <div className="grid min-h-screen place-items-center bg-paper px-4 login-bg">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <Logo className="mx-auto mb-3 h-12 w-12" />
          <h1 className="font-display text-2xl text-ink">Licence required</h1>
          <p className="mt-1 text-sm text-ink-soft">{state.message}</p>
        </div>

        {canActivate && (
          <div className="ledger-panel space-y-4 p-6">
            <LicenceForm installId={state.install_id} onActivated={onActivated} />
            <div className="border-t border-rule pt-3">
              <button
                type="button"
                onClick={() => setShowOwner((v) => !v)}
                className="text-xs text-ink-soft underline decoration-dotted"
              >
                I&apos;m the owner of this system
              </button>
              {showOwner && (
                <form onSubmit={handleClaim} className="mt-3 space-y-3">
                  <label
                    htmlFor="owner-key"
                    className="block text-xs uppercase tracking-wide text-ink-soft"
                  >
                    Owner key
                  </label>
                  <input
                    id="owner-key"
                    type="password"
                    autoComplete="off"
                    value={ownerKey}
                    onChange={(e) => setOwnerKey(e.target.value)}
                    className="w-full border border-rule bg-paper px-3 py-2 text-sm text-ink outline-none focus-visible:border-brass"
                  />
                  <p className="text-xs text-ink-soft">
                    Used once to prove ownership on this computer. It is never stored.
                  </p>
                  {ownerError && (
                    <p
                      role="alert"
                      className="border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red"
                    >
                      {ownerError}
                    </p>
                  )}
                  <button
                    type="submit"
                    disabled={claiming || ownerKey.trim() === ''}
                    className="w-full border border-rule px-4 py-2 text-sm text-ink hover:border-brass disabled:opacity-50"
                  >
                    {claiming ? 'Checking…' : 'Use owner key'}
                  </button>
                </form>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
