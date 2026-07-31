import { Component, type ErrorInfo, type ReactNode } from 'react'
import { buildErrorReportMailto } from '../lib/errorReport'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Logged locally for anyone with dev tools open; never sent
    // anywhere automatically -- the person chooses whether to report
    // it, and only their own email client does the sending.
    console.error('Unhandled error caught by ErrorBoundary:', error, info.componentStack)
  }

  handleReload = () => {
    window.location.reload()
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    const mailtoUrl = buildErrorReportMailto({
      message: error.message,
      stack: error.stack,
      pageUrl: window.location.href,
    })

    return (
      <div className="grid min-h-screen place-items-center bg-paper px-4">
        <div className="ledger-panel w-full max-w-md p-6 text-center">
          <h1 className="font-display text-xl text-ink">Something went wrong</h1>
          <p className="mt-2 text-sm text-ink-soft">
            This shouldn't happen. Your data is safe — this is just this screen having a problem
            displaying it. Reloading usually fixes it.
          </p>
          <p className="mt-3 border border-stamp-red-soft bg-stamp-red-soft/30 px-3 py-2 text-left font-mono text-xs text-stamp-red">
            {error.message}
          </p>
          <div className="mt-4 flex flex-col gap-2">
            <button
              onClick={this.handleReload}
              className="border border-ink bg-ink py-2 text-sm font-medium text-paper"
            >
              Reload
            </button>
            <a
              href={mailtoUrl}
              className="border border-rule py-2 text-sm text-ink-soft hover:border-brass"
            >
              Email this error to support
            </a>
          </div>
        </div>
      </div>
    )
  }
}
