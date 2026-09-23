const SUPPORT_EMAIL = 'dericmarangu@gmail.com'
const MAX_STACK_CHARS = 1500

export interface ErrorReportDetails {
  message: string
  stack?: string
  pageUrl: string
  extra?: Record<string, string>
}

function buildReportText(details: ErrorReportDetails): string {
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
  return lines.join('\n')
}

const REPORT_SUBJECT = 'Pharmacy System - Error Report'

export function buildGmailComposeUrl(details: ErrorReportDetails): string {
  const params = new URLSearchParams({
    view: 'cm',
    fs: '1',
    to: SUPPORT_EMAIL,
    su: REPORT_SUBJECT,
    body: buildReportText(details),
  })
  return `https://mail.google.com/mail/?${params.toString()}`
}

export function buildErrorReportMailto(details: ErrorReportDetails): string {
  const subject = encodeURIComponent(REPORT_SUBJECT)
  const body = encodeURIComponent(buildReportText(details))
  return `mailto:${SUPPORT_EMAIL}?subject=${subject}&body=${body}`
}

export function buildReportPlainText(details: ErrorReportDetails): string {
  return `To: ${SUPPORT_EMAIL}\nSubject: ${REPORT_SUBJECT}\n\n${buildReportText(details)}`
}
