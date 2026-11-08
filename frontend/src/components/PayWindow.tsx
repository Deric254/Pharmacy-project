import { useEffect, useState } from 'react'
import { Modal } from './Modal'
import {
  PAY,
  openExternal,
  payReference,
  proofEmail,
  proofSentKey,
  type PlanCode,
} from '../lib/payment'

function readSent(reference: string): number | null {
  try {
    const value = localStorage.getItem(proofSentKey(reference))
    return value ? Number(value) : null
  } catch {
    return null
  }
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="mt-3">
      <p className="text-xs uppercase tracking-wide text-ink-soft">{label}</p>
      <div className="mt-1 flex items-center justify-between gap-2 border border-rule bg-panel px-3 py-2">
        <b className="break-all text-sm text-ink">{value}</b>
        <button
          type="button"
          onClick={() => void copy()}
          className="shrink-0 border border-rule px-2 py-1 text-xs text-ink hover:border-brass"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
    </div>
  )
}

/** The one window for paying a licence: PayPal to the owner, then an email carrying the proof. */
export function PayWindow({
  installId,
  plan,
  onClose,
}: {
  installId: string
  plan: PlanCode
  onClose: () => void
}) {
  const { name, amount } = PAY.plans[plan]
  const reference = payReference(installId, plan)
  const mail = proofEmail(installId, plan)
  const [sentOn] = useState<number | null>(() => readSent(reference))
  const [payAgain, setPayAgain] = useState(false)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  function sendProof(url: string) {
    openExternal(url)
    try {
      localStorage.setItem(proofSentKey(reference), String(Date.now()))
    } catch {
      /* remembering is a convenience only */
    }
  }

  return (
    <Modal title="Pay with PayPal" onClose={onClose}>
      <p className="-mt-2 mb-3 text-sm text-ink-soft">
        {PAY.app} licence, {name}
      </p>
      {sentOn !== null && !payAgain ? (
        <div className="space-y-4">
          <p className="border border-brass bg-panel px-3 py-3 text-sm text-ink">
            <b>Proof already sent.</b> You opened the proof email for this payment on{' '}
            {new Date(sentOn).toLocaleDateString()}. We are checking it. Please do not pay again for
            the same thing.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 border border-rule px-3 py-2 text-sm text-ink hover:border-brass"
            >
              Close
            </button>
            <button
              type="button"
              onClick={() => setPayAgain(true)}
              className="flex-1 border border-rule px-3 py-2 text-sm text-ink hover:border-brass"
            >
              I need to pay again
            </button>
          </div>
        </div>
      ) : (
        <div>
          <p className="border border-rule bg-panel px-3 py-2 font-display text-3xl text-stamp-green">
            ${amount} USD
          </p>
          <CopyRow label="Send to (PayPal)" value={PAY.email} />
          <CopyRow label="Put this in the PayPal note" value={reference} />
          <ol className="my-4 list-decimal space-y-1 pl-5 text-sm text-ink-soft">
            <li>
              <b className="text-ink">Open PayPal</b> and send exactly{' '}
              <b className="text-ink">${amount} USD</b> to the email above.
            </li>
            <li>
              <b className="text-ink">Take a screenshot</b> of the PayPal confirmation. It must show
              the amount, the date and the <b className="text-ink">transaction ID</b>.
            </li>
            <li>
              <b className="text-ink">Click &quot;Email my proof&quot;</b>. The email opens already
              written. Attach the screenshot and press Send.
            </li>
          </ol>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => openExternal(PAY.paypalUrl)}
              className="flex-1 bg-[#0070ba] px-3 py-2 text-sm font-medium text-white hover:opacity-90"
            >
              Open PayPal
            </button>
            <button
              type="button"
              onClick={() => sendProof(mail.mailto)}
              className="flex-1 border border-ink bg-ink px-3 py-2 text-sm font-medium text-paper hover:opacity-90"
            >
              Email my proof
            </button>
          </div>
          <p className="mt-3 text-xs text-ink-soft">
            Email app not opening?{' '}
            <button
              type="button"
              onClick={() => sendProof(mail.gmail)}
              className="underline decoration-dotted"
            >
              Write it in Gmail on the web
            </button>
            , or send the screenshot to <b>{PAY.email}</b> with the reference above.
          </p>
        </div>
      )}
    </Modal>
  )
}
