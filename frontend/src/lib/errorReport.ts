const SUPPORT_EMAIL = 'dericmarangu@gmail.com'
// mailto: URLs have real length limits in some mail clients/OSes --
// keep the body well under that so the link always opens correctly,
// rather than silently failing on a very long stack trace.
const MAX_STACK_CHARS = 1500

export interface ErrorReportDetails {
  message: string
  stack?: string
  pageUrl: string
  extra?: Record<string, string>
}

export function buildErrorReportMailto(details: ErrorReportDetails): string {
  const timestamp = new Date().toISOString()
  const truncatedStack = details.stack ? details.stack.slice(0, MAX_STACK_CHARS) : 'Not available'

  const lines = [
    'An unexpected error occurred in the Pharmacy ERP system.',
    '',
    `Time: ${timestamp}`,
    `Page: ${details.pageUrl}`,
    `Error: ${details.message}`,
    '',
    ...(details.extra
      ? Object.entries(details.extra).map(([key, value]) => `${key}: ${value}`)
      : []),
    '',
    'Details:',
    truncatedStack,
    '',
    '(Feel free to add anything else about what you were doing when this happened.)',
  ]

  const subject = encodeURIComponent('Pharmacy System - Error Report')
  const body = encodeURIComponent(lines.join('\n'))
  return `mailto:${SUPPORT_EMAIL}?subject=${subject}&body=${body}`
}
