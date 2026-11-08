import { useEffect, useState } from 'react'
import type { LicenceState } from '../api/licence'
import { LICENCE_READ_ONLY_EVENT } from '../api/client'
import { LicenceForm } from './LicenceForm'
import { Modal } from './Modal'

export const RENEWAL_WARNING_DAYS = 14
const REFUSAL_SHOWN_MS = 8000

function daysText(days: number): string {
  return days <= 0 ? 'today' : days === 1 ? '1 day' : `${days} days`
}

/** What the corner notice says for this licence, or null when there is nothing to warn about. */
function noticeText(state: LicenceState): string | null {
  if (state.kind === 'expired' && state.read_only) {
    return 'Licence expired. View-only: you can read and export your data.'
  }
  if (state.kind !== 'licensed') return null
  if (state.grace) {
    return `Licence expired. Renew within ${daysText(state.grace_days_left ?? 0)} to keep full access.`
  }
  if (state.lifetime || state.days_left === undefined || state.days_left > RENEWAL_WARNING_DAYS) {
    return null
  }
  const days = state.days_left
  return `Licence expires ${days <= 0 ? 'today' : days === 1 ? 'tomorrow' : `in ${days} days`}.`
}

/**
 * A small corner notice for a licence that is close to expiring, in its grace period or
 * view-only. Fixed-position so it never changes the app's layout. When the server refuses a
 * change because the licence ran out, the notice shows the server's message for a few seconds
 * (one message, however many actions were refused). Renewal uses the same pay buttons and
 * paste box as activation.
 */
export function LicenceRenewalNotice({
  state,
  onRenewed,
}: {
  state: LicenceState
  onRenewed: (state: LicenceState) => void
}) {
  const [open, setOpen] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    const onRefused = (e: Event) => {
      setRefusal((e as CustomEvent<string>).detail || 'Your licence has expired, so this is view-only.')
      clearTimeout(timer)
      timer = setTimeout(() => setRefusal(null), REFUSAL_SHOWN_MS)
    }
    window.addEventListener(LICENCE_READ_ONLY_EVENT, onRefused)
    return () => {
      window.removeEventListener(LICENCE_READ_ONLY_EVENT, onRefused)
      clearTimeout(timer)
    }
  }, [])

  const text = refusal ?? noticeText(state)
  if (text === null) return null

  return (
    <>
      <div
        role="status"
        className="fixed bottom-3 right-3 z-40 flex max-w-md items-center gap-3 border border-brass bg-panel px-3 py-2 text-sm text-ink"
      >
        <span>{text}</span>
        <button
          onClick={() => setOpen(true)}
          className="shrink-0 border border-rule px-2 py-1 text-xs hover:border-brass"
        >
          Renew
        </button>
      </div>
      {open && (
        <Modal title="Renew licence" onClose={() => setOpen(false)}>
          <LicenceForm
            installId={state.install_id}
            renewUrl={state.renew_url}
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
