import { useState } from 'react'
import type { LicenceState } from '../api/licence'
import { LicenceForm } from './LicenceForm'
import { Modal } from './Modal'

export const RENEWAL_WARNING_DAYS = 14

/**
 * A small corner notice when a licence is close to expiring. Fixed-position so it
 * never changes the app's layout. Renewal uses the same paste box as activation.
 */
export function LicenceRenewalNotice({
  state,
  onRenewed,
}: {
  state: LicenceState
  onRenewed: (state: LicenceState) => void
}) {
  const [open, setOpen] = useState(false)

  if (state.kind !== 'licensed' || state.days_left === undefined) return null
  if (state.days_left > RENEWAL_WARNING_DAYS) return null

  const days = state.days_left
  const when = days <= 0 ? 'today' : days === 1 ? 'tomorrow' : `in ${days} days`

  return (
    <>
      <div className="fixed bottom-3 right-3 z-40 flex items-center gap-3 border border-brass bg-panel px-3 py-2 text-sm text-ink">
        <span>Licence expires {when}.</span>
        <button
          onClick={() => setOpen(true)}
          className="border border-rule px-2 py-1 text-xs hover:border-brass"
        >
          Renew
        </button>
      </div>
      {open && (
        <Modal title="Renew licence" onClose={() => setOpen(false)}>
          <LicenceForm
            installId={state.install_id}
            onActivated={(next) => {
              setOpen(false)
              onRenewed(next)
            }}
          />
        </Modal>
      )}
    </>
  )
}
