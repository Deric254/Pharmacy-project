import { useState } from 'react'
import { buildErrorReportMailto } from '../lib/errorReport'
import { useAuthStore } from '../auth/store'

export function HelpPage() {
  const user = useAuthStore((s) => s.user)
  const [description, setDescription] = useState('')

  const mailtoUrl = buildErrorReportMailto({
    message: description.trim() || 'No description provided',
    pageUrl: window.location.href,
    extra: {
      'Reported by': user ? `${user.full_name} (${user.role_name})` : 'Not signed in',
    },
  })

  return (
    <div className="p-6">
      <h1 className="mb-1 font-display text-2xl text-ink">Help</h1>
      <p className="mb-6 text-sm text-ink-soft">
        Something not working right, or not sure how to do something? Describe it below and send
        it directly — this opens your own email app with the details already filled in, nothing
        is sent from here automatically.
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
          href={mailtoUrl}
          className="mt-4 block w-full border border-ink bg-ink py-2 text-center text-sm font-medium text-paper hover:opacity-90"
        >
          Open email to report this
        </a>

        <p className="mt-3 text-xs text-ink-soft">
          This includes the page you're on and the time automatically, so you don't have to
          remember to mention it.
        </p>
      </div>
    </div>
  )
}
