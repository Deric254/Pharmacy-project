/**
 * Paying for a licence: the one place that knows the PayPal account, the plans, the
 * payment reference and the proof-of-payment email. The PayWindow shows it.
 *
 * Links open outside the app only through openExternal(). In the desktop app the main
 * process checks each one against its own short allowlist (PAYMENT_LINK_PREFIXES in
 * electron/urlPolicy.js, kept in step with the links built here).
 */

export const PAY = {
  email: 'dericmarangu@gmail.com',
  paypalUrl: 'https://www.paypal.com/myaccount/transfer/homepage',
  app: 'Pharmacy ERP',
  tag: 'PHA',
  plans: {
    M: { name: 'Monthly (30 days)', amount: 100 },
    L: { name: 'Lifetime', amount: 1000 },
  },
} as const

export type PlanCode = keyof typeof PAY.plans

/** Same app + computer + plan always gives the same reference, so a repeat payment is easy to spot. */
export function payReference(installId: string, plan: PlanCode): string {
  return `LIC-${PAY.tag}-${installId.replace(/[^A-Z0-9]/gi, '').toUpperCase()}-${plan}`
}

export function proofEmail(installId: string, plan: PlanCode): { mailto: string; gmail: string } {
  const { name, amount } = PAY.plans[plan]
  const reference = payReference(installId, plan)
  const subject = `Payment proof - ${PAY.app} licence ${name} - ${reference}`
  const body = [
    'Hello Deric,',
    '',
    'I have paid by PayPal. My proof is attached.',
    '',
    `What I paid for: ${PAY.app} licence, ${name}`,
    `Amount: $${amount} USD`,
    `Reference: ${reference}`,
    `Activation code: ${installId}`,
    'PayPal transaction ID: ',
    'My name: ',
    '',
    '(Please attach your PayPal screenshot before sending.)',
  ].join('\n')
  const to = encodeURIComponent(PAY.email)
  const su = encodeURIComponent(subject)
  const bd = encodeURIComponent(body)
  return {
    mailto: `mailto:${PAY.email}?subject=${su}&body=${bd}`,
    gmail: `https://mail.google.com/mail/?view=cm&fs=1&to=${to}&su=${su}&body=${bd}`,
  }
}

/** Where a person pays on the website instead of in the app. */
export const RENEW_PAGE_PREFIX = 'https://dericbi.vercel.app/renew.html?'

/** Where "proof already sent" is remembered on this computer, per reference. */
export function proofSentKey(reference: string): string {
  return `dericbi.paysent.${reference}`
}

/** Open a payment link in the person's own browser or email app. */
export function openExternal(url: string): void {
  if (window.electronAPI?.openLink) void window.electronAPI.openLink(url)
  else window.open(url, '_blank', 'noopener')
}
