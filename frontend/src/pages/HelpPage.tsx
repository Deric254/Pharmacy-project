import { useState } from 'react'
import {
  buildErrorReportMailto,
  buildGmailComposeUrl,
  buildReportPlainText,
} from '../lib/errorReport'
import { useAuthStore } from '../auth/store'

export function HelpPage() {
  const user = useAuthStore((s) => s.user)
  const [description, setDescription] = useState('')
  const [copied, setCopied] = useState(false)

  const details = {
    message: description.trim() || 'No description provided',
    pageUrl: window.location.href,
    extra: {
      'Reported by': user ? `${user.full_name} (${user.role_name})` : 'Not signed in',
    },
  }

  async function handleCopy() {
    await navigator.clipboard.writeText(buildReportPlainText(details))
    setCopied(true)
    setTimeout(() => setCopied(false), 2500)
  }

  return (
    <div className="p-6">
      <h1 className="mb-1 font-display text-2xl text-ink">Help</h1>
      <p className="mb-6 text-sm text-ink-soft">
        Something not working right, or not sure how to do something? Describe it below, then
        send it however's easiest for you.
      </p>

      <div className="ledger-panel max-w-xl p-4">
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">
            What's going on? (optional, but helps)
          </span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={5}
            placeholder="e.g. when I try to receive stock, the page goes blank..."
            className="mt-1 w-full border border-rule bg-paper px-3 py-2 text-sm"
          />
        </label>

        <a
          href={buildGmailComposeUrl(details)}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-4 block w-full border border-ink bg-ink py-2 text-center text-sm font-medium text-paper hover:opacity-90"
        >
          Open Gmail to send this
        </a>

        <div className="mt-2 grid grid-cols-2 gap-2">
          <a
            href={buildErrorReportMailto(details)}
            className="border border-rule py-2 text-center text-sm text-ink-soft hover:border-brass"
          >
            Use my email app
          </a>
          <button
            onClick={() => void handleCopy()}
            className="border border-rule py-2 text-center text-sm text-ink-soft hover:border-brass"
          >
            {copied ? 'Copied!' : 'Copy to share elsewhere'}
          </button>
        </div>

        <p className="mt-3 text-xs text-ink-soft">
          "Copy to share elsewhere" puts the whole report on your clipboard, ready to paste into
          WhatsApp, SMS, or wherever's easiest — this includes the page you're on and the time
          automatically, so you don't have to remember to mention it.
        </p>
      </div>
    </div>
  )
}
